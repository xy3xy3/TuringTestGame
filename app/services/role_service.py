"""角色服务层。"""

from __future__ import annotations

from typing import Any

from app.apps.admin.registry import ADMIN_TREE, iter_assignable_leaf_nodes, iter_leaf_nodes
from app.models import AdminUser, Role
from app.models.role import utc_now
from app.services import validators

DEFAULT_ROLES = [
    {"name": "超级管理员", "slug": "super"},
    {"name": "管理员", "slug": "admin"},
    {"name": "只读", "slug": "viewer"},
]

SYSTEM_ROLE_SLUGS = {item["slug"] for item in DEFAULT_ROLES}
ROLE_TRANSFER_VERSION = 1

_RESOURCE_ACTIONS = {
    node["key"]: set(node.get("actions", []))
    for node in iter_leaf_nodes(ADMIN_TREE)
}
_RESOURCE_REQUIRE_READ = {
    node["key"]: bool(node.get("require_read", True))
    for node in iter_leaf_nodes(ADMIN_TREE)
}
_RESOURCE_ASSIGNABLE = {
    node["key"]: bool(node.get("assignable", True))
    for node in iter_leaf_nodes(ADMIN_TREE)
}
_RESOURCE_META = {
    node["key"]: node
    for node in iter_leaf_nodes(ADMIN_TREE)
}


def _build_permission_description(node: dict[str, Any]) -> str:
    """构建权限描述文本，便于日志与导出阅读。"""
    return f"{node['name']} | {node['url']}"


def build_default_role_permissions(role_slug: str, owner: str = "system") -> list[dict[str, Any]]:
    """根据默认角色构建权限集。"""

    if role_slug in {"super", "admin"}:
        action_picker = lambda actions: list(actions)
    elif role_slug == "viewer":
        action_picker = lambda actions: ["read"] if "read" in actions else []
    else:
        return []

    permissions: list[dict[str, Any]] = []
    for node in iter_assignable_leaf_nodes(ADMIN_TREE):
        actions = action_picker(node.get("actions", []))
        if not actions:
            continue

        description = _build_permission_description(node)
        for action in actions:
            permissions.append(
                {
                    "resource": node["key"],
                    "action": action,
                    "priority": 3,
                    "status": "enabled",
                    "owner": owner,
                    "tags": ["default"],
                    "description": description,
                }
            )

    return permissions


async def list_roles() -> list[Role]:
    """查询全部角色列表。"""

    return await Role.find_all().sort("slug").to_list()


async def get_role_by_slug(slug: str) -> Role | None:
    """按 slug 查询角色。"""

    return await Role.find_one(Role.slug == slug)


def is_system_role(slug: str) -> bool:
    """判断是否为系统内置角色。"""

    return slug in SYSTEM_ROLE_SLUGS


async def role_in_use(slug: str) -> bool:
    """判断角色是否仍被管理员账号引用。"""

    admin = await AdminUser.find_one({"role_slug": slug})
    return admin is not None


def _sanitize_permissions(raw_permissions: Any, owner: str) -> list[dict[str, Any]]:
    """清洗导入权限并兜底 read 依赖，防止脏数据进入数据库。"""

    permission_map: dict[str, set[str]] = {}
    for item in raw_permissions or []:
        if not isinstance(item, dict):
            continue

        resource = str(item.get("resource") or "").strip()
        action = str(item.get("action") or "").strip().lower()
        status = str(item.get("status") or "enabled").strip().lower()
        if status != "enabled":
            continue
        if not _RESOURCE_ASSIGNABLE.get(resource, True):
            continue
        if action not in _RESOURCE_ACTIONS.get(resource, set()):
            continue
        permission_map.setdefault(resource, set()).add(action)

    normalized_permissions: list[dict[str, Any]] = []
    for resource, actions in permission_map.items():
        allowed_actions = _RESOURCE_ACTIONS.get(resource, set())
        action_set = set(actions) & allowed_actions

        if (
            _RESOURCE_REQUIRE_READ.get(resource, True)
            and "read" in allowed_actions
            and "read" not in action_set
        ):
            action_set = {action for action in action_set if action == "read"}

        node = _RESOURCE_META.get(resource)
        if not node:
            continue

        description = _build_permission_description(node)
        for action in sorted(action_set):
            normalized_permissions.append(
                {
                    "resource": resource,
                    "action": action,
                    "priority": 3,
                    "status": "enabled",
                    "owner": owner,
                    "tags": ["imported"],
                    "description": description,
                }
            )

    return normalized_permissions


async def create_role(payload: dict[str, Any]) -> Role:
    """创建角色。"""

    role = Role(
        name=payload["name"],
        slug=payload["slug"],
        status=payload.get("status", "enabled"),
        description=payload.get("description", ""),
        permissions=payload.get("permissions", []),
        updated_at=utc_now(),
    )
    await role.insert()
    return role


async def update_role(role: Role, payload: dict[str, Any]) -> Role:
    """更新角色。"""

    role.name = payload["name"]
    role.status = payload.get("status", role.status)
    role.description = payload.get("description", role.description)
    if "permissions" in payload:
        role.permissions = payload["permissions"]
    role.updated_at = utc_now()
    await role.save()
    return role


async def delete_role(role: Role) -> None:
    """删除角色。"""

    await role.delete()


def _serialize_permissions(raw_permissions: Any) -> list[dict[str, Any]]:
    """序列化角色权限，便于导出 JSON。"""

    items: list[dict[str, Any]] = []
    for item in raw_permissions or []:
        if isinstance(item, dict):
            resource = str(item.get("resource") or "").strip()
            action = str(item.get("action") or "").strip()
            status = str(item.get("status") or "enabled").strip()
            owner = str(item.get("owner") or "").strip()
            description = str(item.get("description") or "").strip()
        else:
            resource = str(getattr(item, "resource", "") or "").strip()
            action = str(getattr(item, "action", "") or "").strip()
            status = str(getattr(item, "status", "enabled") or "enabled").strip()
            owner = str(getattr(item, "owner", "") or "").strip()
            description = str(getattr(item, "description", "") or "").strip()

        if not resource or not action:
            continue

        items.append(
            {
                "resource": resource,
                "action": action,
                "status": status,
                "owner": owner,
                "description": description,
            }
        )

    return items


async def export_roles_payload(include_system: bool = True) -> dict[str, Any]:
    """导出角色权限配置（JSON payload）。"""

    roles = await list_roles()
    result_roles: list[dict[str, Any]] = []
    for role in roles:
        if not include_system and is_system_role(role.slug):
            continue

        result_roles.append(
            {
                "name": role.name,
                "slug": role.slug,
                "status": role.status,
                "description": role.description,
                "permissions": _serialize_permissions(role.permissions),
                "updated_at": role.updated_at.isoformat() if role.updated_at else "",
            }
        )

    return {
        "version": ROLE_TRANSFER_VERSION,
        "exported_at": utc_now().isoformat(),
        "roles": result_roles,
    }


async def import_roles_payload(
    payload: dict[str, Any],
    *,
    owner: str,
    allow_system: bool = True,
) -> dict[str, Any]:
    """导入角色权限配置，支持创建与更新。"""

    raw_roles = payload.get("roles", [])
    if not isinstance(raw_roles, list):
        return {
            "total": 0,
            "created": 0,
            "updated": 0,
            "skipped": 1,
            "errors": ["roles 字段必须为数组"],
        }

    summary = {
        "total": len(raw_roles),
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }

    for index, raw_role in enumerate(raw_roles, start=1):
        if not isinstance(raw_role, dict):
            summary["skipped"] += 1
            summary["errors"].append(f"第 {index} 项不是对象")
            continue

        slug = validators.normalize_role_slug(str(raw_role.get("slug", "")))
        slug_error = validators.validate_role_slug(slug)
        name = str(raw_role.get("name", "")).strip()
        if slug_error:
            summary["skipped"] += 1
            summary["errors"].append(f"第 {index} 项 slug 非法：{slug_error}")
            continue
        if len(name) < 2:
            summary["skipped"] += 1
            summary["errors"].append(f"第 {index} 项角色名称至少 2 个字符")
            continue
        if is_system_role(slug) and not allow_system:
            summary["skipped"] += 1
            summary["errors"].append(f"第 {index} 项系统角色不允许覆盖")
            continue

        status = str(raw_role.get("status", "enabled")).strip().lower()
        if status not in {"enabled", "disabled"}:
            status = "enabled"

        role_payload = {
            "name": name,
            "slug": slug,
            "status": status,
            "description": str(raw_role.get("description", "")).strip()[:120],
            "permissions": _sanitize_permissions(raw_role.get("permissions", []), owner),
        }

        current = await get_role_by_slug(slug)
        if current is None:
            await create_role(role_payload)
            summary["created"] += 1
        else:
            await update_role(current, role_payload)
            summary["updated"] += 1

    return summary


def _extract_permission_pairs(raw_permissions: Any) -> set[tuple[str, str]]:
    """提取权限键集合，用于补齐缺失的系统默认权限。"""

    pairs: set[tuple[str, str]] = set()
    for item in raw_permissions or []:
        if isinstance(item, dict):
            resource = str(item.get("resource") or "").strip()
            action = str(item.get("action") or "").strip().lower()
        else:
            resource = str(getattr(item, "resource", "") or "").strip()
            action = str(getattr(item, "action", "") or "").strip().lower()

        if not resource or not _RESOURCE_ASSIGNABLE.get(resource, True):
            continue
        if action not in _RESOURCE_ACTIONS.get(resource, set()):
            continue
        pairs.add((resource, action))

    return pairs


async def ensure_default_roles() -> None:
    """初始化系统默认角色，并补齐新增资源的默认权限。"""

    for item in DEFAULT_ROLES:
        default_permissions = build_default_role_permissions(item["slug"], owner="system")
        role = await get_role_by_slug(item["slug"])
        if not role:
            await create_role(
                {
                    "name": item["name"],
                    "slug": item["slug"],
                    "status": "enabled",
                    "description": "",
                    "permissions": default_permissions,
                }
            )
            continue

        if not role.permissions and default_permissions:
            role.permissions = default_permissions
            role.updated_at = utc_now()
            await role.save()
            continue

        existing_pairs = _extract_permission_pairs(role.permissions)
        missing_permissions = [
            permission
            for permission in default_permissions
            if (permission["resource"], permission["action"]) not in existing_pairs
        ]
        if not missing_permissions:
            continue

        role.permissions = [*(role.permissions or []), *missing_permissions]
        role.updated_at = utc_now()
        await role.save()
