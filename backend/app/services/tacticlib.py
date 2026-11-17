"""策略库业务规则：状态流转、字段校验与筛选口径都收在这里。

列表/详情/登记/流转的通用写法在 ``app.services.base.CatalogService``；这里只保留策略库
自己的字段、状态序列和动作表，并在「发布策略」动作后把发布记录同步到实时决策的生效会话。
"""
from __future__ import annotations

from typing import Any

from app.services.base import CatalogService
from app.services.decision import DecisionError, ledger

MODULE = "tacticlib"
REQUIRED_FIELDS = ["策略编号", "策略名称", "策略类型"]
STATUS_ORDER = ["草稿", "策略评审中", "已发布", "已退役"]
ACTION_RULES = {"提交策略评审": "策略评审中", "发布策略": "已发布", "退役策略": "已退役"}
NEGATIVE_ACTIONS = []


class TacticlibService(CatalogService):
    MODULE = MODULE
    REQUIRED_FIELDS = REQUIRED_FIELDS
    STATUS_ORDER = STATUS_ORDER
    ACTION_RULES = ACTION_RULES
    NEGATIVE_ACTIONS = NEGATIVE_ACTIONS
    ENTITY_NAME = "运营策略"
    SCOPE_NAME = "策略库"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"运营策略 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于策略库可执行范围"
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

        if action == "发布策略":
            try:
                ledger.on_tactic_publish(entry)
            except DecisionError as exc:
                # 裁决失败：回退到原完整边界，策略状态与实时规则都不留下半条变更。
                entry.update(previous)
                return None, str(exc)
        return entry, f"运营策略已{action}"

    def publish_records(self, entry_id: int) -> list[dict[str, Any]] | None:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        return ledger.publish_records(entry)
