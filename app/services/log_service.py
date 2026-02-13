"""操作日志服务层。"""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId
from fastapi import Request

from app.models import OperationLog
from app.models.operation_log import utc_now
from app.services import config_service

MODULE_LABELS: dict[str, str] = {
    "auth": "账号安全",
    "rbac": "RBAC 权限",
    "admin_users": "管理员管理",
    "config": "系统配置",
    "logs": "操作日志",
    "backup": "数据备份",
}


def normalize_log_action(value: str) -> str:
    action = value.strip().lower()
    if action in set(config_service.AUDIT_ACTION_ORDER):
        return action
    return ""


def get_request_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return ""


async def record_action(
    *,
    action: str,
    module: str,
    operator: str,
    target: str = "",
    target_id: str = "",
    detail: str = "",
    method: str = "",
    path: str = "",
    ip: str = "",
) -> bool:
    normalized = normalize_log_action(action)
    if not normalized:
        return False

    enabled = await config_service.get_audit_log_actions()
    if normalized not in set(enabled):
        return False

    try:
        await OperationLog(
            action=normalized,
            module=module,
            target=target.strip(),
            target_id=target_id.strip(),
            detail=detail.strip(),
            operator=(operator or "system").strip() or "system",
            method=method.upper().strip(),
            path=path.strip(),
            ip=ip.strip(),
            created_at=utc_now(),
        ).insert()
    except Exception:
        return False
    return True


async def record_request(
    request: Request,
    *,
    action: str,
    module: str,
    target: str = "",
    target_id: str = "",
    detail: str = "",
) -> bool:
    return await record_action(
        action=action,
        module=module,
        operator=request.session.get("admin_name") or "system",
        target=target,
        target_id=target_id,
        detail=detail,
        method=request.method,
        path=request.url.path,
        ip=get_request_ip(request),
    )


async def list_logs(filters: dict[str, str], page: int, page_size: int) -> tuple[list[OperationLog], int]:
    query: dict[str, Any] = {}
    keyword = filters.get("search_q", "").strip()
    if keyword:
        regex = {"$regex": keyword, "$options": "i"}
        query["$or"] = [
            {"target": regex},
            {"detail": regex},
            {"operator": regex},
            {"path": regex},
            {"ip": regex},
        ]

    action = normalize_log_action(filters.get("search_action", ""))
    if action:
        query["action"] = action

    module = filters.get("search_module", "").strip()
    if module:
        query["module"] = module

    sort_value = filters.get("search_sort", "created_desc")
    sort_field = "-created_at" if sort_value != "created_asc" else "created_at"

    total = await OperationLog.find(query).count()
    safe_page = page if page > 0 else 1
    skip = max((safe_page - 1) * page_size, 0)
    items = await OperationLog.find(query).sort(sort_field).skip(skip).limit(page_size).to_list()
    return items, total


async def get_log(log_id: str) -> OperationLog | None:
    """按 ID 查询单条日志。"""

    try:
        object_id = PydanticObjectId(log_id)
    except Exception:
        return None
    return await OperationLog.get(object_id)


async def delete_log(log: OperationLog) -> None:
    """删除单条日志。"""

    await log.delete()
