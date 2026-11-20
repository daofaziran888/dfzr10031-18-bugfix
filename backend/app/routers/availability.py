"""可用观测接口：营业日历、观测批次、停采回执的覆盖分解，以及单位时间指标入口。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult
from app.services.availability import AvailabilityService

router = APIRouter(prefix="/api/availability", tags=["可用观测"])

service = AvailabilityService()


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    """采集源与营业日历：窗口分母的唯一依据。"""
    return {"items": service.list_sources(), "total": len(service.list_sources())}


@router.get("/windows")
def list_windows(source_id: str | None = Query(default=None)) -> dict[str, Any]:
    """逐窗口覆盖分解：有效营业分钟、实采分钟、缺报与停采记账。"""
    windows = service.list_windows(source_id)
    items = [service.compute_window(str(row["window_id"])) for row in windows]
    return {"items": [item for item in items if item is not None], "total": len(windows)}


@router.get("/windows/{window_id}")
def get_window(window_id: str) -> dict[str, Any]:
    """单窗口明细；不存在时给出可读说明。"""
    computed = service.compute_window(window_id)
    if computed is None:
        raise HTTPException(status_code=404, detail=f"采集窗口 {window_id} 不存在或已归档")
    return computed


@router.get("/metric")
def metric_entry() -> dict[str, Any]:
    """指标入口：从覆盖分解表导出单位时间指标，只认实采营业分钟作分母。"""
    return service.metric_entry()


@router.get("/verify")
def verify() -> dict[str, Any]:
    """场景对账：内置示例窗口的当期值、待补分钟与基线可比性。"""
    return service.verify()


@router.post("/windows/{window_id}/retry", response_model=ActionResult)
def retry_window(window_id: str) -> ActionResult:
    """重试取数失败的批次；成功与否都返回重算后的窗口。"""
    entry, message = service.retry_window(window_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
