from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute

from app.main import app
from app.services import permission_service


@pytest.mark.unit
def test_required_permission_route_mapping() -> None:
    assert permission_service.required_permission("/admin/users", "GET") == ("admin_users", "read")
    assert permission_service.required_permission("/admin/users/507f1f77bcf86cd799439011", "POST") == (
        "admin_users",
        "update",
    )
    assert permission_service.required_permission("/admin/rbac/roles/viewer", "DELETE") == ("rbac", "delete")
    assert permission_service.required_permission("/admin/backup", "GET") == ("backup_records", "read")
    assert permission_service.required_permission("/admin/backup/table", "GET") == ("backup_records", "read")
    assert permission_service.required_permission("/admin/backup/collections", "GET") == ("backup_config", "read")
    assert permission_service.required_permission("/admin/backup/demo", "DELETE") == ("backup_records", "delete")
    assert permission_service.required_permission("/admin/backup/demo/restore", "POST") == ("backup_records", "restore")
    assert permission_service.required_permission("/admin/logs/demo", "DELETE") == ("operation_logs", "delete")
    assert permission_service.required_permission("/admin/logs/bulk-delete", "POST") == ("operation_logs", "delete")
    assert permission_service.required_permission("/admin/unknown", "GET") is None


@pytest.mark.unit
def test_required_permission_covers_all_admin_routes() -> None:
    exempt_paths = {"/admin/login", "/admin/logout"}

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/admin"):
            continue
        if route.path in exempt_paths:
            continue

        concrete_path = re.sub(r"\{[^/]+\}", "demo", route.path)
        methods = {method for method in (route.methods or set()) if method in {"GET", "POST", "DELETE"}}
        for method in methods:
            assert permission_service.required_permission(concrete_path, method) is not None, (
                f"未配置权限映射: {method} {route.path}"
            )


@pytest.mark.unit
def test_build_permission_flags_contains_menu_switches() -> None:
    permission_map = {
        "dashboard_home": {"read"},
        "rbac": {"read"},
        "profile": {"read"},
    }

    flags = permission_service.build_permission_flags(permission_map)

    assert flags["dashboard"]["read"] is True
    assert flags["rbac"]["read"] is True
    assert flags["admin_users"]["read"] is False
    assert flags["menus"]["security"] is True
    assert flags["menus"]["system"] is False
    assert flags["menus"]["profile"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_permission_map_uses_role_permissions_without_slug_special_case(monkeypatch) -> None:
    request = SimpleNamespace(session={"admin_id": "abc"}, state=SimpleNamespace())

    admin = SimpleNamespace(status="enabled", role_slug="viewer")
    role = SimpleNamespace(
        status="enabled",
        permissions=[
            {"resource": "admin_users", "action": "read", "status": "enabled"},
            {"resource": "admin_users", "action": "update", "status": "enabled"},
            {"resource": "config", "action": "read", "status": "enabled"},
            {"resource": "config", "action": "invalid", "status": "enabled"},
        ],
    )

    async def fake_get_admin_by_id(_admin_id: str):
        return admin

    async def fake_get_role_by_slug(_role_slug: str):
        return role

    monkeypatch.setattr(permission_service.auth_service, "get_admin_by_id", fake_get_admin_by_id)
    monkeypatch.setattr(permission_service.role_service, "get_role_by_slug", fake_get_role_by_slug)

    permission_map = await permission_service.resolve_permission_map(request)

    assert permission_map == {
        "admin_users": {"read", "update"},
        "config": {"read"},
        "profile": {"read", "update_self"},
        "password": {"read", "update_self"},
    }
    assert request.state.permission_flags["admin_users"]["update"] is True
    assert request.state.permission_flags["profile"]["update_self"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_permission_map_requires_read_for_mutating_actions(monkeypatch) -> None:
    request = SimpleNamespace(session={"admin_id": "abc"}, state=SimpleNamespace())

    admin = SimpleNamespace(status="enabled", role_slug="admin")
    role = SimpleNamespace(
        status="enabled",
        permissions=[
            {"resource": "admin_users", "action": "update", "status": "enabled"},
        ],
    )

    async def fake_get_admin_by_id(_admin_id: str):
        return admin

    async def fake_get_role_by_slug(_role_slug: str):
        return role

    monkeypatch.setattr(permission_service.auth_service, "get_admin_by_id", fake_get_admin_by_id)
    monkeypatch.setattr(permission_service.role_service, "get_role_by_slug", fake_get_role_by_slug)

    permission_map = await permission_service.resolve_permission_map(request)

    assert permission_map == {
        "profile": {"read", "update_self"},
        "password": {"read", "update_self"},
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_permission_map_keeps_self_service_when_role_missing(monkeypatch) -> None:
    request = SimpleNamespace(session={"admin_id": "abc"}, state=SimpleNamespace())

    admin = SimpleNamespace(status="enabled", role_slug="viewer")

    async def fake_get_admin_by_id(_admin_id: str):
        return admin

    async def fake_get_role_by_slug(_role_slug: str):
        return None

    monkeypatch.setattr(permission_service.auth_service, "get_admin_by_id", fake_get_admin_by_id)
    monkeypatch.setattr(permission_service.role_service, "get_role_by_slug", fake_get_role_by_slug)

    permission_map = await permission_service.resolve_permission_map(request)

    assert permission_map == {
        "profile": {"read", "update_self"},
        "password": {"read", "update_self"},
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_permission_map_keeps_self_service_for_disabled_role(monkeypatch) -> None:
    request = SimpleNamespace(session={"admin_id": "abc"}, state=SimpleNamespace())

    admin = SimpleNamespace(status="enabled", role_slug="viewer")
    role = SimpleNamespace(status="disabled", permissions=[])

    async def fake_get_admin_by_id(_admin_id: str):
        return admin

    async def fake_get_role_by_slug(_role_slug: str):
        return role

    monkeypatch.setattr(permission_service.auth_service, "get_admin_by_id", fake_get_admin_by_id)
    monkeypatch.setattr(permission_service.role_service, "get_role_by_slug", fake_get_role_by_slug)

    permission_map = await permission_service.resolve_permission_map(request)

    assert permission_map == {
        "profile": {"read", "update_self"},
        "password": {"read", "update_self"},
    }


@pytest.mark.unit
def test_build_permission_flags_contains_dynamic_resource_map() -> None:
    permission_map = {
        "dashboard_home": {"read"},
        "profile": {"read"},
    }

    flags = permission_service.build_permission_flags(permission_map)

    assert "resources" in flags
    assert flags["resources"]["dashboard_home"]["read"] is True
    assert flags["resources"]["admin_users"]["read"] is False
    assert flags["resources"]["backup_records"]["trigger"] is False
    assert flags["menus"]["accounts"] is True


@pytest.mark.unit
def test_required_permission_prefers_explicit_route_declaration() -> None:
    assert permission_service.required_permission("/admin/users", "POST") == ("admin_users", "create")
    assert permission_service.required_permission("/admin/users/507f1f77bcf86cd799439011", "POST") == (
        "admin_users",
        "update",
    )
    assert permission_service.required_permission("/admin/config", "POST") == ("config", "update")
    assert permission_service.required_permission("/admin/rbac/roles/import", "GET") == ("rbac", "update")
    assert permission_service.required_permission("/admin/rbac/roles/import", "POST") == ("rbac", "update")
    assert permission_service.required_permission("/admin/backup", "POST") == ("backup_config", "update")
    assert permission_service.required_permission("/admin/backup/trigger", "POST") == ("backup_records", "trigger")
    assert permission_service.required_permission("/admin/backup/demo/restore", "POST") == ("backup_records", "restore")
    assert permission_service.required_permission("/admin/profile", "POST") == ("profile", "update_self")
    assert permission_service.required_permission("/admin/password", "POST") == ("password", "update_self")


@pytest.mark.unit
def test_bulk_delete_routes_are_registered_before_dynamic_post_routes() -> None:
    """避免 /bulk-delete 被 /{id} 等动态 POST 路由抢先匹配。"""

    def route_index(path: str, method: str) -> int:
        for index, route in enumerate(app.routes):
            if not isinstance(route, APIRoute):
                continue
            if route.path != path:
                continue
            if method in (route.methods or set()):
                return index
        raise AssertionError(f"未找到路由: {method} {path}")

    assert route_index("/admin/users/bulk-delete", "POST") < route_index("/admin/users/{item_id}", "POST")
    assert route_index("/admin/rbac/roles/bulk-delete", "POST") < route_index("/admin/rbac/roles/{slug}", "POST")
