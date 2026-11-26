"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services import service_for

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


def require_capability(module: str, method: str):
    """取领域服务并确认它支持该扩展动作；不支持的模块按未知能力处理。"""
    service = require_service(module)
    if not callable(getattr(service, method, None)):
        raise HTTPException(status_code=404, detail=f"模块 {module} 不支持该操作")
    return service


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/manifest")
def get_manifest(
    module: str,
    entry_id: int = Query(alias="publish_id", ge=1),
) -> dict[str, Any]:
    """发布行回查：发布确认、同版分发清单与可领取状态（固定路径，先于动态 entry 匹配）。"""
    service = require_capability(module, "get_manifest")
    result = service.get_manifest(entry_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.get("/{module}/receipt")
def get_publish_receipt(
    module: str,
    entry_id: int = Query(alias="publish_id", ge=1),
) -> dict[str, Any]:
    """发布完成回执：断线后按原发布编号回查提交结果。"""
    service = require_capability(module, "get_receipt")
    result = service.get_receipt(entry_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.get("/{module}/claimable")
def list_claimable(
    module: str,
    publish_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """分发领取端：按发布编号取完整分发清单，返回可领取与不可领取及原因。"""
    service = require_capability(module, "list_claimable")
    return service.list_claimable(publish_id)


@router.post("/{module}/publish")
def publish_batch(module: str, entry_id: int = Query(alias="publish_id", ge=1)) -> dict[str, Any]:
    """发布提交：确认版本并登记分发清单，重复请求沿用已确认版本。"""
    service = require_capability(module, "publish_batch")
    return service.publish_batch(entry_id)


@router.post("/{module}/claim")
def claim_batch(module: str, entry_id: int = Query(alias="task_id", ge=1)) -> dict[str, Any]:
    """分发领取：从完整分发清单认领一条批次，不把未完成的申请记为已发出。"""
    service = require_capability(module, "claim_batch")
    return service.claim_batch(entry_id)


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)
