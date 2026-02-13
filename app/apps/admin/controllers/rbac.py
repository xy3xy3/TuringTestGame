"""Admin RBAC 控制器。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.apps.admin.registry import ADMIN_TREE, iter_assignable_leaf_nodes
from app.services import admin_user_service, log_service, permission_decorator, role_service, validators

BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(prefix="/admin")


def fmt_dt(value: datetime | None) -> str:
    """格式化日期，统一在页面展示短时间文本。"""

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

ROLE_SORT_OPTIONS: dict[str, str] = {
    "updated_desc": "最近更新",
    "updated_asc": "最早更新",
    "slug_asc": "标识 A-Z",
}

ROLE_PAGE_SIZE = 10

ACTION_LABELS = {
    "create": "新增",
    "read": "查看",
    "update": "编辑",
    "delete": "删除",
    "trigger": "触发",
    "restore": "恢复",
    "update_self": "修改本人",
}


def build_role_permission_tree() -> list[dict[str, Any]]:
    """构建可分配权限树，自动剔除不可分配资源。"""

    def filter_node(node: dict[str, Any]) -> dict[str, Any] | None:
        children = node.get("children")
        if isinstance(children, list) and children:
            filtered_children = [
                child
                for child in (filter_node(item) for item in children)
                if child is not None
            ]
            if not filtered_children:
                return None
            return {
                **node,
                "children": filtered_children,
            }

        if not bool(node.get("assignable", True)):
            return None
        return dict(node)

    return [
        item
        for item in (filter_node(node) for node in ADMIN_TREE)
        if item is not None
    ]


ROLE_PERMISSION_TREE = build_role_permission_tree()


def base_context(request: Request) -> dict[str, Any]:
    """构建模板基础上下文。"""

    return {
        "request": request,
        "current_admin": request.session.get("admin_name"),
    }


def _is_htmx_request(request: Request) -> bool:
    """判断是否为 HTMX 请求，用于区分表单错误返回策略。"""

    return request.headers.get("hx-request", "").strip().lower() == "true"


def build_role_form(values: dict[str, Any]) -> dict[str, Any]:
    """构建角色表单默认值。"""

    return {
        "name": values.get("name", ""),
        "slug": values.get("slug", ""),
        "status": values.get("status", "enabled"),
        "description": values.get("description", ""),
    }


def build_import_form(values: dict[str, Any]) -> dict[str, Any]:
    """构建角色导入表单默认值。"""

    allow_system_raw = str(values.get("allow_system", "")).strip().lower()
    allow_system = allow_system_raw in {"1", "true", "on", "yes"}
    return {
        "payload": values.get("payload", ""),
        "allow_system": allow_system,
    }


def parse_positive_int(value: Any, default: int = 1) -> int:
    """安全解析正整数参数。"""

    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def parse_role_filters(values: Mapping[str, Any]) -> tuple[dict[str, str], int]:
    """解析角色列表筛选条件。"""

    search_q = str(values.get("search_q") or values.get("q") or "").strip()
    search_status = str(values.get("search_status") or "").strip()
    if search_status not in STATUS_META:
        search_status = ""

    search_sort = str(values.get("search_sort") or "updated_desc").strip()
    if search_sort not in ROLE_SORT_OPTIONS:
        search_sort = "updated_desc"

    page = parse_positive_int(values.get("page"), default=1)
    return (
        {
            "search_q": search_q,
            "search_status": search_status,
            "search_sort": search_sort,
        },
        page,
    )


def build_pagination(total: int, page: int, page_size: int) -> dict[str, Any]:
    """构建分页结构，供模板渲染页码。"""

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


def filter_roles(roles: list[Any], filters: dict[str, str]) -> list[Any]:
    """按关键词、状态、排序筛选角色列表。"""

    filtered = roles
    if filters["search_q"]:
        keyword = filters["search_q"].lower()
        filtered = [
            item
            for item in filtered
            if keyword in item.slug.lower()
            or keyword in item.name.lower()
            or keyword in (item.description or "").lower()
        ]
    if filters["search_status"]:
        filtered = [item for item in filtered if item.status == filters["search_status"]]

    sort_key = filters["search_sort"]
    if sort_key == "updated_asc":
        filtered = sorted(filtered, key=lambda item: item.updated_at)
    elif sort_key == "slug_asc":
        filtered = sorted(filtered, key=lambda item: item.slug.lower())
    else:
        filtered = sorted(filtered, key=lambda item: item.updated_at, reverse=True)
    return filtered


async def read_request_values(request: Request) -> dict[str, str]:
    """统一读取 Query + Form 参数，兼容 HTMX 请求。"""

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


async def build_role_table_context(
    request: Request,
    filters: dict[str, str],
    page: int,
) -> dict[str, Any]:
    """构建角色表格上下文。"""

    roles = await role_service.list_roles()
    filtered_roles = filter_roles(roles, filters)
    pagination = build_pagination(len(filtered_roles), page, ROLE_PAGE_SIZE)
    start = (pagination["page"] - 1) * ROLE_PAGE_SIZE
    paged_roles = filtered_roles[start : start + ROLE_PAGE_SIZE]

    return {
        **base_context(request),
        "roles": paged_roles,
        "status_meta": STATUS_META,
        "filters": filters,
        "pagination": pagination,
    }


def build_checked_map(form_data: Any) -> dict[str, set[str]]:
    """从表单解析权限勾选状态。"""

    checked_map: dict[str, set[str]] = {}
    for node in iter_assignable_leaf_nodes(ADMIN_TREE):
        actions = form_data.getlist(f"perm_{node['key']}")
        if actions:
            checked_map[node["key"]] = set(actions)
    return checked_map


def build_permissions(form_data: Any, owner: str) -> list[dict[str, Any]]:
    """将表单勾选项转换为权限列表，并统一补齐 read 依赖。"""

    permissions: list[dict[str, Any]] = []
    for node in iter_assignable_leaf_nodes(ADMIN_TREE):
        allowed_actions = set(node.get("actions", []))
        actions = [
            str(action)
            for action in form_data.getlist(f"perm_{node['key']}")
            if str(action) in allowed_actions
        ]
        if (
            bool(node.get("require_read", True))
            and "read" in allowed_actions
            and any(action != "read" for action in actions)
            and "read" not in actions
        ):
            actions.append("read")

        for action in actions:
            description = f"{node['name']} | {node['url']}"
            permissions.append(
                {
                    "resource": node["key"],
                    "action": action,
                    "priority": 3,
                    "status": "enabled",
                    "owner": owner,
                    "tags": [],
                    "description": description,
                }
            )
    return permissions


def build_checked_map_from_permissions(permissions: list[Any]) -> dict[str, set[str]]:
    """将角色权限列表转换为模板可用的勾选映射。"""

    checked_map: dict[str, set[str]] = {}
    for item in permissions:
        resource = getattr(item, "resource", None) or item.get("resource")
        action = getattr(item, "action", None) or item.get("action")
        if not resource or not action:
            continue
        checked_map.setdefault(resource, set()).add(action)
    return checked_map


def role_errors(values: dict[str, Any]) -> list[str]:
    """校验角色基础字段，避免 slug 非法导致路由异常。"""

    errors: list[str] = []
    if len(values.get("name", "")) < 2:
        errors.append("角色名称至少 2 个字符")

    slug_error = validators.validate_role_slug(str(values.get("slug", "")))
    if slug_error:
        errors.append(slug_error)

    if values.get("status") not in STATUS_META:
        errors.append("状态不合法")
    return errors


def build_import_errors(payload: str) -> list[str]:
    """校验导入表单基础字段。"""

    errors: list[str] = []
    if not payload.strip():
        errors.append("请粘贴角色导入 JSON")
    return errors


def build_import_summary_message(summary: dict[str, Any]) -> str:
    """构建导入结果描述，便于 toast 与日志复用。"""

    return (
        f"总计 {summary['total']}，新增 {summary['created']}，"
        f"更新 {summary['updated']}，跳过 {summary['skipped']}"
    )


@router.get("/", response_class=HTMLResponse)
async def admin_root() -> RedirectResponse:
    """后台首页重定向到仪表盘。"""

    return RedirectResponse(url="/admin/dashboard", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request) -> HTMLResponse:
    """仪表盘页面。"""

    roles = await role_service.list_roles()
    admins = await admin_user_service.list_admins()
    dashboard = {
        "role_total": len(roles),
        "role_enabled": sum(1 for item in roles if item.status == "enabled"),
        "role_disabled": sum(1 for item in roles if item.status == "disabled"),
        "admin_total": len(admins),
        "admin_enabled": sum(1 for item in admins if item.status == "enabled"),
        "admin_disabled": sum(1 for item in admins if item.status == "disabled"),
        "latest_role": fmt_dt(roles[0].updated_at) if roles else "暂无",
        "latest_admin": fmt_dt(admins[0].updated_at) if admins else "暂无",
    }
    context = {
        **base_context(request),
        "dashboard": dashboard,
    }
    return templates.TemplateResponse("pages/dashboard.html", context)


@router.get("/rbac", response_class=HTMLResponse)
async def rbac_page(request: Request) -> HTMLResponse:
    """RBAC 页面。"""

    filters, page = parse_role_filters(request.query_params)
    context = await build_role_table_context(request, filters, page)
    context["action_labels"] = ACTION_LABELS
    context["role_sort_options"] = ROLE_SORT_OPTIONS
    await log_service.record_request(
        request,
        action="read",
        module="rbac",
        target="角色与权限",
        detail="访问 RBAC 角色列表页面",
    )
    return templates.TemplateResponse("pages/rbac.html", context)


@router.get("/rbac/roles/table", response_class=HTMLResponse)
async def role_table(request: Request) -> HTMLResponse:
    """角色表格 partial。"""

    filters, page = parse_role_filters(request.query_params)
    context = await build_role_table_context(request, filters, page)
    return templates.TemplateResponse("partials/role_table.html", context)


@router.get("/rbac/roles/export")
@permission_decorator.permission_meta("rbac", "read")
async def role_export(request: Request, include_system: str = "1") -> JSONResponse:
    """导出角色权限 JSON，便于跨项目迁移。"""

    include_system_value = include_system.strip().lower() in {"1", "true", "yes", "on"}
    payload = await role_service.export_roles_payload(include_system=include_system_value)
    filename = f"roles-export-{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    await log_service.record_request(
        request,
        action="read",
        module="rbac",
        target="角色与权限",
        detail=f"导出角色权限配置（含系统角色：{'是' if include_system_value else '否'}）",
    )
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/rbac/roles/import", response_class=HTMLResponse)
@permission_decorator.permission_meta("rbac", "update")
async def role_import_form(request: Request) -> HTMLResponse:
    """加载角色导入弹窗。"""

    filters, page = parse_role_filters(request.query_params)
    context = {
        **base_context(request),
        "form": build_import_form({}),
        "errors": [],
        "filters": filters,
        "page": page,
    }
    return templates.TemplateResponse("partials/role_import_form.html", context)


@router.post("/rbac/roles/import", response_class=HTMLResponse)
@permission_decorator.permission_meta("rbac", "update")
async def role_import(request: Request) -> HTMLResponse:
    """导入角色权限 JSON。"""

    request_values = await read_request_values(request)
    filters, page = parse_role_filters(request_values)
    form_data = await request.form()
    form = build_import_form(
        {
            "payload": str(form_data.get("payload", "")),
            "allow_system": str(form_data.get("allow_system", "")),
        }
    )

    errors = build_import_errors(form["payload"])
    parsed_payload: dict[str, Any] = {}
    if not errors:
        try:
            raw_payload = json.loads(form["payload"])
            if isinstance(raw_payload, dict):
                parsed_payload = raw_payload
            else:
                errors.append("导入 JSON 顶层必须是对象")
        except json.JSONDecodeError:
            errors.append("导入 JSON 解析失败，请检查格式")

    if errors:
        context = {
            **base_context(request),
            "form": form,
            "errors": errors,
            "filters": filters,
            "page": page,
        }
        error_status = 200 if _is_htmx_request(request) else 422
        return templates.TemplateResponse("partials/role_import_form.html", context, status_code=error_status)

    owner = request.session.get("admin_name") or "system"
    summary = await role_service.import_roles_payload(
        parsed_payload,
        owner=owner,
        allow_system=form["allow_system"],
    )
    summary_message = build_import_summary_message(summary)

    await log_service.record_request(
        request,
        action="update",
        module="rbac",
        target="角色与权限",
        detail=f"导入角色权限配置：{summary_message}",
    )

    context = await build_role_table_context(request, filters, page)
    response = templates.TemplateResponse("partials/role_table.html", context)
    response.headers["HX-Retarget"] = "#role-table"
    response.headers["HX-Reswap"] = "outerHTML"
    has_errors = bool(summary["errors"])
    message = summary_message
    if has_errors:
        # 仅展示前 2 条错误，避免 toast 过长影响可读性。
        brief_errors = "；".join(summary["errors"][:2])
        if brief_errors:
            message = f"{summary_message}。{brief_errors}"

    response.headers["HX-Trigger"] = json.dumps(
        {
            "rbac-toast": {
                "title": "导入完成" if not has_errors else "导入完成（部分跳过）",
                "message": message,
                "variant": "success" if not has_errors else "warning",
            },
            "rbac-close": True,
        },
        ensure_ascii=True,
    )
    return response


@router.get("/rbac/roles/new", response_class=HTMLResponse)
async def role_new(request: Request) -> HTMLResponse:
    """新建角色弹窗。"""

    form = build_role_form({})
    filters, page = parse_role_filters(request.query_params)
    context = {
        **base_context(request),
        "mode": "create",
        "action": "/admin/rbac/roles",
        "form": form,
        "errors": [],
        "status_meta": STATUS_META,
        "tree": ROLE_PERMISSION_TREE,
        "checked_map": {},
        "action_labels": ACTION_LABELS,
        "filters": filters,
        "page": page,
    }
    return templates.TemplateResponse("partials/role_form.html", context)


@router.get("/rbac/roles/{slug}/edit", response_class=HTMLResponse)
async def role_edit(request: Request, slug: str) -> HTMLResponse:
    """编辑角色弹窗。"""

    role = await role_service.get_role_by_slug(slug)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    checked_map = build_checked_map_from_permissions(role.permissions or [])
    form = build_role_form(
        {
            "name": role.name,
            "slug": role.slug,
            "status": role.status,
            "description": role.description,
        }
    )
    filters, page = parse_role_filters(request.query_params)
    context = {
        **base_context(request),
        "mode": "edit",
        "action": f"/admin/rbac/roles/{slug}",
        "form": form,
        "errors": [],
        "status_meta": STATUS_META,
        "tree": ROLE_PERMISSION_TREE,
        "checked_map": checked_map,
        "action_labels": ACTION_LABELS,
        "filters": filters,
        "page": page,
    }
    return templates.TemplateResponse("partials/role_form.html", context)


@router.post("/rbac/roles", response_class=HTMLResponse)
@permission_decorator.permission_meta("rbac", "create")
async def role_create(
    request: Request,
) -> HTMLResponse:
    """创建角色。"""

    request_values = await read_request_values(request)
    filters, page = parse_role_filters(request_values)
    form_data = await request.form()
    form = build_role_form(
        {
            "name": str(form_data.get("name", "")).strip(),
            "slug": validators.normalize_role_slug(str(form_data.get("slug", ""))),
            "status": str(form_data.get("status", "enabled")),
            "description": str(form_data.get("description", "")).strip(),
        }
    )
    errors = role_errors(form)
    if await role_service.get_role_by_slug(form["slug"]):
        errors.append("角色标识已存在")
    if errors:
        context = {
            **base_context(request),
            "mode": "create",
            "action": "/admin/rbac/roles",
            "form": form,
            "errors": errors,
            "status_meta": STATUS_META,
            "tree": ROLE_PERMISSION_TREE,
            "checked_map": build_checked_map(form_data),
            "action_labels": ACTION_LABELS,
            "filters": filters,
            "page": page,
        }
        error_status = 200 if _is_htmx_request(request) else 422
        return templates.TemplateResponse("partials/role_form.html", context, status_code=error_status)

    owner = request.session.get("admin_name") or "system"
    form["permissions"] = build_permissions(form_data, owner)
    await role_service.create_role(form)
    await log_service.record_request(
        request,
        action="create",
        module="rbac",
        target=f"角色: {form['name']}",
        target_id=form["slug"],
        detail=f"创建角色 {form['slug']}",
    )
    context = await build_role_table_context(request, filters, page)
    response = templates.TemplateResponse("partials/role_table.html", context)
    response.headers["HX-Retarget"] = "#role-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {
            "rbac-toast": {"title": "已创建", "message": "角色已保存", "variant": "success"},
            "rbac-close": True,
        },
        ensure_ascii=True,
    )
    return response


@router.post("/rbac/roles/bulk-delete", response_class=HTMLResponse)
@permission_decorator.permission_meta("rbac", "delete")
async def role_bulk_delete(request: Request) -> HTMLResponse:
    """批量删除角色。"""

    request_values = await read_request_values(request)
    filters, page = parse_role_filters(request_values)
    form_data = await request.form()
    selected_slugs = [str(item).strip() for item in form_data.getlist("selected_slugs") if str(item).strip()]
    selected_slugs = list(dict.fromkeys(selected_slugs))

    deleted_count = 0
    skipped_system = 0
    skipped_in_use = 0
    skipped_missing = 0

    for slug in selected_slugs:
        role = await role_service.get_role_by_slug(slug)
        if not role:
            skipped_missing += 1
            continue
        if role_service.is_system_role(role.slug):
            skipped_system += 1
            continue
        if await role_service.role_in_use(role.slug):
            skipped_in_use += 1
            continue

        await role_service.delete_role(role)
        deleted_count += 1
        await log_service.record_request(
            request,
            action="delete",
            module="rbac",
            target=f"角色: {role.name}",
            target_id=role.slug,
            detail=f"批量删除角色 {role.slug}",
        )

    context = await build_role_table_context(request, filters, page)
    response = templates.TemplateResponse("partials/role_table.html", context)
    response.headers["HX-Retarget"] = "#role-table"
    response.headers["HX-Reswap"] = "outerHTML"

    if deleted_count == 0:
        toast_message = "未删除任何角色，请先勾选记录"
    else:
        extras: list[str] = []
        if skipped_system:
            extras.append(f"系统角色 {skipped_system} 条")
        if skipped_in_use:
            extras.append(f"在用角色 {skipped_in_use} 条")
        if skipped_missing:
            extras.append(f"无效记录 {skipped_missing} 条")
        suffix = f"（跳过{'，'.join(extras)}）" if extras else ""
        toast_message = f"已删除 {deleted_count} 个角色{suffix}"

    response.headers["HX-Trigger"] = json.dumps(
        {
            "rbac-toast": {
                "title": "批量删除完成",
                "message": toast_message,
                "variant": "warning",
            }
        },
        ensure_ascii=True,
    )
    return response


@router.post("/rbac/roles/{slug}", response_class=HTMLResponse)
@permission_decorator.permission_meta("rbac", "update")
async def role_update(
    request: Request,
    slug: str,
) -> HTMLResponse:
    """更新角色。"""

    request_values = await read_request_values(request)
    filters, page = parse_role_filters(request_values)
    role = await role_service.get_role_by_slug(slug)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    form_data = await request.form()
    form = build_role_form(
        {
            "name": str(form_data.get("name", "")).strip(),
            "slug": role.slug,
            "status": str(form_data.get("status", "enabled")),
            "description": str(form_data.get("description", "")).strip(),
        }
    )
    errors = role_errors(form)
    if errors:
        context = {
            **base_context(request),
            "mode": "edit",
            "action": f"/admin/rbac/roles/{slug}",
            "form": form,
            "errors": errors,
            "status_meta": STATUS_META,
            "tree": ROLE_PERMISSION_TREE,
            "checked_map": build_checked_map(form_data),
            "action_labels": ACTION_LABELS,
            "filters": filters,
            "page": page,
        }
        error_status = 200 if _is_htmx_request(request) else 422
        return templates.TemplateResponse("partials/role_form.html", context, status_code=error_status)

    owner = request.session.get("admin_name") or "system"
    form["permissions"] = build_permissions(form_data, owner)
    await role_service.update_role(role, form)
    await log_service.record_request(
        request,
        action="update",
        module="rbac",
        target=f"角色: {role.name}",
        target_id=role.slug,
        detail=f"更新角色 {role.slug}",
    )
    context = await build_role_table_context(request, filters, page)
    response = templates.TemplateResponse("partials/role_table.html", context)
    response.headers["HX-Retarget"] = "#role-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {
            "rbac-toast": {"title": "已更新", "message": "角色已修改", "variant": "success"},
            "rbac-close": True,
        },
        ensure_ascii=True,
    )
    return response


@router.delete("/rbac/roles/{slug}", response_class=HTMLResponse)
@permission_decorator.permission_meta("rbac", "delete")
async def role_delete(request: Request, slug: str) -> HTMLResponse:
    """删除角色。"""

    request_values = await read_request_values(request)
    filters, page = parse_role_filters(request_values)
    role = await role_service.get_role_by_slug(slug)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    if role_service.is_system_role(role.slug):
        raise HTTPException(status_code=400, detail="系统内置角色不允许删除")

    if await role_service.role_in_use(role.slug):
        raise HTTPException(status_code=400, detail="该角色仍被管理员使用，无法删除")

    await role_service.delete_role(role)
    await log_service.record_request(
        request,
        action="delete",
        module="rbac",
        target=f"角色: {role.name}",
        target_id=role.slug,
        detail=f"删除角色 {role.slug}",
    )
    context = await build_role_table_context(request, filters, page)
    response = templates.TemplateResponse("partials/role_table.html", context)
    response.headers["HX-Trigger"] = json.dumps(
        {"rbac-toast": {"title": "已删除", "message": "角色已移除", "variant": "warning"}},
        ensure_ascii=True,
    )
    return response
