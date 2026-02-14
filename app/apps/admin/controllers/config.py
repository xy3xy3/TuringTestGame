"""系统配置控制器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services import backup_service, config_service, log_service, permission_decorator, cleanup_service
from app.services.backup_scheduler import restart_scheduler
from app.services.cleanup_service import restart_cleanup_scheduler

BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(prefix="/admin")

CLOUD_PROVIDER_LABELS: dict[str, str] = {
    "aliyun_oss": "阿里云 OSS",
    "tencent_cos": "腾讯云 COS",
}
VALID_CONFIG_TABS = {"system", "backup", "cleanup", "game_time"}


def base_context(request: Request) -> dict[str, Any]:
    """构造模板公共上下文。"""
    return {
        "request": request,
        "current_admin": request.session.get("admin_name"),
    }


def _normalize_config_tab(raw_value: Any) -> str:
    """规范化配置页签参数，仅允许 system/backup。"""
    tab = str(raw_value or "").strip().lower()
    return tab if tab in VALID_CONFIG_TABS else "system"


def _build_backup_payload(form_data: Any) -> dict[str, Any]:
    """从系统配置表单构建备份配置载荷。"""
    return {
        "enabled": form_data.get("backup_enabled") == "on",
        "local_dir": str(form_data.get("backup_local_dir", "backups")).strip(),
        "local_retention": form_data.get("backup_local_retention", "5"),
        "interval_hours": form_data.get("backup_interval_hours", "24"),
        "excluded_collections": [
            str(value)
            for value in form_data.getlist("backup_excluded_collections")
            if isinstance(value, str) and value.strip()
        ],
        "cloud_enabled": form_data.get("backup_cloud_enabled") == "on",
        "cloud_providers": [
            str(value)
            for value in form_data.getlist("backup_cloud_providers")
            if isinstance(value, str) and value.strip()
        ],
        "cloud_path": str(form_data.get("backup_cloud_path", "backups/TuringTestGame")).strip(),
        "cloud_retention": form_data.get("backup_cloud_retention", "10"),
        # OSS
        "oss_region": str(form_data.get("backup_oss_region", "")).strip(),
        "oss_endpoint": str(form_data.get("backup_oss_endpoint", "")).strip(),
        "oss_access_key_id": str(form_data.get("backup_oss_access_key_id", "")).strip(),
        "oss_access_key_secret": str(form_data.get("backup_oss_access_key_secret", "")).strip(),
        "oss_bucket": str(form_data.get("backup_oss_bucket", "")).strip(),
        # COS
        "cos_region": str(form_data.get("backup_cos_region", "")).strip(),
        "cos_secret_id": str(form_data.get("backup_cos_secret_id", "")).strip(),
        "cos_secret_key": str(form_data.get("backup_cos_secret_key", "")).strip(),
        "cos_bucket": str(form_data.get("backup_cos_bucket", "")).strip(),
    }


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request) -> HTMLResponse:
    """系统配置主页。"""
    smtp = await config_service.get_smtp_config()
    audit_actions = await config_service.get_audit_log_actions()
    backup_config = await backup_service.get_backup_config()
    collections = await backup_service.get_collection_names()
    base_url = await config_service.get_base_url()
    footer_copyright = await config_service.get_footer_copyright()
    cleanup_config = await cleanup_service.get_cleanup_config()
    game_time_config = await config_service.get_game_time_config()
    active_config_tab = _normalize_config_tab(request.query_params.get("tab"))

    context = {
        **base_context(request),
        "smtp": smtp,
        "saved": False,
        "audit_actions": audit_actions,
        "audit_action_labels": config_service.AUDIT_ACTION_LABELS,
        "backup_config": backup_config,
        "collections": collections,
        "cloud_provider_labels": CLOUD_PROVIDER_LABELS,
        "active_config_tab": active_config_tab,
        "base_url": base_url,
        "footer_copyright": footer_copyright,
        "cleanup_config": cleanup_config,
        "game_time_config": game_time_config,
    }
    await log_service.record_request(
        request,
        action="read",
        module="config",
        target="系统配置",
        detail=f"访问系统配置页面（tab={active_config_tab}）",
    )
    return templates.TemplateResponse("pages/config.html", context)


@router.post("/config", response_class=HTMLResponse)
@permission_decorator.permission_meta("config", "update")
async def config_save(
    request: Request,
    smtp_host: str = Form(""),
    smtp_port: str = Form(""),
    smtp_user: str = Form(""),
    smtp_pass: str = Form(""),
    smtp_from: str = Form(""),
    smtp_ssl: str = Form(""),
    base_url: str = Form(""),
    footer_copyright_text: str = Form(""),
    footer_copyright_url: str = Form(""),
) -> HTMLResponse:
    """保存系统配置（SMTP + 日志策略 + 备份设置）。"""
    form_data = await request.form()
    active_config_tab = _normalize_config_tab(form_data.get("config_tab"))

    smtp_payload = {
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_user": smtp_user,
        "smtp_pass": smtp_pass,
        "smtp_from": smtp_from,
        "smtp_ssl": smtp_ssl,
    }
    selected_actions = [
        str(value)
        for value in form_data.getlist("audit_actions")
        if isinstance(value, str)
    ]
    backup_payload = _build_backup_payload(form_data)

    await config_service.save_smtp_config(smtp_payload)
    audit_actions = await config_service.save_audit_log_actions(selected_actions)
    await backup_service.save_backup_config(backup_payload)
    await config_service.save_base_url(base_url)
    await config_service.save_footer_copyright(footer_copyright_text, footer_copyright_url)

    # 保存清理配置
    cleanup_enabled = form_data.get("cleanup_enabled") == "on"
    cleanup_retention_days = int(form_data.get("cleanup_retention_days", "7") or "7")
    cleanup_interval_hours = int(form_data.get("cleanup_interval_hours", "24") or "24")
    await cleanup_service.save_cleanup_config(
        enabled=cleanup_enabled,
        retention_days=cleanup_retention_days,
        interval_hours=cleanup_interval_hours,
    )
    restart_cleanup_scheduler()

    # 保存游戏时间配置
    game_time_payload = {
        "setup_duration": int(form_data.get("setup_duration", "60") or "60"),
        "question_duration": int(form_data.get("question_duration", "30") or "30"),
        "answer_duration": int(form_data.get("answer_duration", "45") or "45"),
        "vote_duration": int(form_data.get("vote_duration", "15") or "15"),
        "reveal_delay": int(form_data.get("reveal_delay", "3") or "3"),
    }
    await config_service.save_game_time_config(game_time_payload)

    restart_scheduler()

    smtp = await config_service.get_smtp_config()
    backup_config = await backup_service.get_backup_config()
    collections = await backup_service.get_collection_names()
    base_url = await config_service.get_base_url()
    footer_copyright = await config_service.get_footer_copyright()
    cleanup_config = await config_service.get_cleanup_config()
    game_time_config = await config_service.get_game_time_config()
    context = {
        **base_context(request),
        "smtp": smtp,
        "saved": True,
        "audit_actions": audit_actions,
        "audit_action_labels": config_service.AUDIT_ACTION_LABELS,
        "backup_config": backup_config,
        "collections": collections,
        "cloud_provider_labels": CLOUD_PROVIDER_LABELS,
        "active_config_tab": active_config_tab,
        "base_url": base_url,
        "footer_copyright": footer_copyright,
        "cleanup_config": cleanup_config,
        "game_time_config": game_time_config,
    }
    detail = (
        f"更新系统配置（tab={active_config_tab}，含备份参数），日志类型："
        + ("、".join(config_service.AUDIT_ACTION_LABELS.get(item, item) for item in audit_actions) if audit_actions else "不记录")
    )
    await log_service.record_request(
        request,
        action="update",
        module="config",
        target="系统配置",
        detail=detail,
    )
    return templates.TemplateResponse("pages/config.html", context)
