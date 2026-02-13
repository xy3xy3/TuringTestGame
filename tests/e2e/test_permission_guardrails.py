from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import httpx
import pytest
from playwright.sync_api import Error, expect, sync_playwright
from pymongo import MongoClient

from app.services.auth_service import hash_password


def _utc_now() -> datetime:
    """返回 UTC 时间，便于构造测试数据。"""

    return datetime.now(timezone.utc)


def _login_http_client(base_url: str, username: str, password: str) -> httpx.Client:
    """登录后台并返回带会话 Cookie 的 HTTP 客户端。"""

    client = httpx.Client(base_url=base_url, follow_redirects=False)
    login_page = client.get("/admin/login")
    assert login_page.status_code == 200
    token_match = re.search(r'name="csrf_token"\s+value="([^"]+)"', login_page.text)
    assert token_match, "登录页未返回 CSRF Token"

    response = client.post(
        "/admin/login",
        data={
            "username": username,
            "password": password,
            "next": "/admin/dashboard",
            "csrf_token": token_match.group(1),
        },
    )
    assert response.status_code == 302

    dashboard_response = client.get(response.headers.get("location") or "/admin/dashboard")
    assert dashboard_response.status_code == 200
    dashboard_token_match = re.search(
        r'<meta\s+name="csrf-token"\s+content="([^"]+)"',
        dashboard_response.text,
    )
    assert dashboard_token_match, "仪表盘页未返回 CSRF Token"
    client.headers["X-CSRF-Token"] = dashboard_token_match.group(1)
    return client


@pytest.mark.e2e
def test_readonly_role_hides_actions_and_backend_forbids_mutation(
    e2e_base_url: str,
    test_mongo_url: str,
    e2e_mongo_db_name: str,
) -> None:
    client = MongoClient(test_mongo_url)
    db = client[e2e_mongo_db_name]

    db.roles.insert_one(
        {
            "name": "审计员",
            "slug": "auditor",
            "status": "enabled",
            "description": "仅查看账号和角色",
            "permissions": [
                {"resource": "dashboard_home", "action": "read", "status": "enabled", "priority": 3},
                {"resource": "admin_users", "action": "read", "status": "enabled", "priority": 3},
                {"resource": "rbac", "action": "read", "status": "enabled", "priority": 3},
            ],
            "updated_at": _utc_now(),
        }
    )
    db.admin_users.insert_one(
        {
            "username": "auditor_user",
            "display_name": "审计员",
            "email": "",
            "role_slug": "auditor",
            "status": "enabled",
            "password_hash": hash_password("auditor_pass_123"),
            "last_login": None,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
    )
    client.close()

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Error as exc:
            pytest.skip(f"Chromium not installed for Playwright: {exc}")

        page = browser.new_page()
        page.goto(f"{e2e_base_url}/admin/login", wait_until="networkidle")
        page.locator("input[name=username]").fill("auditor_user")
        page.locator("input[name=password]").fill("auditor_pass_123")
        page.get_by_role("button", name="登录").click()
        page.wait_for_url("**/admin/dashboard")

        page.goto(f"{e2e_base_url}/admin/users", wait_until="networkidle")
        expect(page.get_by_role("heading", name="管理员列表")).to_be_visible()
        expect(page.locator("button:has-text(\"新建管理员\")")).to_have_count(0)
        expect(page.locator("#admin-table thead th:has-text(\"操作\")")).to_have_count(0)
        expect(page.locator("#admin-table button:has-text(\"编辑\")")).to_have_count(0)
        expect(page.locator("#admin-table button:has-text(\"删除\")")).to_have_count(0)

        page.goto(f"{e2e_base_url}/admin/rbac", wait_until="networkidle")
        expect(page.get_by_role("heading", name="角色列表")).to_be_visible()
        expect(page.locator("button:has-text(\"新建角色\")")).to_have_count(0)
        expect(page.locator("#role-table thead th:has-text(\"操作\")")).to_have_count(0)
        expect(page.locator("#role-table button:has-text(\"编辑\")")).to_have_count(0)
        expect(page.locator("#role-table button:has-text(\"删除\")")).to_have_count(0)

        browser.close()

    session = _login_http_client(e2e_base_url, "auditor_user", "auditor_pass_123")
    deny_response = session.get("/admin/users/new")
    assert deny_response.status_code == 403
    assert "没有执行该操作的权限" in deny_response.text
    session.close()


@pytest.mark.e2e
def test_admin_guardrails_for_unmapped_route_and_role_delete(
    e2e_base_url: str,
    test_mongo_url: str,
    e2e_mongo_db_name: str,
) -> None:
    mongo_client = MongoClient(test_mongo_url)
    db = mongo_client[e2e_mongo_db_name]

    db.roles.insert_one(
        {
            "name": "运维",
            "slug": "ops",
            "status": "enabled",
            "description": "运维角色",
            "permissions": [
                {"resource": "dashboard_home", "action": "read", "status": "enabled", "priority": 3},
                {"resource": "admin_users", "action": "read", "status": "enabled", "priority": 3},
            ],
            "updated_at": _utc_now(),
        }
    )
    db.admin_users.insert_one(
        {
            "username": "ops_user",
            "display_name": "运维账号",
            "email": "",
            "role_slug": "ops",
            "status": "enabled",
            "password_hash": hash_password("ops_pass_123"),
            "last_login": None,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
    )
    mongo_client.close()

    admin_user = os.getenv("TEST_ADMIN_USER", "e2e_admin")
    admin_pass = os.getenv("TEST_ADMIN_PASS", "e2e_pass_123")
    session = _login_http_client(e2e_base_url, admin_user, admin_pass)

    unmapped = session.get("/admin/not-mapped")
    assert unmapped.status_code == 403
    assert "未注册权限映射" in unmapped.text

    protected = session.delete("/admin/rbac/roles/viewer")
    assert protected.status_code == 400
    assert protected.json()["detail"] == "系统内置角色不允许删除"

    in_use = session.delete("/admin/rbac/roles/ops")
    assert in_use.status_code == 400
    assert in_use.json()["detail"] == "该角色仍被管理员使用，无法删除"

    session.close()
