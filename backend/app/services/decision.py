"""实时决策动作冲突裁决与执行明细数据集。

策略库（tacticlib）发布策略、实时决策（realtime）生效规则，是同一份运营策略的两个承接方：
发布记录说明「策略从哪来」，规则命中结果说明「这条策略在实时链路里怎么被触发」。改造前，
两边各自按自己的口径读触发条件、各自落执行动作，动作冲突的边界散落在两个 service 里。这里把
口径和边界收拢到一处：

* 触发条件按原口径读取：策略库读 ``触发条件``，实时决策读 ``决策条件``，不做改写、不做推断。
* 生效会话：同一份策略的发布记录与规则命中结果共享同一个会话编号与数据版本。
* 动作裁决：同一触发对象只保留一个最终执行动作。先比优先级；优先级相同且历史未规定先后时，
  按「登记基准」（先登记的会话）裁决——会话开始/完成时间只记录、不参与裁决，避免时间字段
  抖动改变已确认的最终动作。历史已确认的会话在同优先级下优先保留，不改写。
* 执行明细：每个触发对象只有一条执行明细，先落在「待处理」；重复登记收敛为同一条记录，
  已闭环的记录不会被重复登记覆盖。落败会话逐条给出原因，最终动作与冲突原因可一起回放。
* 失败回退：登记/裁决/回写任何一步失败，整体回退到调用前的完整边界，不留半条会话、半条明细。
"""
from __future__ import annotations

import copy
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.store import store

REALTIME_MODULE = "realtime"
BASELINE_RULE_CODE = "REAL-0003"

SESSION_PREFIX = "SESS"
DETAIL_PREFIX = "EXEC"

STATUS_PENDING = "待处理"
STATUS_CLOSED = "已闭环"

BASIS_PRIORITY = "优先级"
BASIS_HISTORY = "历史已确认结果"
BASIS_REGISTRATION = "登记基准（同优先级、历史未规定先后）"
BASIS_SINGLE = "唯一生效会话"
BASIS_SAME_ACTION = "同动作收敛"

PRIORITY_PATTERN = re.compile(r"(-?\d+)")


class DecisionError(Exception):
    """可预期的裁决失败：调用方必须回退到调用前的完整边界。"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_priority(raw: Any) -> tuple[int, str]:
    """按原字段读取优先级：取字段里第一个整数，读不出数字时记为 0。

    存量数据的优先级是样例文本（如 ``实时决策样例3``）。这里不改写原值，只在裁决时给出一个
    稳定的比较口径，保证同优先级与不同优先级都能被解释。
    """
    text = str(raw if raw is not None else "").strip()
    match = PRIORITY_PATTERN.search(text)
    return (int(match.group(1)) if match else 0), text


@dataclass
class Session:
    """一个生效会话：一份策略在某个触发对象上的生效记录。"""

    id: int
    code: str
    trigger_object: str
    priority: int
    priority_raw: str
    tactic_condition: str
    rule_condition: str
    action: str
    source: str
    ref_code: str
    ref_entry_id: int | None
    start_at: str
    finish_at: str
    version: str
    last_version: str
    status: str = "生效"
    loser_reason: str = ""
    baseline: bool = False
    aliases: list[str] = field(default_factory=list)
    publish_records: list[dict[str, Any]] = field(default_factory=list)
    rule_hits: list[dict[str, Any]] = field(default_factory=list)

    @property
    def trigger_condition(self) -> str:
        """按会话来源的原口径给触发条件：策略库读「触发条件」，实时决策读「决策条件」。"""
        if self.source == "策略库":
            return self.tactic_condition or self.rule_condition
        return self.rule_condition or self.tactic_condition

    def codes(self) -> set[str]:
        """会话的全部关联编号：策略编号、规则编号互为别名，两边共享同一个会话。"""
        return {code for code in [self.ref_code, *self.aliases] if code}

    def key(self) -> tuple[str, int, str, str]:
        # 有关联编号（策略编号/规则编号）时，一个关联编号在一个优先级上只算一个会话，
        # 规则动作调整后复用同一会话；没有关联编号的临时登记才按动作区分。
        code = self.ref_code or f"@{self.action}"
        return (self.trigger_object, self.priority, code, "" if self.ref_code else self.action)


@dataclass
class ExecutionDetail:
    """实时决策执行明细：一个触发对象一条最终动作。"""

    id: int
    code: str
    trigger_object: str
    final_action: str
    priority: int
    trigger_condition: str
    tactic_condition: str
    rule_condition: str
    sessions: list[str]
    status: str
    version: str
    baseline: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


class DecisionLedger:
    """动作裁决台账：生效会话、执行明细、数据版本都收在这里。

    台账独立于各业务模块的内存表，不进入 ``/api/overview`` 的模块统计，也不改写列表接口的
    存量记录，因此既有列表/详情/动作响应保持不变。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: list[Session] = []
        self._details: list[ExecutionDetail] = []
        self._versions: list[dict[str, Any]] = []
        self._followups: dict[str, list[dict[str, Any]]] = {}
        self._session_seq = 0
        self._detail_seq = 0
        self._version = 0
        self._seeded = False

    # ------------------------------------------------------------------ 版本
    def _bump(
        self,
        event: str,
        *,
        detail_code: str = "",
        session_code: str = "",
    ) -> str:
        self._version += 1
        code = f"DV-{self._version:04d}"
        self._versions.append({
            "数据版本": code,
            "事件": event,
            "明细编号": detail_code,
            "会话编号": session_code,
            "时间": _now(),
        })
        return code

    def current_version(self) -> str:
        return f"DV-{max(self._version, 1):04d}"

    # ------------------------------------------------------------------ 基准
    def seed_baseline(self) -> None:
        """从存量实时规则派生核对基准（REAL-0003）。

        只读存量记录、不写回：派生出来的执行明细是既有已确认结果的只读投影，用于核对重构前后
        的最终动作是否一致；它不参与在线会话裁决，避免基准数据影响实时动作。
        """
        with self._lock:
            if self._seeded:
                return
            self._seeded = True
            row = next(
                (item for item in store.rows(REALTIME_MODULE)
                 if str(item.get("规则编号")) == BASELINE_RULE_CODE),
                None,
            )
            if row is None:
                return
            version = self._bump("派生核对基准（REAL-0003）")
            session = self._new_session(
                trigger_object=str(row.get("触发事件") or ""),
                priority_raw=row.get("优先级"),
                tactic_condition="",
                rule_condition=str(row.get("决策条件") or ""),
                action=str(row.get("执行动作") or ""),
                source="实时决策",
                ref_code=str(row.get("规则编号") or ""),
                ref_entry_id=int(row.get("id") or 0) or None,
                start_at=str(row.get("生效时段") or ""),
                finish_at=str(row.get("生效时段") or ""),
                version=version,
                baseline=True,
            )
            session.rule_hits.append({
                "规则编号": session.ref_code,
                "命中动作": session.action,
                "触发事件": session.trigger_object,
                "数据版本": version,
                "来源": "存量已确认结果",
            })
            detail = self._new_detail(session.trigger_object, [session], baseline=True)
            detail.status = STATUS_CLOSED
            detail.payload.update({
                "状态": STATUS_CLOSED,
                "pending": False,
                "已确认": True,
                "是否核对基准": True,
                "裁决依据": BASIS_HISTORY,
                "冲突状态": "无冲突",
                "冲突原因": "",
                "闭环说明": "存量已确认结果只读投影，仅用于重构前后核对",
            })
            detail.payload.pop("登记键", None)

    # ------------------------------------------------------------------ 会话
    def _next_session_code(self) -> str:
        self._session_seq += 1
        return f"{SESSION_PREFIX}-{self._session_seq:04d}"

    def _next_detail_code(self) -> str:
        self._detail_seq += 1
        return f"{DETAIL_PREFIX}-{self._detail_seq:04d}"

    def _new_session(
        self,
        *,
        trigger_object: str,
        priority_raw: Any,
        tactic_condition: str,
        rule_condition: str,
        action: str,
        source: str,
        ref_code: str,
        ref_entry_id: int | None,
        start_at: str,
        finish_at: str,
        version: str,
        baseline: bool = False,
        aliases: list[str] | None = None,
    ) -> Session:
        priority, raw = parse_priority(priority_raw)
        session = Session(
            id=len(self._sessions) + 1,
            code=self._next_session_code(),
            trigger_object=trigger_object,
            priority=priority,
            priority_raw=raw,
            tactic_condition=tactic_condition,
            rule_condition=rule_condition,
            action=action,
            source=source,
            ref_code=ref_code,
            ref_entry_id=ref_entry_id,
            start_at=start_at or _now(),
            finish_at=finish_at or _now(),
            version=version,
            last_version=version,
            baseline=baseline,
            aliases=list(aliases or []),
        )
        self._sessions.append(session)
        return session

    def _find_session(
        self,
        trigger_object: str,
        priority: int,
        codes: set[str],
        action: str,
    ) -> Session | None:
        for session in self._sessions:
            if session.baseline or session.trigger_object != trigger_object:
                continue
            if session.priority != priority:
                continue
            if codes:
                if session.codes() & codes:
                    return session
            elif not session.codes() and session.action == action:
                return session
        return None

    def ensure_session(
        self,
        *,
        trigger_object: str,
        priority_raw: Any,
        tactic_condition: str = "",
        rule_condition: str = "",
        action: str,
        source: str,
        ref_code: str,
        ref_entry_id: int | None = None,
        start_at: str = "",
        finish_at: str = "",
        version: str,
        aliases: list[str] | None = None,
    ) -> Session:
        """取得共享生效会话：同对象、同优先级、同关联编号（含别名）只保留一个会话。"""
        priority = parse_priority(priority_raw)[0]
        extra = [code for code in (aliases or []) if code and code != ref_code]
        codes = {code for code in [ref_code, *extra] if code}
        existing = self._find_session(trigger_object, priority, codes, action)
        if existing is not None:
            if ref_code and ref_code != existing.ref_code:
                if ref_code not in existing.aliases:
                    existing.aliases.append(ref_code)
            for code in extra:
                if code not in existing.aliases and code != existing.ref_code:
                    existing.aliases.append(code)
        if existing is not None:
            existing.last_version = version
            if tactic_condition and not existing.tactic_condition:
                existing.tactic_condition = tactic_condition
            if rule_condition and not existing.rule_condition:
                existing.rule_condition = rule_condition
            if existing.ref_code and action:
                # 同一规则再次生效/再次发布时，以最新动作为准，仍是同一个生效会话。
                existing.action = action
            return existing
        return self._new_session(
            trigger_object=trigger_object,
            priority_raw=priority_raw,
            tactic_condition=tactic_condition,
            rule_condition=rule_condition,
            action=action,
            source=source,
            ref_code=ref_code,
            ref_entry_id=ref_entry_id,
            start_at=start_at,
            finish_at=finish_at,
            version=version,
            aliases=extra,
        )

    def _live_sessions(self, trigger_object: str) -> list[Session]:
        return [
            session
            for session in self._sessions
            if session.trigger_object == trigger_object and not session.baseline
        ]

    # ------------------------------------------------------------------ 裁决
    def _confirmed_winner(self, trigger_object: str) -> tuple[str, str] | None:
        detail = self._live_detail(trigger_object)
        if detail is None or detail.status != STATUS_CLOSED:
            return None
        return str(detail.payload.get("生效会话", "")), detail.final_action

    def _arbitrate(self, trigger_object: str, sessions: list[Session] | None = None) -> dict[str, Any]:
        """按优先级 → 历史已确认结果 → 登记基准的顺序裁决，返回唯一最终动作。"""
        if sessions is None:
            sessions = self._live_sessions(trigger_object)
        if not sessions:
            raise DecisionError(f"触发对象「{trigger_object}」没有可裁决的生效会话")
        top_priority = max(session.priority for session in sessions)
        top = [session for session in sessions if session.priority == top_priority]
        history = self._confirmed_winner(trigger_object)

        winner: Session | None = None
        basis = BASIS_SINGLE if len(sessions) == 1 else BASIS_PRIORITY
        if history is not None:
            history_code, history_action = history
            for session in top:
                if session.code == history_code:
                    winner, basis = session, BASIS_HISTORY
                    break
            if winner is None:
                for session in top:
                    if session.action == history_action:
                        winner, basis = session, BASIS_HISTORY
                        break
        if winner is None:
            # 同优先级且历史未规定先后：按登记顺序保留先登记会话。
            # 会话开始/完成时间只做记录，不参与裁决。
            winner = min(top, key=lambda session: session.id)
            if len(top) > 1:
                basis = BASIS_SAME_ACTION if len({s.action for s in top}) == 1 else BASIS_REGISTRATION

        losers: list[dict[str, Any]] = []
        for session in sessions:
            if session is winner:
                session.status = "生效"
                session.loser_reason = ""
                continue
            session.status = "落败"
            if session.priority < top_priority:
                reason = (
                    f"优先级 {session.priority} 低于生效会话 {winner.code} 的 {top_priority}，"
                    f"未取得最终执行动作"
                )
            elif session.action == winner.action:
                reason = f"执行动作与生效会话 {winner.code} 一致，收敛为同一最终动作"
            elif basis == BASIS_HISTORY:
                reason = (
                    f"同优先级，但触发对象「{trigger_object}」已有已确认结果"
                    f"（生效会话 {winner.code}，动作「{winner.action}」），保留已确认结果，不改写历史"
                )
            else:
                reason = (
                    f"同优先级且历史未规定先后，按登记基准保留先登记会话 {winner.code}；"
                    f"本会话登记顺序靠后，会话开始时间 {session.start_at or '未记录'}、"
                    f"完成时间 {session.finish_at or '未记录'} 仅记录，不参与裁决"
                )
            session.loser_reason = reason
            losers.append({
                "会话编号": session.code,
                "执行动作": session.action,
                "优先级": session.priority,
                "来源": session.source,
                "关联编号": session.ref_code,
                "落败原因": reason,
            })

        distinct_actions = {session.action for session in sessions}
        if len(distinct_actions) > 1:
            conflict_status = "冲突已裁决"
            if basis == BASIS_HISTORY:
                edge = "同优先级会话已有历史已确认结果，沿用已确认动作、不改写历史"
            elif basis == BASIS_REGISTRATION:
                edge = f"最高优先级 {top_priority} 下有 {len(top)} 个会话且历史未规定先后"
            else:
                edge = f"最高优先级 {top_priority} 的会话优先，低优先级会话未取得最终动作"
            conflict_reason = (
                f"同一触发对象「{trigger_object}」存在 {len(sessions)} 个生效会话，{edge}；"
                f"按「{basis}」保留会话 {winner.code} 的执行动作「{winner.action}」，"
                f"落败会话 {len(losers)} 个已逐条记录。"
            )
        elif len(sessions) > 1:
            conflict_status = "同动作收敛"
            conflict_reason = (
                f"同一触发对象「{trigger_object}」的 {len(sessions)} 个生效会话动作一致，"
                f"已收敛为单一最终动作「{winner.action}」"
            )
        else:
            conflict_status = "无冲突"
            conflict_reason = ""

        return {
            "触发对象": trigger_object,
            "优先级": top_priority,
            "生效会话": winner,
            "最终执行动作": winner.action,
            "裁决依据": basis,
            "冲突状态": conflict_status,
            "冲突原因": conflict_reason,
            "落败会话": losers,
            "候选会话": sessions,
        }

    # ------------------------------------------------------------------ 明细
    @staticmethod
    def _public(detail: ExecutionDetail) -> dict[str, Any]:
        return copy.deepcopy(detail.payload)

    def _detail_payload(self, detail: ExecutionDetail, verdict: dict[str, Any]) -> dict[str, Any]:
        winner: Session = verdict["生效会话"]
        sessions: list[Session] = verdict["候选会话"]
        return {
            "id": detail.id,
            "明细编号": detail.code,
            "触发对象": detail.trigger_object,
            "触发条件": detail.trigger_condition,
            "优先级": detail.priority,
            "候选动作": [
                {
                    "会话编号": session.code,
                    "执行动作": session.action,
                    "来源": session.source,
                    "关联编号": session.ref_code,
                    "关联编号别名": list(session.aliases),
                    "优先级": session.priority,
                }
                for session in sessions
            ],
            "最终执行动作": detail.final_action,
            "执行动作": detail.final_action,
            "策略触发条件": detail.tactic_condition,
            "规则触发条件": detail.rule_condition,
            "生效会话": winner.code,
            "裁决依据": verdict["裁决依据"],
            "冲突状态": verdict["冲突状态"],
            "冲突原因": verdict["冲突原因"],
            "落败会话": copy.deepcopy(verdict["落败会话"]),
            "会话编号": [session.code for session in sessions],
            "关联编号": sorted({code for session in sessions for code in session.codes()}),
            "发布记录": [record for session in sessions for record in session.publish_records],
            "命中结果": [record for session in sessions for record in session.rule_hits],
            "数据版本": detail.version,
            "会话数据版本": {session.code: session.last_version for session in sessions},
            "状态": detail.status,
            "pending": detail.status == STATUS_PENDING,
            "已确认": detail.status == STATUS_CLOSED,
            "可回放": True,
            "是否核对基准": detail.baseline,
            "会话开始时间": winner.start_at,
            "会话完成时间": winner.finish_at,
            "登记键": f"{detail.trigger_object}#{','.join(sorted(session.code for session in sessions))}",
        }

    def _new_detail(
        self,
        trigger_object: str,
        sessions: list[Session],
        *,
        baseline: bool = False,
    ) -> ExecutionDetail:
        verdict = self._arbitrate(trigger_object, sessions)
        detail = ExecutionDetail(
            id=len(self._details) + 1,
            code=self._next_detail_code(),
            trigger_object=trigger_object,
            final_action=verdict["最终执行动作"],
            priority=verdict["优先级"],
            trigger_condition=verdict["生效会话"].trigger_condition,
            tactic_condition=verdict["生效会话"].tactic_condition,
            rule_condition=verdict["生效会话"].rule_condition,
            sessions=[session.code for session in sessions],
            status=STATUS_PENDING,
            version="",
            baseline=baseline,
        )
        detail.version = self._bump("登记实时决策执行明细", detail_code=detail.code)
        detail.payload = self._detail_payload(detail, verdict)
        self._details.append(detail)
        return detail

    def _refresh_detail(self, detail: ExecutionDetail, verdict: dict[str, Any], sessions: list[Session]) -> bool:
        """重算明细内容；内容有变化时更新同一条记录并推进数据版本。"""
        candidate = self._detail_payload(detail, verdict)
        ignored = {"数据版本", "会话数据版本", "状态", "pending", "已确认", "是否核对基准"}
        changed = {k: v for k, v in candidate.items() if k not in ignored} != {
            k: v for k, v in detail.payload.items() if k not in ignored
        }
        detail.final_action = verdict["最终执行动作"]
        detail.priority = verdict["优先级"]
        detail.trigger_condition = verdict["生效会话"].trigger_condition
        detail.tactic_condition = verdict["生效会话"].tactic_condition
        detail.rule_condition = verdict["生效会话"].rule_condition
        detail.sessions = [session.code for session in sessions]
        if changed:
            detail.version = self._bump(
                "更新实时决策执行明细",
                detail_code=detail.code,
                session_code=verdict["生效会话"].code,
            )
        detail.payload = self._detail_payload(detail, verdict)
        return changed

    def _live_detail(self, trigger_object: str) -> ExecutionDetail | None:
        for detail in reversed(self._details):
            if detail.trigger_object == trigger_object and not detail.baseline:
                return detail
        return None

    def ensure_detail(self, trigger_object: str) -> dict[str, Any]:
        """登记执行明细：每个触发对象只有一条明细，重复登记收敛为同一条记录。"""
        sessions = self._live_sessions(trigger_object)
        if not sessions:
            raise DecisionError(f"触发对象「{trigger_object}」没有可登记的生效会话")
        verdict = self._arbitrate(trigger_object)
        detail = self._live_detail(trigger_object)

        if detail is None:
            return self._public(self._new_detail(trigger_object, sessions))

        if detail.status == STATUS_CLOSED:
            if detail.final_action != verdict["最终执行动作"]:
                raise DecisionError(
                    f"触发对象「{trigger_object}」的执行明细 {detail.code} 已闭环，"
                    f"最终动作「{detail.final_action}」不可被后续登记改写，已回退到原完整边界"
                )
            # 已闭环且最终动作一致：收敛到同一条记录，只追加后续登记记录，不改写已确认内容。
            self._followups.setdefault(detail.code, []).append({
                "数据版本": self._bump(
                    "重复登记收敛",
                    detail_code=detail.code,
                    session_code=verdict["生效会话"].code,
                ),
                "会话编号": [session.code for session in sessions],
                "裁决依据": verdict["裁决依据"],
                "最终执行动作": detail.final_action,
                "冲突状态": verdict["冲突状态"],
                "冲突原因": verdict["冲突原因"],
                "落败会话": copy.deepcopy(verdict["落败会话"]),
                "时间": _now(),
            })
            result = self._public(detail)
            # 已确认内容保持原样，后续登记的落败会话只追加在这里，便于解释而不改写历史。
            result["后续登记"] = copy.deepcopy(self._followups[detail.code])
            return result

        self._refresh_detail(detail, verdict, sessions)
        return self._public(detail)

    def _close(self, detail: ExecutionDetail, *, reason: str) -> None:
        if detail.status == STATUS_CLOSED:
            return
        verdict = self._arbitrate(detail.trigger_object)
        detail.status = STATUS_CLOSED
        detail.version = self._bump(
            "确认实时决策执行明细",
            detail_code=detail.code,
            session_code=verdict["生效会话"].code,
        )
        detail.payload = self._detail_payload(detail, verdict)
        detail.payload.update({
            "状态": STATUS_CLOSED,
            "pending": False,
            "已确认": True,
            "闭环说明": reason,
            "数据版本": detail.version,
        })

    # ------------------------------------------------------------------ 关联与回写
    def resolve_rule(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        """策略库记录到实时规则的关联：显式 ``关联规则`` 优先，否则按策略编号与规则编号对齐。"""
        rows = store.rows(REALTIME_MODULE)
        link = str(entry.get("关联规则") or "").strip()
        if link:
            for row in rows:
                if link in {str(row.get("规则编号")), str(row.get("id"))}:
                    return row
            raise DecisionError(f"关联规则「{link}」不存在，策略发布未生效，已回退到原完整边界")
        code = str(entry.get("策略编号") or "").strip()
        if not code:
            return None
        for row in rows:
            if code == str(row.get("规则编号")):
                return row
        return None

    def _write_back_rule(
        self,
        rule: dict[str, Any],
        *,
        entry: dict[str, Any],
        final_action: str,
        version: str,
    ) -> dict[str, Any]:
        """策略库发布动作回写到实时决策列表：状态与唯一最终动作保持同一份。"""
        before = {
            "status": rule.get("status"),
            "pending": rule.get("pending"),
            "abnormal": rule.get("abnormal"),
            "执行动作": rule.get("执行动作"),
        }
        rule["status"] = "已生效"
        rule["pending"] = False
        rule["abnormal"] = False
        if final_action:
            rule["执行动作"] = final_action
        return {
            "回写对象": str(rule.get("规则编号") or ""),
            "策略编号": str(entry.get("策略编号") or ""),
            "发布动作": "发布策略",
            "回写前": before,
            "回写后": {
                "status": rule.get("status"),
                "pending": rule.get("pending"),
                "abnormal": rule.get("abnormal"),
                "执行动作": rule.get("执行动作"),
            },
            "数据版本": version,
            "时间": _now(),
        }

    @staticmethod
    def _append_once(records: list[dict[str, Any]], record: dict[str, Any], key_fields: tuple[str, ...]) -> dict[str, Any]:
        """同一份发布记录/命中结果只保留一条，重复登记只推进数据版本。"""
        for existing in records:
            if all(existing.get(field) == record.get(field) for field in key_fields):
                existing["数据版本"] = record["数据版本"]
                existing["时间"] = record["时间"]
                return existing
        records.append(record)
        return record

    # ------------------------------------------------------------------ 钩子
    def on_tactic_publish(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        """策略发布：发布记录 + 规则命中结果共享同一生效会话，并生成/收敛执行明细。"""
        with self._lock:
            snapshot = self._snapshot()
            try:
                rule = self.resolve_rule(entry)
                trigger_condition = str(entry.get("触发条件") or "").strip()
                action = str(entry.get("执行动作") or "").strip()
                if rule is not None:
                    trigger_object = str(rule.get("触发事件") or "").strip()
                    if not trigger_object:
                        raise DecisionError("关联规则缺少触发事件，策略发布未生效，已回退到原完整边界")
                    rule_condition = str(rule.get("决策条件") or "").strip()
                    action = action or str(rule.get("执行动作") or "").strip()
                else:
                    if not trigger_condition:
                        return None
                    trigger_object = trigger_condition
                    rule_condition = ""

                version = self._bump("策略库发布策略")
                session = self.ensure_session(
                    trigger_object=trigger_object,
                    priority_raw=rule.get("优先级") if rule is not None else entry.get("优先级"),
                    tactic_condition=trigger_condition,
                    rule_condition=rule_condition,
                    action=action,
                    source="策略库",
                    ref_code=str(entry.get("策略编号") or ""),
                    ref_entry_id=int(entry.get("id") or 0) or None,
                    start_at=str(entry.get("生效周期") or ""),
                    finish_at=str(entry.get("生效周期") or ""),
                    version=version,
                    # 显式关联到规则时，规则编号是同一会话的别名，发布记录与命中结果共用会话。
                    aliases=[str(rule.get("规则编号") or "")] if rule is not None else [],
                )
                publish_record = self._append_once(session.publish_records, {
                    "策略编号": str(entry.get("策略编号") or ""),
                    "策略名称": str(entry.get("策略名称") or ""),
                    "发布动作": "发布策略",
                    "关联规则": str(rule.get("规则编号") or "") if rule is not None else "",
                    "触发条件": trigger_condition,
                    "执行动作": action,
                    "数据版本": version,
                    "回写": None,
                    "时间": _now(),
                }, ("策略编号", "发布动作"))

                verdict = self._arbitrate(trigger_object)
                if rule is not None:
                    writeback_version = self._bump(
                        "策略库发布回写实时决策", session_code=session.code
                    )
                    publish_record["回写"] = self._write_back_rule(
                        rule,
                        entry=entry,
                        final_action=verdict["最终执行动作"],
                        version=writeback_version,
                    )
                hit_version = self._bump("策略库发布同步规则命中", session_code=session.code)
                self._append_once(session.rule_hits, {
                    "规则编号": str(rule.get("规则编号") or "") if rule is not None else "",
                    "命中动作": verdict["最终执行动作"],
                    "触发事件": trigger_object,
                    "数据版本": hit_version,
                    "来源": "发布同步",
                    "时间": _now(),
                }, ("规则编号", "来源", "命中动作"))
                return self.ensure_detail(trigger_object)
            except DecisionError:
                self._restore(snapshot)
                raise
            except Exception as exc:  # pragma: no cover - 兜底回退
                self._restore(snapshot)
                raise DecisionError(f"策略发布裁决失败，已回退到原完整边界：{exc}") from exc

    def on_realtime_activate(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        """实时规则生效：把规则命中结果登记到共享生效会话并生成/收敛执行明细。"""
        with self._lock:
            snapshot = self._snapshot()
            try:
                trigger_object = str(entry.get("触发事件") or "").strip()
                if not trigger_object:
                    return None
                action = str(entry.get("执行动作") or "").strip()
                version = self._bump("实时规则生效")
                session = self.ensure_session(
                    trigger_object=trigger_object,
                    priority_raw=entry.get("优先级"),
                    tactic_condition="",
                    rule_condition=str(entry.get("决策条件") or "").strip(),
                    action=action,
                    source="实时决策",
                    ref_code=str(entry.get("规则编号") or ""),
                    ref_entry_id=int(entry.get("id") or 0) or None,
                    start_at=str(entry.get("生效时段") or ""),
                    finish_at=str(entry.get("生效时段") or ""),
                    version=version,
                )
                self._append_once(session.rule_hits, {
                    "规则编号": str(entry.get("规则编号") or ""),
                    "命中动作": action,
                    "触发事件": trigger_object,
                    "数据版本": version,
                    "来源": "规则生效",
                    "时间": _now(),
                }, ("规则编号", "来源", "命中动作"))
                return self.ensure_detail(trigger_object)
            except DecisionError:
                self._restore(snapshot)
                raise
            except Exception as exc:  # pragma: no cover - 兜底回退
                self._restore(snapshot)
                raise DecisionError(f"规则生效裁决失败，已回退到原完整边界：{exc}") from exc

    # ------------------------------------------------------------------ 登记接口
    def register(self, sessions: list[dict[str, Any]], *, auto_confirm: bool = False) -> dict[str, Any]:
        """登记生效会话并计算动作裁决；失败时不落任何会话/明细，整体回退。

        同一触发对象的多个会话在一次登记里一起裁决：只保留一个最终动作，落败会话逐条解释。
        """
        if not isinstance(sessions, list) or not sessions:
            raise DecisionError("未提供生效会话，未登记执行明细")
        with self._lock:
            snapshot = self._snapshot()
            try:
                objects: list[str] = []
                for item in sessions:
                    if not isinstance(item, dict):
                        raise DecisionError("生效会话格式不正确，未登记执行明细")
                    trigger_object = str(item.get("触发对象") or "").strip()
                    action = str(item.get("执行动作") or "").strip()
                    if not trigger_object:
                        raise DecisionError("缺少触发对象，未登记执行明细")
                    if not action:
                        raise DecisionError("缺少执行动作，未登记执行明细")
                    version = self._bump("登记生效会话")
                    self.ensure_session(
                        trigger_object=trigger_object,
                        priority_raw=item.get("优先级"),
                        tactic_condition=str(item.get("触发条件") or "").strip(),
                        rule_condition=str(item.get("规则条件") or "").strip(),
                        action=action,
                        source=str(item.get("来源") or "登记"),
                        ref_code=str(item.get("关联编号") or "").strip(),
                        ref_entry_id=None,
                        start_at=str(item.get("会话开始时间") or ""),
                        finish_at=str(item.get("会话完成时间") or ""),
                        version=version,
                    )
                    if trigger_object not in objects:
                        objects.append(trigger_object)
                detail: dict[str, Any] = {}
                for trigger_object in objects:
                    detail = self.ensure_detail(trigger_object)
                    if auto_confirm and detail.get("状态") == STATUS_PENDING:
                        detail = self.confirm(int(detail["id"])) or detail
                return detail
            except DecisionError:
                self._restore(snapshot)
                raise
            except Exception as exc:  # pragma: no cover - 兜底回退
                self._restore(snapshot)
                raise DecisionError(f"执行明细登记失败，已回退到原完整边界：{exc}") from exc

    # ------------------------------------------------------------------ 查询
    def list_details(
        self,
        *,
        status: str | None = None,
        keyword: str | None = None,
        ref: str | None = None,
        baseline: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            rows = [self._public(detail) for detail in self._details]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        if ref:
            rows = [row for row in rows if ref in [str(item) for item in row.get("关联编号", [])]]
        if baseline is not None:
            rows = [row for row in rows if bool(row.get("是否核对基准")) is baseline]
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("明细编号", "")) or keyword in str(row.get("触发对象", ""))
            ]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_detail(self, detail_id: int) -> dict[str, Any] | None:
        with self._lock:
            for detail in self._details:
                if detail.id == detail_id:
                    return self._public(detail)
        return None

    def confirm(self, detail_id: int) -> dict[str, Any] | None:
        with self._lock:
            for detail in self._details:
                if detail.id == detail_id:
                    self._close(detail, reason="人工确认：最终动作已核对")
                    return self._public(detail)
        return None

    def sessions_of(self, detail_id: int) -> list[dict[str, Any]]:
        with self._lock:
            detail = next((item for item in self._details if item.id == detail_id), None)
            if detail is None:
                return []
            codes = set(detail.sessions)
            return [self._session_public(session) for session in self._sessions if session.code in codes]

    @staticmethod
    def _session_public(session: Session) -> dict[str, Any]:
        return {
            "会话编号": session.code,
            "触发对象": session.trigger_object,
            "触发条件": session.trigger_condition,
            "策略触发条件": session.tactic_condition,
            "规则触发条件": session.rule_condition,
            "优先级": session.priority,
            "优先级原值": session.priority_raw,
            "执行动作": session.action,
            "来源": session.source,
            "关联编号": session.ref_code,
            "关联编号别名": list(session.aliases),
            "会话开始时间": session.start_at,
            "会话完成时间": session.finish_at,
            "状态": session.status,
            "落败原因": session.loser_reason,
            "是否核对基准": session.baseline,
            "生效数据版本": session.version,
            "最新数据版本": session.last_version,
            "发布记录": copy.deepcopy(session.publish_records),
            "命中结果": copy.deepcopy(session.rule_hits),
        }

    def replay(self, detail_id: int) -> dict[str, Any] | None:
        detail = self.get_detail(detail_id)
        if detail is None:
            return None
        with self._lock:
            sessions = self.sessions_of(detail_id)
            codes = {session["会话编号"] for session in sessions}
            versions = [
                copy.deepcopy(record) for record in self._versions
                if record.get("明细编号") == detail.get("明细编号")
                or record.get("会话编号") in codes
            ]
            followups = copy.deepcopy(self._followups.get(str(detail.get("明细编号")), []))
        steps = [
            {
                "步骤": 1,
                "说明": "按原口径读取触发条件：策略库取「触发条件」，实时决策取「决策条件」，不改写原值",
                "触发条件": detail.get("触发条件", ""),
            },
            {
                "步骤": 2,
                "说明": "发布记录与规则命中结果归入同一生效会话，数据版本可核对",
                "生效会话": detail.get("生效会话", ""),
                "数据版本": detail.get("数据版本", ""),
                "会话数据版本": detail.get("会话数据版本", {}),
            },
            {
                "步骤": 3,
                "说明": "按优先级裁决；同优先级且历史未规定先后时按登记基准保留先登记会话，时间字段不参与裁决",
                "裁决依据": detail.get("裁决依据", ""),
                "最终执行动作": detail.get("最终执行动作", ""),
                "冲突原因": detail.get("冲突原因", ""),
            },
            {
                "步骤": 4,
                "说明": "落败会话逐条解释，最终动作与冲突原因可一起回放",
                "落败会话": detail.get("落败会话", []),
            },
        ]
        return {
            "明细": detail,
            "会话": sessions,
            "版本核对": versions,
            "后续登记": followups,
            "回放步骤": steps,
        }

    def versions(self, *, detail_code: str = "") -> list[dict[str, Any]]:
        with self._lock:
            rows = copy.deepcopy(self._versions)
        if detail_code:
            rows = [row for row in rows if row.get("明细编号") == detail_code]
        return rows

    def publish_records(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        code = str(entry.get("策略编号") or "")
        with self._lock:
            return [
                copy.deepcopy(record)
                for session in self._sessions
                for record in session.publish_records
                if code and str(record.get("策略编号")) == code
            ]

    # ------------------------------------------------------------------ 快照
    def _snapshot(self) -> dict[str, Any]:
        """记录调用前的完整边界：台账内部状态 + 会被回写的存量记录。"""
        return {
            "sessions": copy.deepcopy(self._sessions),
            "details": copy.deepcopy(self._details),
            "versions": copy.deepcopy(self._versions),
            "followups": copy.deepcopy(self._followups),
            "session_seq": self._session_seq,
            "detail_seq": self._detail_seq,
            "version": self._version,
            "realtime_rows": copy.deepcopy(store.rows(REALTIME_MODULE)),
        }

    def _restore(self, snapshot: dict[str, Any]) -> None:
        self._sessions = snapshot["sessions"]
        self._details = snapshot["details"]
        self._versions = snapshot["versions"]
        self._followups = snapshot["followups"]
        self._session_seq = snapshot["session_seq"]
        self._detail_seq = snapshot["detail_seq"]
        self._version = snapshot["version"]
        # 原地回填：保留既有 dict 对象的身份，让路由层已取到的记录引用也能看到回退后的值。
        rows = store.rows(REALTIME_MODULE)
        restored = snapshot["realtime_rows"]
        common = min(len(rows), len(restored))
        for index in range(common):
            rows[index].clear()
            rows[index].update(restored[index])
        if len(rows) > len(restored):
            del rows[len(restored):]
        elif len(restored) > len(rows):
            rows.extend(copy.deepcopy(restored[len(rows):]))


ledger = DecisionLedger()
ledger.seed_baseline()
