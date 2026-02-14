"""提示词模板后台控制器。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services import log_service, permission_decorator, prompt_templates_service

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
    """判断是否 HTMX 请求。"""
    return request.headers.get("hx-request", "").strip().lower() == "true"


def _build_form_payload(raw_payload: dict[str, Any]) -> dict[str, str]:
    """把表单输入规整为模板字段。"""
    return prompt_templates_service.normalize_template_payload(raw_payload)


def _validate_form_payload(payload: dict[str, str]) -> list[str]:
    """校验模板表单字段。"""
    errors: list[str] = []
    if not payload["name"]:
        errors.append("模板名称不能为空")
    if not payload["prompt_text"]:
        errors.append("提示词内容不能为空")
    if len(payload["prompt_text"]) > 4000:
        errors.append("提示词内容不能超过 4000 字")
    return errors


@router.get("/prompt_templates", response_class=HTMLResponse)
async def prompt_templates_page(request: Request) -> HTMLResponse:
    """提示词模板页面。"""
    items = await prompt_templates_service.list_items()
    await log_service.record_request(
        request,
        action="read",
        module="prompt_templates",
        target="提示词模板",
        detail="访问提示词模板页面",
    )
    return templates.TemplateResponse("pages/prompt_templates.html", {**base_context(request), "items": items})


@router.get("/prompt_templates/table", response_class=HTMLResponse)
async def prompt_templates_table(request: Request) -> HTMLResponse:
    """提示词模板表格 partial。"""
    items = await prompt_templates_service.list_items()
    return templates.TemplateResponse("partials/prompt_templates_table.html", {**base_context(request), "items": items})


@router.get("/prompt_templates/new", response_class=HTMLResponse)
async def prompt_templates_new(request: Request) -> HTMLResponse:
    """新建模板弹窗。"""
    return templates.TemplateResponse(
        "partials/prompt_templates_form.html",
        {
            **base_context(request),
            "mode": "create",
            "action": "/admin/prompt_templates",
            "errors": [],
            "form": {"status": "enabled"},
        },
    )


@router.post("/prompt_templates", response_class=HTMLResponse)
@permission_decorator.permission_meta("prompt_templates", "create")
async def prompt_templates_create(request: Request) -> HTMLResponse:
    """创建模板。"""
    form_data = await request.form()
    payload = _build_form_payload(dict(form_data))
    errors = _validate_form_payload(payload)

    existed = await prompt_templates_service.get_item_by_name(payload["name"])
    if existed:
        errors.append("模板名称已存在，请更换名称")

    if errors:
        error_status = 200 if _is_htmx_request(request) else 422
        return templates.TemplateResponse(
            "partials/prompt_templates_form.html",
            {
                **base_context(request),
                "mode": "create",
                "action": "/admin/prompt_templates",
                "errors": errors,
                "form": payload,
            },
            status_code=error_status,
        )

    created = await prompt_templates_service.create_item(payload)
    await log_service.record_request(
        request,
        action="create",
        module="prompt_templates",
        target="提示词模板",
        target_id=str(created.id),
        detail=f"创建提示词模板：{created.name}",
    )

    items = await prompt_templates_service.list_items()
    response = templates.TemplateResponse("partials/prompt_templates_table.html", {**base_context(request), "items": items})
    response.headers["HX-Retarget"] = "#prompt_templates-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {
            "admin-toast": {"title": "创建成功", "message": "提示词模板已创建", "variant": "success"},
            "rbac-close": True,
        },
        ensure_ascii=True,
    )
    return response


@router.post("/prompt_templates/seed-defaults", response_class=HTMLResponse)
@permission_decorator.permission_meta("prompt_templates", "create")
async def prompt_templates_seed_defaults(request: Request) -> HTMLResponse:
    """一键补齐内置模板。"""
    stats = await prompt_templates_service.seed_builtin_templates()
    await log_service.record_request(
        request,
        action="create",
        module="prompt_templates",
        target="提示词模板",
        detail=f"一键添加预置模板：新增 {stats['created']} 条，跳过 {stats['skipped']} 条",
    )

    items = await prompt_templates_service.list_items()
    response = templates.TemplateResponse("partials/prompt_templates_table.html", {**base_context(request), "items": items})
    response.headers["HX-Retarget"] = "#prompt_templates-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {
            "admin-toast": {
                "title": "已完成",
                "message": f"新增 {stats['created']} 条，已存在 {stats['skipped']} 条",
                "variant": "success",
            }
        },
        ensure_ascii=True,
    )
    return response


@router.get("/prompt_templates/{item_id}/edit", response_class=HTMLResponse)
async def prompt_templates_edit(request: Request, item_id: str) -> HTMLResponse:
    """编辑模板弹窗。"""
    item = await prompt_templates_service.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="模板不存在")

    return templates.TemplateResponse(
        "partials/prompt_templates_form.html",
        {
            **base_context(request),
            "mode": "edit",
            "action": f"/admin/prompt_templates/{item_id}",
            "errors": [],
            "form": {
                "name": item.name,
                "description": item.description,
                "prompt_text": item.prompt_text,
                "status": item.status,
            },
            "item": item,
        },
    )


@router.post("/prompt_templates/bulk-delete", response_class=HTMLResponse)
@permission_decorator.permission_meta("prompt_templates", "delete")
async def prompt_templates_bulk_delete(request: Request) -> HTMLResponse:
    """批量删除模板。"""
    form_data = await request.form()
    selected_ids = [str(value).strip() for value in form_data.getlist("selected_ids") if str(value).strip()]
    selected_ids = list(dict.fromkeys(selected_ids))

    deleted_count = 0
    skipped_count = 0
    for item_id in selected_ids:
        item = await prompt_templates_service.get_item(item_id)
        if not item:
            skipped_count += 1
            continue

        await prompt_templates_service.delete_item(item)
        deleted_count += 1
        await log_service.record_request(
            request,
            action="delete",
            module="prompt_templates",
            target="提示词模板",
            target_id=item_id,
            detail=f"批量删除提示词模板：{item.name}",
        )

    items = await prompt_templates_service.list_items()
    response = templates.TemplateResponse("partials/prompt_templates_table.html", {**base_context(request), "items": items})
    response.headers["HX-Retarget"] = "#prompt_templates-table"
    response.headers["HX-Reswap"] = "outerHTML"

    if deleted_count == 0:
        message = "未删除任何模板，请先勾选数据"
    elif skipped_count > 0:
        message = f"已删除 {deleted_count} 条，跳过 {skipped_count} 条"
    else:
        message = f"已删除 {deleted_count} 条模板"

    response.headers["HX-Trigger"] = json.dumps(
        {"admin-toast": {"title": "批量删除完成", "message": message, "variant": "warning"}},
        ensure_ascii=True,
    )
    return response


@router.post("/prompt_templates/{item_id}", response_class=HTMLResponse)
@permission_decorator.permission_meta("prompt_templates", "update")
async def prompt_templates_update(request: Request, item_id: str) -> HTMLResponse:
    """更新模板。"""
    item = await prompt_templates_service.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="模板不存在")

    form_data = await request.form()
    payload = _build_form_payload(dict(form_data))
    errors = _validate_form_payload(payload)

    existed = await prompt_templates_service.get_item_by_name(payload["name"])
    if existed and str(existed.id) != str(item.id):
        errors.append("模板名称已存在，请更换名称")

    if errors:
        error_status = 200 if _is_htmx_request(request) else 422
        return templates.TemplateResponse(
            "partials/prompt_templates_form.html",
            {
                **base_context(request),
                "mode": "edit",
                "action": f"/admin/prompt_templates/{item_id}",
                "errors": errors,
                "form": payload,
                "item": item,
            },
            status_code=error_status,
        )

    updated = await prompt_templates_service.update_item(item, payload)
    await log_service.record_request(
        request,
        action="update",
        module="prompt_templates",
        target="提示词模板",
        target_id=item_id,
        detail=f"更新提示词模板：{updated.name}",
    )

    items = await prompt_templates_service.list_items()
    response = templates.TemplateResponse("partials/prompt_templates_table.html", {**base_context(request), "items": items})
    response.headers["HX-Retarget"] = "#prompt_templates-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {
            "admin-toast": {"title": "更新成功", "message": "提示词模板已更新", "variant": "success"},
            "rbac-close": True,
        },
        ensure_ascii=True,
    )
    return response


@router.delete("/prompt_templates/{item_id}", response_class=HTMLResponse)
@permission_decorator.permission_meta("prompt_templates", "delete")
async def prompt_templates_delete(request: Request, item_id: str) -> HTMLResponse:
    """删除模板。"""
    item = await prompt_templates_service.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="模板不存在")

    item_name = item.name
    await prompt_templates_service.delete_item(item)
    await log_service.record_request(
        request,
        action="delete",
        module="prompt_templates",
        target="提示词模板",
        target_id=item_id,
        detail=f"删除提示词模板：{item_name}",
    )

    items = await prompt_templates_service.list_items()
    response = templates.TemplateResponse("partials/prompt_templates_table.html", {**base_context(request), "items": items})
    response.headers["HX-Retarget"] = "#prompt_templates-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {"admin-toast": {"title": "已删除", "message": "模板已删除", "variant": "warning"}},
        ensure_ascii=True,
    )
    return response
