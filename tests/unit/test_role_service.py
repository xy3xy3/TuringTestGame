from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import role_service


@pytest.mark.unit
def test_build_default_role_permissions_for_viewer_read_only() -> None:
    permissions = role_service.build_default_role_permissions("viewer")
    mapping = {(item["resource"], item["action"]) for item in permissions}

    assert ("admin_users", "read") in mapping
    assert ("admin_users", "update") not in mapping
    assert ("config", "update") not in mapping


@pytest.mark.unit
def test_build_default_role_permissions_for_super_has_crud() -> None:
    permissions = role_service.build_default_role_permissions("super")
    mapping = {(item["resource"], item["action"]) for item in permissions}

    assert ("rbac", "create") in mapping
    assert ("rbac", "update") in mapping
    assert ("admin_users", "delete") in mapping
    assert ("config", "update") in mapping
    assert ("profile", "update_self") not in mapping


@pytest.mark.unit
def test_is_system_role() -> None:
    assert role_service.is_system_role("super") is True
    assert role_service.is_system_role("admin") is True
    assert role_service.is_system_role("viewer") is True
    assert role_service.is_system_role("ops") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_role_in_use(monkeypatch) -> None:
    async def fake_find_one(*_args, **_kwargs):
        return SimpleNamespace(id="x")

    monkeypatch.setattr(role_service.AdminUser, "find_one", fake_find_one)
    assert await role_service.role_in_use("ops") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_role_not_in_use(monkeypatch) -> None:
    async def fake_find_one(*_args, **_kwargs):
        return None

    monkeypatch.setattr(role_service.AdminUser, "find_one", fake_find_one)
    assert await role_service.role_in_use("ops") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_export_roles_payload_can_skip_system_roles(monkeypatch) -> None:
    roles = [
        SimpleNamespace(
            name="超级管理员",
            slug="super",
            status="enabled",
            description="",
            permissions=[{"resource": "rbac", "action": "read", "status": "enabled"}],
            updated_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00+00:00"),
        ),
        SimpleNamespace(
            name="运维",
            slug="ops",
            status="enabled",
            description="",
            permissions=[{"resource": "admin_users", "action": "read", "status": "enabled"}],
            updated_at=SimpleNamespace(isoformat=lambda: "2026-01-02T00:00:00+00:00"),
        ),
    ]

    async def fake_list_roles() -> list[SimpleNamespace]:
        return roles

    monkeypatch.setattr(role_service, "list_roles", fake_list_roles)

    payload = await role_service.export_roles_payload(include_system=False)

    assert payload["version"] == role_service.ROLE_TRANSFER_VERSION
    assert [item["slug"] for item in payload["roles"]] == ["ops"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_import_roles_payload_creates_and_updates(monkeypatch) -> None:
    payload = {
        "roles": [
            {
                "name": "运维",
                "slug": "ops",
                "status": "enabled",
                "permissions": [
                    {"resource": "admin_users", "action": "read", "status": "enabled"},
                    {"resource": "admin_users", "action": "update", "status": "enabled"},
                ],
            },
            {
                "name": "开发",
                "slug": "dev",
                "status": "enabled",
                "permissions": [
                    {"resource": "admin_users", "action": "read", "status": "enabled"},
                ],
            },
        ]
    }

    existing = SimpleNamespace(slug="dev", name="开发")
    created_payloads: list[dict] = []
    updated_payloads: list[dict] = []

    async def fake_get_role_by_slug(slug: str):
        if slug == "dev":
            return existing
        return None

    async def fake_create_role(item: dict) -> SimpleNamespace:
        created_payloads.append(item)
        return SimpleNamespace(**item)

    async def fake_update_role(role, item: dict):
        updated_payloads.append(item)
        return role

    monkeypatch.setattr(role_service, "get_role_by_slug", fake_get_role_by_slug)
    monkeypatch.setattr(role_service, "create_role", fake_create_role)
    monkeypatch.setattr(role_service, "update_role", fake_update_role)

    summary = await role_service.import_roles_payload(payload, owner="tester", allow_system=False)

    assert summary["created"] == 1
    assert summary["updated"] == 1
    assert summary["skipped"] == 0
    assert created_payloads[0]["slug"] == "ops"
    assert any(item["resource"] == "admin_users" and item["action"] == "update" for item in created_payloads[0]["permissions"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_import_roles_payload_rejects_invalid_roles_field() -> None:
    summary = await role_service.import_roles_payload({"roles": {}}, owner="tester")

    assert summary["skipped"] == 1
    assert "roles 字段必须为数组" in summary["errors"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_default_roles_appends_missing_permissions(monkeypatch) -> None:
    """系统默认角色存在时应补齐新增资源权限。"""

    class FakeRole(SimpleNamespace):
        async def save(self) -> None:
            return None

    role = FakeRole(
        slug="super",
        permissions=[
            {"resource": "config", "action": "read", "status": "enabled"},
            {"resource": "config", "action": "update", "status": "enabled"},
        ],
        updated_at=None,
    )

    async def fake_get_role_by_slug(slug: str):
        if slug == "super":
            return role
        return None

    created_payloads: list[dict] = []

    async def fake_create_role(payload: dict):
        created_payloads.append(payload)
        return SimpleNamespace(**payload)

    monkeypatch.setattr(role_service, "get_role_by_slug", fake_get_role_by_slug)
    monkeypatch.setattr(role_service, "create_role", fake_create_role)

    await role_service.ensure_default_roles()

    restored_pairs = {(item["resource"], item["action"]) for item in role.permissions}
    assert ("backup_config", "read") in restored_pairs
    assert ("backup_config", "update") in restored_pairs
    assert ("backup_records", "trigger") in restored_pairs
    assert ("backup_records", "restore") in restored_pairs
    assert created_payloads
    assert {item["slug"] for item in created_payloads} == {"admin", "viewer"}
