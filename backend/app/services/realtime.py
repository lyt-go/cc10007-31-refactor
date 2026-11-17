"""实时决策业务规则：状态流转、字段校验与筛选口径都收在这里。

列表/详情/登记/流转的通用写法在 ``app.services.base.CatalogService``；这里只保留实时决策
自己的字段、状态序列和动作表，并在「生效规则」动作后把规则命中结果登记到共享生效会话。
"""
from __future__ import annotations

from typing import Any

from app.services.base import CatalogService
from app.services.decision import DecisionError, ledger

MODULE = "realtime"
REQUIRED_FIELDS = ["规则编号", "触发事件", "决策条件"]
STATUS_ORDER = ["草稿", "规则评审中", "已生效", "已暂停"]
ACTION_RULES = {"提交规则评审": "规则评审中", "生效规则": "已生效", "暂停规则": "已暂停"}
NEGATIVE_ACTIONS = []


class RealtimeService(CatalogService):
    MODULE = MODULE
    REQUIRED_FIELDS = REQUIRED_FIELDS
    STATUS_ORDER = STATUS_ORDER
    ACTION_RULES = ACTION_RULES
    NEGATIVE_ACTIONS = NEGATIVE_ACTIONS
    ENTITY_NAME = "决策规则"
    SCOPE_NAME = "实时决策"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"决策规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于实时决策可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        previous = {
            "status": entry.get("status"),
            "pending": entry.get("pending"),
            "abnormal": entry.get("abnormal"),
        }
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS

        if action == "生效规则":
            try:
                ledger.on_realtime_activate(entry)
            except DecisionError as exc:
                entry.update(previous)
                return None, str(exc)
        return entry, f"决策规则已{action}"
