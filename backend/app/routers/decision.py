"""实时决策执行明细接口：登记生效会话、读取裁决结果、回放与数据版本核对。

这个路由只读写裁决台账（``app.services.decision``），不注册进 ``store`` 的模块表，
因此运营概览里的模块数量和既有各模块列表口径都不受影响。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, DecisionRegisterPayload, PageResult
from app.services.decision import DecisionError, ledger

router = APIRouter(prefix="/api/decision", tags=["实时决策执行明细"])


@router.get("/executions", response_model=PageResult[dict])
def list_executions(
    status: str | None = Query(default=None, description="待处理、已闭环"),
    keyword: str | None = Query(default=None, description="按明细编号或触发对象检索"),
    ref: str | None = Query(default=None, description="按关联编号（策略编号/规则编号）检索"),
    baseline: bool | None = Query(default=None, description="只看核对基准记录（REAL-0003）"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """实时决策待处理明细：一个触发对象一条最终动作。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = ledger.list_details(
        status=status, keyword=keyword, ref=ref, baseline=baseline, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/executions", response_model=ActionResult)
def register_executions(payload: DecisionRegisterPayload) -> ActionResult:
    """登记生效会话并计算动作冲突裁决。

    同一触发对象只保留一个最终动作；重复登记收敛为同一条明细。会话缺字段、关联规则不存在
    或已闭环明细的最终动作会被改写时整体回退，不留下半条会话、半条明细。
    """
    try:
        detail = ledger.register(payload.sessions, auto_confirm=payload.auto_confirm)
    except DecisionError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(
        ok=True,
        message=f"执行明细 {detail.get('明细编号', '')} 已登记，最终执行动作「{detail.get('最终执行动作', '')}」",
        entry=detail,
    )


@router.get("/executions/{detail_id}", response_model=dict)
def get_execution(detail_id: int) -> dict:
    """读取单条执行明细；最终动作与冲突原因一起返回。"""
    detail = ledger.get_detail(detail_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"执行明细 {detail_id} 不存在或已归档")
    return detail


@router.post("/executions/{detail_id}/actions", response_model=ActionResult)
def run_execution_action(detail_id: int, payload: DecisionRegisterPayload) -> ActionResult:
    """确认执行明细：只允许「确认执行明细」，确认后同一触发对象不再生成重复数据集。"""
    action = str(payload.action or "").strip()
    if action != "确认执行明细":
        return ActionResult(ok=False, message=f"动作「{action}」不属于执行明细可执行范围")
    detail = ledger.confirm(detail_id)
    if detail is None:
        return ActionResult(ok=False, message=f"执行明细 {detail_id} 不存在或已归档")
    return ActionResult(ok=True, message="执行明细已确认闭环", entry=detail)


@router.get("/executions/{detail_id}/sessions", response_model=dict)
def list_execution_sessions(detail_id: int) -> dict:
    """读取执行明细下的生效会话（含落败会话与共享数据版本）。"""
    if ledger.get_detail(detail_id) is None:
        raise HTTPException(status_code=404, detail=f"执行明细 {detail_id} 不存在或已归档")
    return {"execution": detail_id, "items": ledger.sessions_of(detail_id)}


@router.get("/executions/{detail_id}/replay", response_model=dict)
def replay_execution(detail_id: int) -> dict:
    """回放一条执行明细：触发条件口径、共享会话、裁决过程、落败原因一起给出。"""
    payload = ledger.replay(detail_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"执行明细 {detail_id} 不存在或已归档")
    return payload


@router.get("/versions", response_model=dict)
def list_versions(detail_code: str = Query(default="", description="按明细编号过滤")) -> dict[str, Any]:
    """数据版本核对：每条明细的登记、更新、确认都能从版本序列里对齐。"""
    items = ledger.versions(detail_code=detail_code)
    return {"current": ledger.current_version(), "total": len(items), "items": items}
