"""管理员管理控制器。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from beanie import PydanticObjectId
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services import admin_user_service, auth_service, log_service, permission_decorator, role_service, validators

BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(prefix="/admin")


def fmt_dt(value: datetime | None) -> str:
    if not value:
        return ""
    if value.tzinfo is None:
        return value.strftime("%Y-%m-%d %H:%M")
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


templates.env.filters["fmt_dt"] = fmt_dt

STATUS_META: dict[str, dict[str, str]] = {
    "enabled": {"label": "启用", "color": "#2f855a"},
    "disabled": {"label": "禁用", "color": "#b7791f"},
}

ADMIN_SORT_OPTIONS: dict[str, str] = {
    "updated_desc": "最近更新",
    "updated_asc": "最早更新",
    "username_asc": "账号 A-Z",
}

ADMIN_PAGE_SIZE = 10

def base_context(request: Request) -> dict[str, Any]:
    return {
        "request": request,
        "current_admin": request.session.get("admin_name"),
    }


def _is_htmx_request(request: Request) -> bool:
    """判断是否为 HTMX 请求，用于区分表单错误返回策略。"""
    return request.headers.get("hx-request", "").strip().lower() == "true"


def build_form_data(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": values.get("username", ""),
        "display_name": values.get("display_name", ""),
        "email": values.get("email", ""),
        "role_slug": values.get("role_slug", "admin"),
        "status": values.get("status", "enabled"),
        "password": values.get("password", ""),
    }


def form_errors(values: dict[str, Any], is_create: bool, role_slugs: set[str]) -> list[str]:
    """统一校验管理员表单字段，降低二开重复校验成本。"""

    errors: list[str] = []

    username_error = validators.validate_admin_username(str(values.get("username", "")))
    if username_error:
        errors.append(username_error)

    email_error = validators.validate_optional_email(str(values.get("email", "")))
    if email_error:
        errors.append(email_error)

    if len(str(values.get("display_name", ""))) < 2:
        errors.append("显示名称至少 2 个字符")
    if values.get("status") not in STATUS_META:
        errors.append("状态不合法")
    if role_slugs and values.get("role_slug") not in role_slugs:
        errors.append("角色不合法")
    if is_create and len(values.get("password", "")) < 6:
        errors.append("初始密码至少 6 位")
    return errors


def parse_positive_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def parse_admin_filters(values: Mapping[str, Any]) -> tuple[dict[str, str], int]:
    search_q = str(values.get("search_q") or values.get("q") or "").strip()
    search_role = str(values.get("search_role") or "").strip()
    search_status = str(values.get("search_status") or "").strip()
    if search_status not in STATUS_META:
        search_status = ""

    search_sort = str(values.get("search_sort") or "updated_desc").strip()
    if search_sort not in ADMIN_SORT_OPTIONS:
        search_sort = "updated_desc"

    page = parse_positive_int(values.get("page"), default=1)
    return (
        {
            "search_q": search_q,
            "search_role": search_role,
            "search_status": search_status,
            "search_sort": search_sort,
        },
        page,
    )


def build_pagination(total: int, page: int, page_size: int) -> dict[str, Any]:
    total_pages = max((total + page_size - 1) // page_size, 1)
    current = min(max(page, 1), total_pages)
    start_page = max(current - 2, 1)
    end_page = min(start_page + 4, total_pages)
    start_page = max(end_page - 4, 1)

    if total == 0:
        start_item = 0
        end_item = 0
    else:
        start_item = (current - 1) * page_size + 1
        end_item = min(current * page_size, total)

    return {
        "page": current,
        "page_size": page_size,
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


def filter_admin_items(items: list[Any], filters: dict[str, str]) -> list[Any]:
    filtered = items
    if filters["search_role"]:
        filtered = [item for item in filtered if item.role_slug == filters["search_role"]]
    if filters["search_status"]:
        filtered = [item for item in filtered if item.status == filters["search_status"]]

    sort_key = filters["search_sort"]
    if sort_key == "updated_asc":
        filtered = sorted(filtered, key=lambda item: item.updated_at)
    elif sort_key == "username_asc":
        filtered = sorted(filtered, key=lambda item: item.username.lower())
    else:
        filtered = sorted(filtered, key=lambda item: item.updated_at, reverse=True)
    return filtered


async def read_request_values(request: Request) -> dict[str, str]:
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


async def build_admin_table_context(
    request: Request,
    filters: dict[str, str],
    page: int,
) -> dict[str, Any]:
    roles = await role_service.list_roles()
    role_map = {item.slug: item.name for item in roles}
    items = await admin_user_service.list_admins(filters["search_q"] or None)
    filtered_items = filter_admin_items(items, filters)
    pagination = build_pagination(len(filtered_items), page, ADMIN_PAGE_SIZE)
    start = (pagination["page"] - 1) * ADMIN_PAGE_SIZE
    paged_items = filtered_items[start : start + ADMIN_PAGE_SIZE]

    return {
        **base_context(request),
        "items": paged_items,
        "status_meta": STATUS_META,
        "role_map": role_map,
        "roles": roles,
        "filters": filters,
        "pagination": pagination,
    }


@router.get("/users", response_class=HTMLResponse)
async def admin_users_page(request: Request) -> HTMLResponse:
    filters, page = parse_admin_filters(request.query_params)
    context = await build_admin_table_context(request, filters, page)
    context["admin_sort_options"] = ADMIN_SORT_OPTIONS
    await log_service.record_request(
        request,
        action="read",
        module="admin_users",
        target="管理员账号",
        detail="访问管理员管理页面",
    )
    return templates.TemplateResponse("pages/admin_users.html", context)


@router.get("/users/table", response_class=HTMLResponse)
async def admin_users_table(request: Request) -> HTMLResponse:
    filters, page = parse_admin_filters(request.query_params)
    context = await build_admin_table_context(request, filters, page)
    return templates.TemplateResponse("partials/admin_users_table.html", context)


@router.get("/users/new", response_class=HTMLResponse)
async def admin_users_new(request: Request) -> HTMLResponse:
    roles = await role_service.list_roles()
    default_slug = roles[0].slug if roles else "admin"
    form = build_form_data({"role_slug": default_slug})
    filters, page = parse_admin_filters(request.query_params)
    context = {
        **base_context(request),
        "mode": "create",
        "action": "/admin/users",
        "form": form,
        "errors": [],
        "status_meta": STATUS_META,
        "roles": roles,
        "filters": filters,
        "page": page,
    }
    return templates.TemplateResponse("partials/admin_users_form.html", context)


@router.get("/users/{item_id}/edit", response_class=HTMLResponse)
async def admin_users_edit(request: Request, item_id: PydanticObjectId) -> HTMLResponse:
    item = await admin_user_service.get_admin(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="账号不存在")

    roles = await role_service.list_roles()
    form = build_form_data(
        {
            "username": item.username,
            "display_name": item.display_name,
            "email": item.email,
            "role_slug": item.role_slug,
            "status": item.status,
            "password": "",
        }
    )
    filters, page = parse_admin_filters(request.query_params)
    context = {
        **base_context(request),
        "mode": "edit",
        "action": f"/admin/users/{item_id}",
        "form": form,
        "errors": [],
        "status_meta": STATUS_META,
        "roles": roles,
        "filters": filters,
        "page": page,
    }
    return templates.TemplateResponse("partials/admin_users_form.html", context)


@router.post("/users", response_class=HTMLResponse)
@permission_decorator.permission_meta("admin_users", "create")
async def admin_users_create(
    request: Request,
    username: str = Form(""),
    display_name: str = Form(""),
    email: str = Form(""),
    role_slug: str = Form("admin"),
    status: str = Form("enabled"),
    password: str = Form(""),
) -> HTMLResponse:
    request_values = await read_request_values(request)
    filters, page = parse_admin_filters(request_values)
    roles = await role_service.list_roles()
    role_slugs = {item.slug for item in roles}
    form = build_form_data(
        {
            "username": validators.normalize_admin_username(username),
            "display_name": display_name.strip(),
            "email": validators.normalize_email(email),
            "role_slug": role_slug,
            "status": status,
            "password": password,
        }
    )

    errors = form_errors(form, is_create=True, role_slugs=role_slugs)
    if await admin_user_service.get_admin_by_username(form["username"]):
        errors.append("账号已存在")

    if errors:
        context = {
            **base_context(request),
            "mode": "create",
            "action": "/admin/users",
            "form": form,
            "errors": errors,
            "status_meta": STATUS_META,
            "roles": roles,
            "filters": filters,
            "page": page,
        }
        error_status = 200 if _is_htmx_request(request) else 422
        return templates.TemplateResponse(
            "partials/admin_users_form.html", context, status_code=error_status
        )

    payload = {
        "username": form["username"],
        "display_name": form["display_name"],
        "email": form["email"],
        "role_slug": form["role_slug"],
        "status": form["status"],
        "password_hash": auth_service.hash_password(form["password"]),
    }
    created = await admin_user_service.create_admin(payload)
    await log_service.record_request(
        request,
        action="create",
        module="admin_users",
        target=f"管理员: {created.display_name}",
        target_id=str(created.id),
        detail=f"创建管理员账号 {created.username}",
    )

    context = await build_admin_table_context(request, filters, page)
    response = templates.TemplateResponse("partials/admin_users_table.html", context)
    response.headers["HX-Retarget"] = "#admin-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {
            "admin-toast": {
                "title": "已创建",
                "message": "管理员账号已保存",
                "variant": "success",
            },
            "rbac-close": True,
        },
        ensure_ascii=True,
    )
    return response


@router.post("/users/bulk-delete", response_class=HTMLResponse)
@permission_decorator.permission_meta("admin_users", "delete")
async def admin_users_bulk_delete(request: Request) -> HTMLResponse:
    """批量删除管理员账号。"""

    request_values = await read_request_values(request)
    filters, page = parse_admin_filters(request_values)
    form_data = await request.form()
    selected_ids = [str(item).strip() for item in form_data.getlist("selected_ids") if str(item).strip()]
    selected_ids = list(dict.fromkeys(selected_ids))

    current_admin_id = str(request.session.get("admin_id") or "")
    deleted_count = 0
    skipped_self = 0
    skipped_invalid = 0

    for raw_id in selected_ids:
        try:
            object_id = PydanticObjectId(raw_id)
        except Exception:
            skipped_invalid += 1
            continue

        item = await admin_user_service.get_admin(object_id)
        if not item:
            skipped_invalid += 1
            continue

        if str(item.id) == current_admin_id:
            skipped_self += 1
            continue

        await admin_user_service.delete_admin(item)
        deleted_count += 1
        await log_service.record_request(
            request,
            action="delete",
            module="admin_users",
            target=f"管理员: {item.display_name}",
            target_id=str(item.id),
            detail=f"批量删除管理员账号 {item.username}",
        )

    context = await build_admin_table_context(request, filters, page)
    response = templates.TemplateResponse("partials/admin_users_table.html", context)
    response.headers["HX-Retarget"] = "#admin-table"
    response.headers["HX-Reswap"] = "outerHTML"

    if deleted_count == 0:
        toast_message = "未删除任何账号，请先勾选记录"
    else:
        extras: list[str] = []
        if skipped_self:
            extras.append(f"跳过当前账号 {skipped_self} 条")
        if skipped_invalid:
            extras.append(f"跳过无效记录 {skipped_invalid} 条")
        suffix = f"（{'，'.join(extras)}）" if extras else ""
        toast_message = f"已删除 {deleted_count} 条管理员账号{suffix}"

    response.headers["HX-Trigger"] = json.dumps(
        {
            "admin-toast": {
                "title": "批量删除完成",
                "message": toast_message,
                "variant": "warning",
            }
        },
        ensure_ascii=True,
    )
    return response


@router.post("/users/{item_id}", response_class=HTMLResponse)
@permission_decorator.permission_meta("admin_users", "update")
async def admin_users_update(
    request: Request,
    item_id: PydanticObjectId,
    display_name: str = Form(""),
    email: str = Form(""),
    role_slug: str = Form("admin"),
    status: str = Form("enabled"),
    password: str = Form(""),
) -> HTMLResponse:
    request_values = await read_request_values(request)
    filters, page = parse_admin_filters(request_values)
    item = await admin_user_service.get_admin(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="账号不存在")

    roles = await role_service.list_roles()
    role_slugs = {item.slug for item in roles}
    form = build_form_data(
        {
            "username": item.username,
            "display_name": display_name.strip(),
            "email": validators.normalize_email(email),
            "role_slug": role_slug,
            "status": status,
            "password": password,
        }
    )

    errors = form_errors(form, is_create=False, role_slugs=role_slugs)
    if errors:
        context = {
            **base_context(request),
            "mode": "edit",
            "action": f"/admin/users/{item_id}",
            "form": form,
            "errors": errors,
            "status_meta": STATUS_META,
            "roles": roles,
            "filters": filters,
            "page": page,
        }
        error_status = 200 if _is_htmx_request(request) else 422
        return templates.TemplateResponse(
            "partials/admin_users_form.html", context, status_code=error_status
        )

    payload = {
        "display_name": form["display_name"],
        "email": form["email"],
        "role_slug": form["role_slug"],
        "status": form["status"],
        "password_hash": auth_service.hash_password(form["password"]) if form["password"] else "",
    }
    await admin_user_service.update_admin(item, payload)
    await log_service.record_request(
        request,
        action="update",
        module="admin_users",
        target=f"管理员: {item.display_name}",
        target_id=str(item.id),
        detail=f"更新管理员账号 {item.username}",
    )
    if str(item.id) == str(request.session.get("admin_id")):
        request.session["admin_name"] = item.display_name

    context = await build_admin_table_context(request, filters, page)
    response = templates.TemplateResponse("partials/admin_users_table.html", context)
    response.headers["HX-Retarget"] = "#admin-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {
            "admin-toast": {
                "title": "已更新",
                "message": "管理员账号已修改",
                "variant": "success",
            },
            "rbac-close": True,
        },
        ensure_ascii=True,
    )
    return response


@router.delete("/users/{item_id}", response_class=HTMLResponse)
@permission_decorator.permission_meta("admin_users", "delete")
async def admin_users_delete(request: Request, item_id: PydanticObjectId) -> HTMLResponse:
    request_values = await read_request_values(request)
    filters, page = parse_admin_filters(request_values)
    item = await admin_user_service.get_admin(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="账号不存在")

    if str(item.id) == str(request.session.get("admin_id")):
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")

    await admin_user_service.delete_admin(item)
    await log_service.record_request(
        request,
        action="delete",
        module="admin_users",
        target=f"管理员: {item.display_name}",
        target_id=str(item.id),
        detail=f"删除管理员账号 {item.username}",
    )
    context = await build_admin_table_context(request, filters, page)
    response = templates.TemplateResponse("partials/admin_users_table.html", context)
    response.headers["HX-Retarget"] = "#admin-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {
            "admin-toast": {
                "title": "已删除",
                "message": "管理员账号已移除",
                "variant": "warning",
            }
        },
        ensure_ascii=True,
    )
    return response
