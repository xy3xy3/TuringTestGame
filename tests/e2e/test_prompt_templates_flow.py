from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from playwright.sync_api import Error, expect, sync_playwright
from pymongo import MongoClient


def _parse_room_id(url: str) -> str:
    """从房间页面 URL 解析 room_id。"""

    match = re.search(r"/game/([^/?#]+)", url)
    assert match, f"无法从 URL 解析 room_id: {url}"
    return match.group(1)


@pytest.mark.e2e
def test_prompt_templates_seed_and_setup_apply(
    e2e_base_url: str,
    test_mongo_url: str,
    e2e_mongo_db_name: str,
) -> None:
    """后台一键添加提示词模板后，灵魂注入页应可下拉套用并自动填充。"""

    admin_user = os.getenv("TEST_ADMIN_USER", "e2e_admin")
    admin_pass = os.getenv("TEST_ADMIN_PASS", "e2e_pass_123")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Error as exc:
            pytest.skip(f"Chromium not installed for Playwright: {exc}")

        admin_ctx = browser.new_context()
        admin_page = admin_ctx.new_page()
        admin_page.goto(f"{e2e_base_url}/admin/login", wait_until="networkidle")
        admin_page.locator("input[name=username]").fill(admin_user)
        admin_page.locator("input[name=password]").fill(admin_pass)
        admin_page.get_by_role("button", name="登录").click()
        admin_page.wait_for_url("**/admin/dashboard")

        admin_page.goto(f"{e2e_base_url}/admin/prompt_templates", wait_until="networkidle")
        expect(admin_page.get_by_role("heading", name="提示词模板")).to_be_visible()
        expect(admin_page.locator('.sider-tree a[href="/admin/prompt_templates"]')).to_be_visible()
        expect(admin_page.locator(".breadcrumb-muted")).to_have_text("游戏管理")
        expect(admin_page.locator(".breadcrumb-current")).to_have_text("提示词模板")

        admin_page.once("dialog", lambda dialog: dialog.accept())
        admin_page.get_by_role("button", name="一键添加预置模板").click()

        expect(admin_page.locator("#prompt_templates-table")).to_contain_text("懒男大短句")
        expect(admin_page.locator("#prompt_templates-table")).to_contain_text("网瘾室友版")
        expect(admin_page.locator("#prompt_templates-table")).to_contain_text("社恐路人版")

        player_ctx = browser.new_context()
        player_page = player_ctx.new_page()
        player_page.goto(f"{e2e_base_url}/game", wait_until="networkidle")
        player_page.locator('#create-form input[name="nickname"]').fill("模板测试玩家")
        player_page.get_by_role("button", name="创建房间").click()
        player_page.wait_for_url("**/game/*", timeout=20_000)

        room_id = _parse_room_id(player_page.url)
        mongo_client = MongoClient(test_mongo_url)
        try:
            db = mongo_client[e2e_mongo_db_name]
            update_result = db.game_rooms.update_one(
                {"_id": ObjectId(room_id)},
                {
                    "$set": {
                        "phase": "setup",
                        "started_at": datetime.now(timezone.utc),
                    }
                },
            )
            assert update_result.matched_count == 1
        finally:
            mongo_client.close()

        player_page.goto(f"{e2e_base_url}/game/{room_id}/setup", wait_until="domcontentloaded")
        expect(player_page.get_by_role("heading", name="灵魂注入")).to_be_visible()
        expect(player_page.locator("#prompt-template-select option")).to_have_count(4)

        player_page.eval_on_selector(
            "#prompt-template-select",
            """
            (el) => {
              const target = [...el.options].find((opt) => opt.textContent.includes('懒男大短句'));
              if (!target) throw new Error('未找到预置模板选项');
              el.value = target.value;
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """,
        )

        expect(player_page.locator('#setup-form textarea[name="system_prompt"]')).to_have_value(
            re.compile("普通中国男大学生")
        )

        browser.close()
