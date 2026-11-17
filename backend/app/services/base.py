"""列表/详情/登记/状态流转的共用写法。

策略库与实时决策两个模块的列表口径、登记校验和动作流转此前是两段逐字重复的代码，
这里收拢成一份基类：两边只声明自己的模块名、状态序列和动作表，行为与原来逐字段一致。
"""
from __future__ import annotations

from typing import Any

from app.store import store


class CatalogService:
    """单个业务清单的通用读写口径。

    子类需要声明 ``MODULE``、``REQUIRED_FIELDS``、``STATUS_ORDER``、``ACTION_RULES``，
    其余行为（分页、关键字过滤、登记校验、目标状态校验和提示语）都由基类统一给出。
    """

    MODULE = ""
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER: list[str] = []
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS: list[str] = []
    ENTITY_NAME = "记录"
    SCOPE_NAME = "模块"

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(self.MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get(self.KEYWORD_FIELD, ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    @property
    def KEYWORD_FIELD(self) -> str:
        return self.REQUIRED_FIELDS[0]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(self.MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in self.REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(self.MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in self.REQUIRED_FIELDS})
        entry["status"] = self.STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY_NAME} {entry_id} 不存在或已归档"
        if action not in self.ACTION_RULES:
            return None, f"动作「{action}」不属于{self.SCOPE_NAME}可执行范围"
        target = self.ACTION_RULES[action]
        if target not in self.STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != self.STATUS_ORDER[-1]
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS
        return entry, f"{self.ENTITY_NAME}已{action}"
