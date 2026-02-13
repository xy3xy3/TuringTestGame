from __future__ import annotations

import json
import re

import httpx
import pytest

from app.main import app
from app.services import admin_user_service, auth_service, role_service


def _permission(resource: str, action: str) -> dict[str, str]:
    """构建权限项，统一补齐 enabled 状态字段。"""

    return {
        "resource": resource,
        "action": action,
        "status": "enabled",
    }


def _extract_login_csrf(html: str) -> str:
    """从登录页提取隐藏表单 CSRF Token。"""

    matched = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert matched, "登录页未返回 csrf_token"
    return matched.group(1)


def _extract_page_csrf(html: str) -> str:
    """从后台页面提取 meta CSRF Token。"""

    matched = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', html)
    assert matched, "后台页面未返回 csrf-token meta"
    return matched.group(1)


async def _seed_admin(
    *,
    username: str,
    password: str,
    role_slug: str,
    permissions: list[dict[str, str]],
    display_name: str,
) -> None:
    """初始化测试管理员与角色。"""

    await role_service.create_role(
        {
            "name": f"{display_name}角色",
            "slug": role_slug,
            "status": "enabled",
            "description": "integration",
            "permissions": permissions,
        }
    )
    await admin_user_service.create_admin(
        {
            "username": username,
            "display_name": display_name,
            "email": "",
            "role_slug": role_slug,
            "status": "enabled",
            "password_hash": auth_service.hash_password(password),
        }
    )


async def _login_and_get_csrf(
    client: httpx.AsyncClient,
    *,
    username: str,
    password: str,
    next_path: str,
) -> str:
    """执行登录并返回落地页面的 CSRF Token。"""

    login_page = await client.get("/admin/login")
    assert login_page.status_code == 200
    login_token = _extract_login_csrf(login_page.text)

    login_response = await client.post(
        "/admin/login",
        data={
            "username": username,
            "password": password,
            "next": next_path,
            "csrf_token": login_token,
        },
    )
    assert login_response.status_code == 302

    landing = await client.get(login_response.headers.get("location") or next_path)
    assert landing.status_code == 200
    return _extract_page_csrf(landing.text)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rbac_role_import_and_export_roundtrip(initialized_db) -> None:
    transport = httpx.ASGITransport(app=app)

    await _seed_admin(
        username="ops_importer",
        password="ops_importer_123",
        role_slug="ops_importer",
        display_name="导入管理员",
        permissions=[
            _permission("rbac", "read"),
            _permission("rbac", "update"),
            _permission("admin_users", "read"),
        ],
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
        csrf_token = await _login_and_get_csrf(
            client,
            username="ops_importer",
            password="ops_importer_123",
            next_path="/admin/rbac",
        )

        import_form = await client.get("/admin/rbac/roles/import")
        assert import_form.status_code == 200
        assert 'name="payload"' in import_form.text

        import_payload = {
            "version": 1,
            "roles": [
                {
                    "name": "导入运维",
                    "slug": "ops_transfer",
                    "status": "enabled",
                    "description": "导入测试",
                    "permissions": [
                        {"resource": "admin_users", "action": "read", "status": "enabled"},
                        {"resource": "admin_users", "action": "update", "status": "enabled"},
                        {"resource": "rbac", "action": "update", "status": "enabled"},
                    ],
                }
            ],
        }

        imported = await client.post(
            "/admin/rbac/roles/import",
            data={
                "csrf_token": csrf_token,
                "payload": json.dumps(import_payload, ensure_ascii=False),
                "allow_system": "",
            },
        )
        assert imported.status_code == 200
        assert "ops_transfer" in imported.text

        role = await role_service.get_role_by_slug("ops_transfer")
        assert role is not None
        action_pairs = {(item.action, item.resource) for item in role.permissions}
        assert ("read", "admin_users") in action_pairs
        assert ("update", "admin_users") in action_pairs
        assert ("update", "rbac") not in action_pairs

        exported = await client.get("/admin/rbac/roles/export?include_system=0")
        assert exported.status_code == 200
        assert "attachment; filename=" in exported.headers.get("content-disposition", "")
        exported_payload = exported.json()
        assert any(item["slug"] == "ops_transfer" for item in exported_payload["roles"])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_imported_role_permissions_take_effect_on_access(initialized_db) -> None:
    transport = httpx.ASGITransport(app=app)

    await _seed_admin(
        username="ops_importer2",
        password="ops_importer_456",
        role_slug="ops_importer2",
        display_name="导入管理员二号",
        permissions=[
            _permission("rbac", "read"),
            _permission("rbac", "update"),
            _permission("admin_users", "read"),
        ],
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as importer:
        csrf_token = await _login_and_get_csrf(
            importer,
            username="ops_importer2",
            password="ops_importer_456",
            next_path="/admin/rbac",
        )

        import_payload = {
            "version": 1,
            "roles": [
                {
                    "name": "导入只读账号",
                    "slug": "import_viewer",
                    "status": "enabled",
                    "description": "只读",
                    "permissions": [
                        {"resource": "admin_users", "action": "read", "status": "enabled"},
                    ],
                }
            ],
        }
        imported = await importer.post(
            "/admin/rbac/roles/import",
            data={
                "csrf_token": csrf_token,
                "payload": json.dumps(import_payload, ensure_ascii=False),
            },
        )
        assert imported.status_code == 200

    await admin_user_service.create_admin(
        {
            "username": "import_viewer_user",
            "display_name": "导入只读用户",
            "email": "",
            "role_slug": "import_viewer",
            "status": "enabled",
            "password_hash": auth_service.hash_password("viewer_pass_123"),
        }
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as viewer:
        viewer_csrf = await _login_and_get_csrf(
            viewer,
            username="import_viewer_user",
            password="viewer_pass_123",
            next_path="/admin/users",
        )

        list_response = await viewer.get("/admin/users")
        assert list_response.status_code == 200
        assert "管理员列表" in list_response.text

        new_page = await viewer.get("/admin/users/new")
        assert new_page.status_code == 403

        mutate = await viewer.post(
            "/admin/users",
            data={
                "csrf_token": viewer_csrf,
                "username": "any_user",
                "display_name": "任意用户",
                "role_slug": "import_viewer",
                "status": "enabled",
                "password": "123456",
            },
        )
        assert mutate.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_role_import_returns_warning_trigger_when_partially_skipped(initialized_db) -> None:
    """导入部分失败时应返回 warning toast 触发器，便于前端统一提示。"""

    transport = httpx.ASGITransport(app=app)

    await _seed_admin(
        username="ops_importer3",
        password="ops_importer_789",
        role_slug="ops_importer3",
        display_name="导入管理员三号",
        permissions=[
            _permission("rbac", "read"),
            _permission("rbac", "update"),
        ],
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
        csrf_token = await _login_and_get_csrf(
            client,
            username="ops_importer3",
            password="ops_importer_789",
            next_path="/admin/rbac",
        )

        import_payload = {
            "version": 1,
            "roles": [
                {
                    "name": "非法角色",
                    "slug": "Ops Team",
                    "status": "enabled",
                    "permissions": [
                        {"resource": "admin_users", "action": "read", "status": "enabled"},
                    ],
                }
            ],
        }

        response = await client.post(
            "/admin/rbac/roles/import",
            data={
                "csrf_token": csrf_token,
                "payload": json.dumps(import_payload, ensure_ascii=False),
            },
        )
        assert response.status_code == 200

        trigger_raw = response.headers.get("HX-Trigger", "")
        assert trigger_raw
        trigger = json.loads(trigger_raw)
        assert trigger["rbac-close"] is True
        assert trigger["rbac-toast"]["variant"] == "warning"
        assert "部分跳过" in trigger["rbac-toast"]["title"]
        assert "跳过 1" in trigger["rbac-toast"]["message"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_role_import_invalid_json_returns_422_form_errors(initialized_db) -> None:
    """导入 JSON 语法错误时应返回 422 并展示表单错误。"""

    transport = httpx.ASGITransport(app=app)

    await _seed_admin(
        username="ops_importer4",
        password="ops_importer_abc",
        role_slug="ops_importer4",
        display_name="导入管理员四号",
        permissions=[
            _permission("rbac", "read"),
            _permission("rbac", "update"),
        ],
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
        csrf_token = await _login_and_get_csrf(
            client,
            username="ops_importer4",
            password="ops_importer_abc",
            next_path="/admin/rbac",
        )

        response = await client.post(
            "/admin/rbac/roles/import",
            data={
                "csrf_token": csrf_token,
                "payload": "{not-json}",
            },
        )
        assert response.status_code == 422
        assert "导入 JSON 解析失败，请检查格式" in response.text
        assert response.headers.get("HX-Trigger", "") == ""
