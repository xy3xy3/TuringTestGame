"""AI模型配置 控制器。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services import ai_models_service, log_service, permission_decorator

BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(prefix="/admin")


def base_context(request: Request) -> dict[str, Any]:
    """构建模板基础上下文。"""

    return {
        "request": request,
        "current_admin": request.session.get("admin_name"),
    }


def _is_htmx_request(request: Request) -> bool:
    """判断是否为 HTMX 请求，用于区分表单错误返回策略。"""

    return request.headers.get("hx-request", "").strip().lower() == "true"


def parse_ai_model_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    """标准化 AI 模型表单数据，确保复选框字段始终可被保存。"""

    payload = dict(raw_payload)
    payload["name"] = str(payload.get("name", "")).strip()
    payload["base_url"] = str(payload.get("base_url", "")).strip()
    payload["api_key"] = str(payload.get("api_key", "")).strip()
    payload["model_name"] = str(payload.get("model_name", "")).strip()
    payload["temperature"] = str(payload.get("temperature", "0.8")).strip() or "0.8"
    payload["max_tokens"] = str(payload.get("max_tokens", "500")).strip() or "500"
    payload["description"] = str(payload.get("description", "")).strip()
    payload["is_enabled"] = "true" if str(payload.get("is_enabled", "")).strip().lower() == "true" else "false"
    payload["is_default"] = "true" if str(payload.get("is_default", "")).strip().lower() == "true" else "false"
    return payload


def validate_ai_model_payload(payload: dict[str, Any]) -> list[str]:
    """校验 AI 模型表单必填字段。"""

    errors: list[str] = []
    if not payload["name"]:
        errors.append("名称不能为空")
    if not payload["base_url"]:
        errors.append("Base URL 不能为空")
    if not payload["api_key"]:
        errors.append("API Key 不能为空")
    if not payload["model_name"]:
        errors.append("模型名称不能为空")
    return errors


@router.get("/ai_models", response_class=HTMLResponse)
async def ai_models_page(request: Request) -> HTMLResponse:
    """模块列表页。"""

    items = await ai_models_service.list_ai_models()
    await log_service.record_request(
        request,
        action="read",
        module="ai_models",
        target="AI模型配置",
        detail="访问模块列表页面",
    )
    return templates.TemplateResponse("pages/ai_models.html", {**base_context(request), "items": items})


@router.get("/ai_models/table", response_class=HTMLResponse)
async def ai_models_table(request: Request) -> HTMLResponse:
    """模块表格 partial。"""

    items = await ai_models_service.list_ai_models()
    return templates.TemplateResponse("partials/ai_models_table.html", {**base_context(request), "items": items})


@router.get("/ai_models/new", response_class=HTMLResponse)
async def ai_models_new(request: Request) -> HTMLResponse:
    """新建弹窗。"""

    return templates.TemplateResponse(
        "partials/ai_models_form.html",
        {**base_context(request), "mode": "create", "action": "/admin/ai_models", "errors": [], "form": {}},
    )


@router.post("/ai_models", response_class=HTMLResponse)
@permission_decorator.permission_meta("ai_models", "create")
async def ai_models_create(request: Request) -> HTMLResponse:
    """创建 AI 模型配置。"""

    form_data = await request.form()
    payload = parse_ai_model_payload(dict(form_data))
    errors = validate_ai_model_payload(payload)

    if errors:
        context = {
            **base_context(request),
            "mode": "create",
            "action": "/admin/ai_models",
            "errors": errors,
            "form": payload,
        }
        error_status = 200 if _is_htmx_request(request) else 422
        return templates.TemplateResponse("partials/ai_models_form.html", context, status_code=error_status)

    created = await ai_models_service.create_ai_model(payload)
    await log_service.record_request(
        request,
        action="create",
        module="ai_models",
        target="AI模型配置",
        target_id=str(created.id),
        detail=f"创建AI模型: {created.name}",
    )

    items = await ai_models_service.list_ai_models()
    response = templates.TemplateResponse("partials/ai_models_table.html", {**base_context(request), "items": items})
    response.headers["HX-Retarget"] = "#ai_models-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {
            "admin-toast": {"title": "已创建", "message": "AI模型创建成功", "variant": "success"},
            "rbac-close": True,
        },
        ensure_ascii=True,
    )
    return response


@router.get("/ai_models/{item_id}/edit", response_class=HTMLResponse)
async def ai_models_edit(request: Request, item_id: str) -> HTMLResponse:
    """编辑弹窗。"""

    item = await ai_models_service.get_ai_model_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")

    form_data = {
        "name": item.name,
        "base_url": item.base_url,
        "api_key": item.api_key,
        "model_name": item.model_name,
        "temperature": item.temperature,
        "max_tokens": item.max_tokens,
        "is_enabled": "true" if item.is_enabled else "false",
        "is_default": "true" if item.is_default else "false",
        "description": item.description,
    }
    return templates.TemplateResponse(
        "partials/ai_models_form.html",
        {
            **base_context(request),
            "mode": "edit",
            "action": f"/admin/ai_models/{item_id}",
            "errors": [],
            "form": form_data,
            "item": item,
        },
    )


@router.post("/ai_models/bulk-delete", response_class=HTMLResponse)
@permission_decorator.permission_meta("ai_models", "delete")
async def ai_models_bulk_delete(request: Request) -> HTMLResponse:
    """批量删除数据。"""

    form_data = await request.form()
    selected_ids = [str(item).strip() for item in form_data.getlist("selected_ids") if str(item).strip()]
    selected_ids = list(dict.fromkeys(selected_ids))

    deleted_count = 0
    skipped_count = 0
    for item_id in selected_ids:
        item = await ai_models_service.get_ai_model_by_id(item_id)
        if not item:
            skipped_count += 1
            continue

        await ai_models_service.delete_ai_model(item)
        deleted_count += 1
        await log_service.record_request(
            request,
            action="delete",
            module="ai_models",
            target="AI模型配置",
            target_id=item_id,
            detail=f"批量删除AI模型: {item.name}",
        )

    items = await ai_models_service.list_ai_models()
    response = templates.TemplateResponse("partials/ai_models_table.html", {**base_context(request), "items": items})
    response.headers["HX-Retarget"] = "#ai_models-table"
    response.headers["HX-Reswap"] = "outerHTML"

    if deleted_count == 0:
        message = "未删除任何记录，请先勾选数据"
    elif skipped_count > 0:
        message = f"已删除 {deleted_count} 条，跳过 {skipped_count} 条"
    else:
        message = f"已批量删除 {deleted_count} 条记录"

    response.headers["HX-Trigger"] = json.dumps(
        {"admin-toast": {"title": "批量删除完成", "message": message, "variant": "warning"}},
        ensure_ascii=True,
    )
    return response


@router.post("/ai_models/{item_id}", response_class=HTMLResponse)
@permission_decorator.permission_meta("ai_models", "update")
async def ai_models_update(request: Request, item_id: str) -> HTMLResponse:
    """更新 AI 模型配置。"""

    item = await ai_models_service.get_ai_model_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")

    form_data = await request.form()
    payload = parse_ai_model_payload(dict(form_data))
    errors = validate_ai_model_payload(payload)

    if errors:
        context = {
            **base_context(request),
            "mode": "edit",
            "action": f"/admin/ai_models/{item_id}",
            "errors": errors,
            "form": payload,
        }
        error_status = 200 if _is_htmx_request(request) else 422
        return templates.TemplateResponse("partials/ai_models_form.html", context, status_code=error_status)

    await ai_models_service.update_ai_model(item, payload)
    await log_service.record_request(
        request,
        action="update",
        module="ai_models",
        target="AI模型配置",
        target_id=item_id,
        detail=f"更新AI模型: {item.name}",
    )

    items = await ai_models_service.list_ai_models()
    response = templates.TemplateResponse("partials/ai_models_table.html", {**base_context(request), "items": items})
    response.headers["HX-Retarget"] = "#ai_models-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {
            "admin-toast": {"title": "已更新", "message": "AI模型更新成功", "variant": "success"},
            "rbac-close": True,
        },
        ensure_ascii=True,
    )
    return response


@router.post("/ai_models/{item_id}/toggle", response_class=HTMLResponse)
@permission_decorator.permission_meta("ai_models", "update")
async def ai_models_toggle(request: Request, item_id: str) -> HTMLResponse:
    """切换 AI 模型启用状态。"""

    item = await ai_models_service.get_ai_model_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")

    await ai_models_service.toggle_ai_model(item)
    await log_service.record_request(
        request,
        action="update",
        module="ai_models",
        target="AI模型配置",
        target_id=item_id,
        detail=f"切换AI模型状态: {item.name} -> {item.is_enabled}",
    )

    items = await ai_models_service.list_ai_models()
    return templates.TemplateResponse("partials/ai_models_table.html", {**base_context(request), "items": items})


@router.delete("/ai_models/{item_id}", response_class=HTMLResponse)
@permission_decorator.permission_meta("ai_models", "delete")
async def ai_models_delete(request: Request, item_id: str) -> HTMLResponse:
    """删除数据。"""

    item = await ai_models_service.get_ai_model_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")

    item_name = item.name
    await ai_models_service.delete_ai_model(item)
    await log_service.record_request(
        request,
        action="delete",
        module="ai_models",
        target="AI模型配置",
        target_id=item_id,
        detail=f"删除AI模型: {item_name}",
    )

    items = await ai_models_service.list_ai_models()
    response = templates.TemplateResponse("partials/ai_models_table.html", {**base_context(request), "items": items})
    response.headers["HX-Retarget"] = "#ai_models-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {"admin-toast": {"title": "已删除", "message": "记录已删除", "variant": "warning"}},
        ensure_ascii=True,
    )
    return response
