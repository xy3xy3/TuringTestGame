"""权限解析与鉴权服务。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from typing import Any

from fastapi.routing import APIRoute
from starlette.requests import Request

from app.apps.admin.registry import ADMIN_TREE, iter_leaf_nodes
from app.services import auth_service, role_service

_RESOURCE_ACTIONS: dict[str, set[str]] = {
    node["key"]: set(node.get("actions", []))
    for node in iter_leaf_nodes(ADMIN_TREE)
}

_RESOURCE_REQUIRE_READ: dict[str, bool] = {
    node["key"]: bool(node.get("require_read", True))
    for node in iter_leaf_nodes(ADMIN_TREE)
}

_SELF_SERVICE_ACTIONS: dict[str, set[str]] = {
    node["key"]: set(node.get("actions", []))
    for node in iter_leaf_nodes(ADMIN_TREE)
    if str(node.get("mode") or "").strip() == "self_service"
}

_RESOURCE_URLS: list[tuple[str, str]] = sorted(
    [
        ((str(node.get("url") or "").rstrip("/") or "/"), node["key"])
        for node in iter_leaf_nodes(ADMIN_TREE)
        if node.get("url")
    ],
    key=lambda item: len(item[0]),
    reverse=True,
)

_RESOURCE_BASE_URLS: dict[str, str] = {
    resource: url for url, resource in _RESOURCE_URLS
}


def _normalize_permission_items(items: list[Any] | None) -> dict[str, set[str]]:
    permission_map: dict[str, set[str]] = {}
    for item in items or []:
        resource = getattr(item, "resource", None) or (item.get("resource") if isinstance(item, dict) else None)
        action = getattr(item, "action", None) or (item.get("action") if isinstance(item, dict) else None)
        status = getattr(item, "status", None) or (item.get("status") if isinstance(item, dict) else None)

        if status and status != "enabled":
            continue
        if not resource or not action:
            continue
        if action not in _RESOURCE_ACTIONS.get(resource, set()):
            continue
        permission_map.setdefault(resource, set()).add(action)
    return permission_map


def _apply_action_constraints(permission_map: dict[str, set[str]]) -> dict[str, set[str]]:
    """约束动作依赖：需要 read 依赖的资源必须先有 read。"""

    normalized: dict[str, set[str]] = {}
    for resource, actions in permission_map.items():
        allowed_actions = _RESOURCE_ACTIONS.get(resource, set())
        if not allowed_actions:
            continue

        action_set = set(actions) & allowed_actions
        if (
            _RESOURCE_REQUIRE_READ.get(resource, True)
            and "read" in allowed_actions
            and "read" not in action_set
        ):
            # 对依赖 read 的资源，read 缺失时剔除所有非 read 动作。
            action_set = {action for action in action_set if action == "read"}

        if action_set:
            normalized[resource] = action_set

    return normalized


def _apply_builtin_grants(permission_map: dict[str, set[str]]) -> dict[str, set[str]]:
    """注入系统内置权限（如 self_service 登录即允许）。"""

    merged: dict[str, set[str]] = {
        resource: set(actions)
        for resource, actions in permission_map.items()
    }

    for resource, actions in _SELF_SERVICE_ACTIONS.items():
        merged.setdefault(resource, set()).update(actions)

    return _apply_action_constraints(merged)


async def resolve_permission_map(request: Request) -> dict[str, set[str]]:
    """解析当前登录账号的权限并缓存到 request.state。"""

    cached = getattr(request.state, "permission_map", None)
    if cached is not None:
        return cached

    admin = await auth_service.get_admin_by_id(request.session.get("admin_id"))
    request.state.current_admin_model = admin
    if not admin or admin.status != "enabled":
        request.state.permission_map = {}
        request.state.permission_flags = build_permission_flags({})
        return {}

    role = await role_service.get_role_by_slug(admin.role_slug)
    request.state.current_role_model = role

    permission_map: dict[str, set[str]] = {}
    if role and role.status == "enabled":
        permission_map = _normalize_permission_items(role.permissions)

    permission_map = _apply_action_constraints(permission_map)
    permission_map = _apply_builtin_grants(permission_map)

    request.state.permission_map = permission_map
    request.state.permission_flags = build_permission_flags(permission_map)
    return permission_map


def can(permission_map: dict[str, set[str]], resource: str, action: str) -> bool:
    return action in permission_map.get(resource, set())


def build_resource_flags(permission_map: dict[str, set[str]], resource: str) -> dict[str, bool]:
    """按资源声明的动作动态构建布尔标记。"""

    actions = _RESOURCE_ACTIONS.get(resource, set())
    return {
        action: can(permission_map, resource, action)
        for action in sorted(actions)
    }


def build_permission_flags(permission_map: dict[str, set[str]]) -> dict[str, Any]:
    """构建权限标记，资源位自动从注册树推导。"""

    resource_flags = {
        node["key"]: build_resource_flags(permission_map, node["key"])
        for node in iter_leaf_nodes(ADMIN_TREE)
    }

    flags: dict[str, Any] = {
        "resources": resource_flags,
        **resource_flags,
    }

    # 为历史模板保留 dashboard 别名，避免改动多处页面。
    flags.setdefault("dashboard", resource_flags.get("dashboard_home", build_resource_flags(permission_map, "dashboard_home")))

    menu_flags: dict[str, bool] = {}
    for group in ADMIN_TREE:
        leaf_keys = [node["key"] for node in iter_leaf_nodes([group])]
        menu_flags[group["key"]] = any(
            any(resource_flags.get(key, {}).values())
            for key in leaf_keys
        )

    # 兼容旧模板菜单键，降低迁移成本。
    menu_flags["security"] = menu_flags.get("security", False)
    menu_flags["system"] = menu_flags.get("system", False)
    menu_flags["profile"] = (
        any(resource_flags.get("profile", {}).values())
        or any(resource_flags.get("password", {}).values())
    )
    flags["menus"] = {
        **menu_flags,
    }
    return flags


@dataclass(frozen=True)
class PermissionRouteRule:
    """路由权限规则。"""

    path_regex: re.Pattern[str]
    method: str
    resource: str
    action: str


def _normalize_path(path: str) -> str:
    """统一路径格式，避免尾斜杠导致映射失败。"""

    normalized = path.rstrip("/")
    return normalized or "/"


def _resolve_resource_from_path(path: str) -> str | None:
    """根据路由路径推导对应资源键。"""

    normalized = _normalize_path(path)
    if normalized == "/admin":
        return "dashboard_home"

    for base_url, resource in _RESOURCE_URLS:
        if normalized == base_url or normalized.startswith(f"{base_url}/"):
            return resource
    return None


def _compile_route_regex(path: str) -> re.Pattern[str]:
    """把 FastAPI 模板路径编译为运行时匹配正则。"""

    normalized = _normalize_path(path)
    escaped = re.escape(normalized)
    pattern = re.sub(r"\\\{[^/]+\\\}", r"[^/]+", escaped)
    return re.compile(rf"^{pattern}$")


def _infer_action(resource: str, method: str, path: str) -> str | None:
    """按路由声明和 HTTP 方法推导动作。"""

    allowed_actions = _RESOURCE_ACTIONS.get(resource, set())
    normalized = _normalize_path(path)
    base_url = _RESOURCE_BASE_URLS.get(resource, "")

    if method == "GET":
        if normalized.endswith("/new") and "create" in allowed_actions:
            return "create"
        if normalized.endswith("/edit") and "update" in allowed_actions:
            return "update"
        return "read" if "read" in allowed_actions else None

    if method == "DELETE":
        return "delete" if "delete" in allowed_actions else None

    if method in {"PUT", "PATCH"}:
        return "update" if "update" in allowed_actions else None

    if method != "POST":
        return None

    if normalized.endswith("/new") and "create" in allowed_actions:
        return "create"
    if normalized.endswith("/edit") and "update" in allowed_actions:
        return "update"
    if normalized.endswith("/delete") and "delete" in allowed_actions:
        return "delete"
    if "{" in path and "}" in path and "update" in allowed_actions:
        return "update"

    if normalized == base_url:
        if "create" in allowed_actions:
            return "create"
        if "update" in allowed_actions:
            return "update"

    return None


def _resolve_explicit_permission(route: APIRoute, method: str) -> tuple[str, str] | None:
    """从显式声明读取权限映射，优先于自动推断。"""

    endpoint_meta = getattr(route.endpoint, "__permission_meta__", None)
    openapi_meta = (route.openapi_extra or {}).get("permission")

    for meta in [endpoint_meta, openapi_meta]:
        if not isinstance(meta, dict):
            continue

        scoped = meta.get(method.upper(), meta)
        if not isinstance(scoped, dict):
            continue

        resource = str(scoped.get("resource") or "").strip()
        action = str(scoped.get("action") or "").strip()
        if not resource or not action:
            continue
        if action not in _RESOURCE_ACTIONS.get(resource, set()):
            continue
        return (resource, action)

    return None


@lru_cache(maxsize=1)
def _build_permission_rules() -> tuple[PermissionRouteRule, ...]:
    """从路由声明自动生成权限映射规则。"""

    # 延迟导入，避免在模块加载阶段产生循环依赖。
    from app.main import app

    rules: list[PermissionRouteRule] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue

        route_path = _normalize_path(route.path)
        if not route_path.startswith("/admin"):
            continue
        if route_path.startswith("/admin/login") or route_path == "/admin/logout":
            continue

        resource = _resolve_resource_from_path(route_path)
        if not resource:
            continue

        methods = {method for method in (route.methods or set()) if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}}
        path_regex = _compile_route_regex(route.path)

        for method in methods:
            explicit = _resolve_explicit_permission(route, method)
            if explicit is not None:
                resource_key, action = explicit
            else:
                action = _infer_action(resource, method, route.path)
                resource_key = resource
                if not action:
                    continue

            rules.append(
                PermissionRouteRule(
                    path_regex=path_regex,
                    method=method,
                    resource=resource_key,
                    action=action,
                )
            )

    # 更长路径优先匹配，避免 /admin/users 先于 /admin/users/{id}/edit 命中。
    rules.sort(key=lambda item: len(item.path_regex.pattern), reverse=True)
    return tuple(rules)


def required_permission(path: str, method: str) -> tuple[str, str] | None:
    """根据自动生成的规则解析请求资源与动作。"""

    normalized_path = _normalize_path(path)
    normalized_method = method.upper()
    if normalized_method == "HEAD":
        normalized_method = "GET"

    for rule in _build_permission_rules():
        if rule.method != normalized_method:
            continue
        if rule.path_regex.fullmatch(normalized_path):
            return (rule.resource, rule.action)

    return None
