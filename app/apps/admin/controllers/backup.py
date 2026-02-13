"""数据备份控制器。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services import backup_service, log_service, permission_decorator
from app.services.backup_scheduler import restart_scheduler

BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(prefix="/admin")

BACKUP_PAGE_SIZE = 10

# 云端供应商选项
CLOUD_PROVIDER_LABELS: dict[str, str] = {
    "aliyun_oss": "阿里云 OSS",
    "tencent_cos": "腾讯云 COS",
}


def fmt_dt(value: datetime | None) -> str:
    """把 UTC 时间格式化为本地易读字符串。"""
    if not value:
        return ""
    if value.tzinfo is None:
        return value.strftime("%Y-%m-%d %H:%M")
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def fmt_bytes(value: int | None) -> str:
    """把字节数格式化为人类可读单位。"""
    size = int(value or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    current = float(size)
    for unit in units:
        if current < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(current)} {unit}"
            return f"{current:.2f} {unit}"
        current /= 1024
    return f"{size} B"


templates.env.filters["fmt_dt"] = fmt_dt
templates.env.filters["fmt_bytes"] = fmt_bytes


def base_context(request: Request) -> dict[str, Any]:
    """构造模板公共上下文。"""
    return {
        "request": request,
        "current_admin": request.session.get("admin_name"),
    }


def parse_positive_int(raw_value: Any, default: int) -> int:
    """把输入解析为正整数，失败时返回默认值。"""
    try:
        value = int(str(raw_value))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


async def read_request_values(request: Request) -> dict[str, str]:
    """兼容 query 与表单读取请求参数。"""
    values: dict[str, str] = {key: value for key, value in request.query_params.items()}
    if request.method == "GET":
        return values

    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" not in content_type and "multipart/form-data" not in content_type:
        return values

    form_data = await request.form()
    for key, value in form_data.items():
        if isinstance(value, str):
            values[key] = value
    return values


def build_pagination(total: int, page: int, page_size: int) -> dict[str, Any]:
    """构建分页元数据，便于模板直接渲染。"""
    safe_size = max(page_size, 1)
    total_pages = max((total + safe_size - 1) // safe_size, 1)
    current = min(max(page, 1), total_pages)

    start_page = max(current - 2, 1)
    end_page = min(start_page + 4, total_pages)
    start_page = max(end_page - 4, 1)

    if total == 0:
        start_item = 0
        end_item = 0
    else:
        start_item = (current - 1) * safe_size + 1
        end_item = min(current * safe_size, total)

    return {
        "page": current,
        "page_size": safe_size,
        "total": total,
        "total_pages": total_pages,
        "has_prev": current > 1,
        "has_next": current < total_pages,
        "prev_page": current - 1,
        "next_page": current + 1,
        "pages": list(range(start_page, end_page + 1)),
        "start_item": start_item,
        "end_item": end_item,
    }


async def build_table_context(
    request: Request,
    page: int,
    page_size: int,
    action_feedback: dict[str, str] | None = None,
) -> dict[str, Any]:
    """构建备份记录表格上下文。"""
    records, total = await backup_service.list_backup_records(page=page, page_size=page_size)
    pagination = build_pagination(total, page, page_size)

    return {
        **base_context(request),
        "records": records,
        "pagination": pagination,
        "action_feedback": action_feedback,
    }


@router.get("/backup", response_class=HTMLResponse)
@permission_decorator.permission_meta("backup_records", "read")
async def backup_page(request: Request) -> HTMLResponse:
    """备份管理主页。"""
    config = await backup_service.get_backup_config()
    collections = await backup_service.get_collection_names()

    context = await build_table_context(request, page=1, page_size=BACKUP_PAGE_SIZE)
    context.update(
        {
            "config": config,
            "saved": False,
            "collections": collections,
            "cloud_provider_labels": CLOUD_PROVIDER_LABELS,
        }
    )

    await log_service.record_request(
        request,
        action="read",
        module="backup",
        target="数据备份",
        detail="访问数据备份页面",
    )
    return templates.TemplateResponse("pages/backup.html", context)


@router.get("/backup/table", response_class=HTMLResponse)
@permission_decorator.permission_meta("backup_records", "read")
async def backup_table(request: Request) -> HTMLResponse:
    """HTMX 局部刷新备份记录表格。"""
    page = parse_positive_int(request.query_params.get("page"), default=1)
    page_size = parse_positive_int(request.query_params.get("page_size"), default=BACKUP_PAGE_SIZE)
    context = await build_table_context(request, page=page, page_size=page_size)
    return templates.TemplateResponse("partials/backup_table.html", context)


@router.get("/backup/collections", response_class=HTMLResponse)
@permission_decorator.permission_meta("backup_config", "read")
async def backup_collections(request: Request) -> HTMLResponse:
    """HTMX 局部刷新可选集合列表。"""
    config = await backup_service.get_backup_config()
    collections = await backup_service.get_collection_names()
    context = {
        **base_context(request),
        "collections": collections,
        "excluded_collections": set(config.get("excluded_collections", [])),
    }
    return templates.TemplateResponse("partials/backup_collections.html", context)


@router.post("/backup", response_class=HTMLResponse)
@permission_decorator.permission_meta("backup_config", "update")
async def backup_save_config(request: Request) -> HTMLResponse:
    """保存备份配置。"""
    form = await request.form()

    payload: dict[str, Any] = {
        "enabled": form.get("enabled") == "on",
        "local_dir": str(form.get("local_dir", "backups")).strip(),
        "local_retention": form.get("local_retention", "5"),
        "interval_hours": form.get("interval_hours", "24"),
        "excluded_collections": [
            str(value)
            for value in form.getlist("excluded_collections")
            if isinstance(value, str) and value.strip()
        ],
        "cloud_enabled": form.get("cloud_enabled") == "on",
        "cloud_providers": [
            str(value)
            for value in form.getlist("cloud_providers")
            if isinstance(value, str) and value.strip()
        ],
        "cloud_path": str(form.get("cloud_path", "backups/pyfastadmin")).strip(),
        "cloud_retention": form.get("cloud_retention", "10"),
        # OSS
        "oss_region": str(form.get("oss_region", "")).strip(),
        "oss_endpoint": str(form.get("oss_endpoint", "")).strip(),
        "oss_access_key_id": str(form.get("oss_access_key_id", "")).strip(),
        "oss_access_key_secret": str(form.get("oss_access_key_secret", "")).strip(),
        "oss_bucket": str(form.get("oss_bucket", "")).strip(),
        # COS
        "cos_region": str(form.get("cos_region", "")).strip(),
        "cos_secret_id": str(form.get("cos_secret_id", "")).strip(),
        "cos_secret_key": str(form.get("cos_secret_key", "")).strip(),
        "cos_bucket": str(form.get("cos_bucket", "")).strip(),
    }

    config = await backup_service.save_backup_config(payload)
    restart_scheduler()

    collections = await backup_service.get_collection_names()
    context = await build_table_context(request, page=1, page_size=BACKUP_PAGE_SIZE)
    context.update(
        {
            "config": config,
            "saved": True,
            "collections": collections,
            "cloud_provider_labels": CLOUD_PROVIDER_LABELS,
        }
    )

    await log_service.record_request(
        request,
        action="update",
        module="backup",
        target="备份配置",
        detail="更新数据备份配置",
    )
    return templates.TemplateResponse("pages/backup.html", context)


@router.post("/backup/trigger", response_class=HTMLResponse)
@permission_decorator.permission_meta("backup_records", "trigger")
async def backup_trigger(request: Request) -> HTMLResponse:
    """手动触发一次备份。"""
    record = await backup_service.run_backup()
    feedback = {
        "variant": "success" if record.status == "success" else "error",
        "message": "手动备份执行完成" if record.status == "success" else f"手动备份失败：{record.error or '未知错误'}",
    }
    context = await build_table_context(
        request,
        page=1,
        page_size=BACKUP_PAGE_SIZE,
        action_feedback=feedback,
    )

    await log_service.record_request(
        request,
        action="create",
        module="backup",
        target="手动备份",
        detail=f"手动触发数据库备份，状态：{record.status}",
    )
    return templates.TemplateResponse("partials/backup_table.html", context)


@router.post("/backup/{record_id}/restore", response_class=HTMLResponse)
@permission_decorator.permission_meta("backup_records", "restore")
async def backup_restore(request: Request, record_id: str) -> HTMLResponse:
    """按指定备份记录恢复数据库。"""
    values = await read_request_values(request)
    page = parse_positive_int(values.get("page"), default=1)
    page_size = parse_positive_int(values.get("page_size"), default=BACKUP_PAGE_SIZE)

    success, message = await backup_service.restore_backup_record(record_id)
    feedback = {
        "variant": "success" if success else "error",
        "message": message,
    }
    context = await build_table_context(
        request,
        page=page,
        page_size=page_size,
        action_feedback=feedback,
    )

    await log_service.record_request(
        request,
        action="update",
        module="backup",
        target="恢复备份",
        target_id=record_id,
        detail=f"恢复备份记录 {record_id}：{message}",
    )
    return templates.TemplateResponse("partials/backup_table.html", context)


@router.delete("/backup/{record_id}", response_class=HTMLResponse)
@permission_decorator.permission_meta("backup_records", "delete")
async def backup_delete(request: Request, record_id: str) -> HTMLResponse:
    """删除一条备份记录。"""
    values = await read_request_values(request)
    page = parse_positive_int(values.get("page"), default=1)
    page_size = parse_positive_int(values.get("page_size"), default=BACKUP_PAGE_SIZE)

    deleted = await backup_service.delete_backup_record(record_id)
    feedback = {
        "variant": "success" if deleted else "error",
        "message": "删除备份记录成功" if deleted else "删除备份记录失败：记录不存在",
    }
    context = await build_table_context(
        request,
        page=page,
        page_size=page_size,
        action_feedback=feedback,
    )

    await log_service.record_request(
        request,
        action="delete",
        module="backup",
        target="备份记录",
        target_id=record_id,
        detail=feedback["message"],
    )
    return templates.TemplateResponse("partials/backup_table.html", context)
