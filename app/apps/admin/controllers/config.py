"""系统配置控制器。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from app.services import backup_service, cleanup_service, config_service, log_service, permission_decorator, validators
from app.services import rate_limit_service
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
GAME_BGM_UPLOAD_DIR = Path(__file__).resolve().parents[3] / "static" / "uploads" / "game_bgm"
MAX_BGM_UPLOAD_BYTES = 8 * 1024 * 1024


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


async def _build_config_page_context(
    request: Request,
    *,
    active_config_tab: str,
    saved: bool,
    config_error: str = "",
    game_bgm_changes: list[str] | None = None,
) -> dict[str, Any]:
    """构建系统配置页面上下文。"""

    smtp = await config_service.get_smtp_config()
    audit_actions = await config_service.get_audit_log_actions()
    backup_config = await backup_service.get_backup_config()
    collections = await backup_service.get_collection_names()
    base_url = await config_service.get_base_url()
    footer_copyright = await config_service.get_footer_copyright()
    rate_limit_config = await config_service.get_rate_limit_config()
    cleanup_config = await cleanup_service.get_cleanup_config()
    game_time_config = await config_service.get_game_time_config()
    game_role_balance_config = await config_service.get_game_role_balance_config()
    game_bgm_config = await config_service.get_game_bgm_config()
    return {
        **base_context(request),
        "smtp": smtp,
        "saved": saved,
        "config_error": config_error,
        "audit_actions": audit_actions,
        "audit_action_labels": config_service.AUDIT_ACTION_LABELS,
        "backup_config": backup_config,
        "collections": collections,
        "cloud_provider_labels": CLOUD_PROVIDER_LABELS,
        "active_config_tab": active_config_tab,
        "base_url": base_url,
        "footer_copyright": footer_copyright,
        "rate_limit_config": rate_limit_config,
        "cleanup_config": cleanup_config,
        "game_time_config": game_time_config,
        "game_role_balance_config": game_role_balance_config,
        "game_bgm_phase_labels": config_service.GAME_BGM_PHASE_KEYS,
        "game_bgm_config": game_bgm_config,
        "game_bgm_changes": game_bgm_changes or [],
    }


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


def _is_checked(value: Any) -> bool:
    """将前端复选框值统一转换为布尔。"""

    return str(value or "").strip().lower() in {"1", "true", "on", "yes", "y"}


def _is_non_empty_upload(upload: UploadFile | None) -> bool:
    """判断上传对象是否包含真实文件。"""

    if not upload:
        return False
    return bool(str(upload.filename or "").strip())


def _remove_old_bgm_file(url: str) -> None:
    """删除旧背景音乐文件（仅允许删除 game_bgm 目录内文件）。"""

    normalized = str(url or "").strip()
    prefix = "/static/uploads/game_bgm/"
    if not normalized.startswith(prefix):
        return

    filename = Path(normalized).name
    if not filename:
        return

    candidate = (GAME_BGM_UPLOAD_DIR / filename).resolve()
    try:
        candidate.relative_to(GAME_BGM_UPLOAD_DIR.resolve())
    except Exception:
        return

    if candidate.is_file():
        candidate.unlink()


async def _merge_game_bgm_uploads(
    form_data: Any,
    existing_config: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """处理阶段背景音乐上传/清空逻辑，返回新配置与变更摘要。"""

    merged = {key: str(existing_config.get(key, "")).strip() for key in config_service.GAME_BGM_PHASE_KEYS}
    changes: list[str] = []
    GAME_BGM_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    for phase, label in config_service.GAME_BGM_PHASE_KEYS.items():
        clear_key = f"bgm_{phase}_clear"
        file_key = f"bgm_{phase}_file"

        clear_requested = _is_checked(form_data.get(clear_key))
        raw_upload = form_data.get(file_key)
        upload = raw_upload if isinstance(raw_upload, UploadFile) else None
        has_upload = _is_non_empty_upload(upload)
        if not has_upload:
            if clear_requested and merged.get(phase):
                _remove_old_bgm_file(merged[phase])
                merged[phase] = ""
                changes.append(f"{label}已清空")
            if upload:
                await upload.close()
            continue

        filename = str(upload.filename or "").strip()
        error = validators.validate_audio_file_meta(filename, upload.content_type)
        if error:
            await upload.close()
            raise ValueError(f"{label}上传失败：{error}")

        content = await upload.read()
        await upload.close()
        if len(content) > MAX_BGM_UPLOAD_BYTES:
            raise ValueError(f"{label}上传失败：文件大小不能超过 {MAX_BGM_UPLOAD_BYTES // (1024 * 1024)}MB")

        suffix = validators.normalize_audio_extension(filename)
        new_filename = f"{phase}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid4().hex}{suffix}"
        save_path = GAME_BGM_UPLOAD_DIR / new_filename
        save_path.write_bytes(content)
        new_url = f"/static/uploads/game_bgm/{new_filename}"

        old_url = merged.get(phase, "")
        if old_url and old_url != new_url:
            _remove_old_bgm_file(old_url)
        merged[phase] = new_url
        changes.append(f"{label}已更新")

    return merged, changes


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request) -> HTMLResponse:
    """系统配置主页。"""
    active_config_tab = _normalize_config_tab(request.query_params.get("tab"))
    context = await _build_config_page_context(
        request,
        active_config_tab=active_config_tab,
        saved=False,
    )
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
    rate_limit_payload = {
        "enabled": form_data.get("rate_limit_enabled") == "on",
        "trust_proxy_headers": form_data.get("rate_limit_trust_proxy_headers") == "on",
        "window_seconds": form_data.get("rate_limit_window_seconds", "60"),
        "max_requests": form_data.get("rate_limit_max_requests", "120"),
        "create_room_max_requests": form_data.get("rate_limit_create_room_max_requests", "20"),
        "join_room_max_requests": form_data.get("rate_limit_join_room_max_requests", "40"),
        "chat_api_max_requests": form_data.get("rate_limit_chat_api_max_requests", "30"),
    }
    current_game_bgm_config = await config_service.get_game_bgm_config()
    try:
        merged_game_bgm_config, game_bgm_changes = await _merge_game_bgm_uploads(form_data, current_game_bgm_config)
    except ValueError as exc:
        context = await _build_config_page_context(
            request,
            active_config_tab=active_config_tab,
            saved=False,
            config_error=str(exc),
        )
        return templates.TemplateResponse("pages/config.html", context)

    await config_service.save_smtp_config(smtp_payload)
    audit_actions = await config_service.save_audit_log_actions(selected_actions)
    await backup_service.save_backup_config(backup_payload)
    await config_service.save_base_url(base_url)
    await config_service.save_footer_copyright(footer_copyright_text, footer_copyright_url)
    await config_service.save_rate_limit_config(rate_limit_payload)
    rate_limit_service.invalidate_config_cache()

    # 保存清理配置
    cleanup_enabled = form_data.get("cleanup_enabled") == "on"
    cleanup_retention_days = int(form_data.get("cleanup_retention_days", "7") or "7")
    cleanup_interval_hours = int(form_data.get("cleanup_interval_hours", "24") or "24")
    cleanup_waiting_timeout_minutes = int(form_data.get("cleanup_waiting_timeout_minutes", "30") or "30")
    await cleanup_service.save_cleanup_config(
        enabled=cleanup_enabled,
        retention_days=cleanup_retention_days,
        interval_hours=cleanup_interval_hours,
        waiting_timeout_minutes=cleanup_waiting_timeout_minutes,
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
    game_role_balance_payload = {
        "pity_gap_threshold": form_data.get("role_pity_gap_threshold", "2"),
        "weight_base": form_data.get("role_weight_base", "100"),
        "weight_deficit_step": form_data.get("role_weight_deficit_step", "40"),
        "weight_zero_bonus": form_data.get("role_weight_zero_bonus", "60"),
    }
    await config_service.save_game_role_balance_config(game_role_balance_payload)
    await config_service.save_game_bgm_config(merged_game_bgm_config)

    restart_scheduler()

    context = await _build_config_page_context(
        request,
        active_config_tab=active_config_tab,
        saved=True,
        game_bgm_changes=game_bgm_changes,
    )
    detail = (
        f"更新系统配置（tab={active_config_tab}，含备份参数），日志类型："
        + ("、".join(config_service.AUDIT_ACTION_LABELS.get(item, item) for item in audit_actions) if audit_actions else "不记录")
    )
    if game_bgm_changes:
        detail += "；背景音乐：" + "，".join(game_bgm_changes)
    await log_service.record_request(
        request,
        action="update",
        module="config",
        target="系统配置",
        detail=detail,
    )
    return templates.TemplateResponse("pages/config.html", context)
