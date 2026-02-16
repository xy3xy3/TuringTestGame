from __future__ import annotations

import re

import pytest
from playwright.sync_api import Error, expect, sync_playwright


def _parse_room_id(url: str) -> str:
    match = re.search(r"/game/([^/?#]+)", url)
    assert match, f"无法从 URL 解析 room_id: {url}"
    return match.group(1)


def _read_room_code(page) -> str:
    return page.locator("text=房间号：").locator("span").inner_text().strip()


@pytest.mark.e2e
def test_room_list_show_locked_and_public_rooms_and_join_locked(e2e_base_url: str) -> None:
    """房间列表应展示有密码/无密码房间，并支持输入密码加入加锁房间。"""

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Error as exc:
            pytest.skip(f"Chromium not installed for Playwright: {exc}")

        owner_locked_ctx = browser.new_context()
        owner_public_ctx = browser.new_context()
        joiner_ctx = browser.new_context()

        owner_locked = owner_locked_ctx.new_page()
        owner_public = owner_public_ctx.new_page()
        joiner = joiner_ctx.new_page()

        # 1) 创建加锁房间
        owner_locked.goto(f"{e2e_base_url}/game/create", wait_until="networkidle")
        owner_locked.locator('#create-form input[name="nickname"]').fill("LockHost")
        owner_locked.locator('#create-form input[name="password"]').fill("123456")
        owner_locked.get_by_role("button", name="创建房间").click()
        owner_locked.wait_for_url(re.compile(r".*/game/[0-9a-f]{24}$"), timeout=20_000)
        locked_room_id = _parse_room_id(owner_locked.url)
        locked_room_code = _read_room_code(owner_locked)
        assert locked_room_code

        # 2) 创建公开房间
        owner_public.goto(f"{e2e_base_url}/game/create", wait_until="networkidle")
        owner_public.locator('#create-form input[name="nickname"]').fill("OpenHost")
        owner_public.get_by_role("button", name="创建房间").click()
        owner_public.wait_for_url(re.compile(r".*/game/[0-9a-f]{24}$"), timeout=20_000)
        public_room_code = _read_room_code(owner_public)
        assert public_room_code

        # 3) 打开房间列表页，验证两个房间均展示，且锁图标符合预期
        joiner.goto(f"{e2e_base_url}/game", wait_until="networkidle")
        expect(joiner.get_by_role("heading", name="房间列表")).to_be_visible()

        locked_card = joiner.locator(f'[data-room-card][data-room-code="{locked_room_code}"]')
        public_card = joiner.locator(f'[data-room-card][data-room-code="{public_room_code}"]')
        expect(locked_card).to_be_visible(timeout=10_000)
        expect(public_card).to_be_visible(timeout=10_000)

        expect(locked_card.locator(".fa-lock")).to_be_visible()
        expect(public_card.locator(".fa-lock-open")).to_be_visible()

        # 4) 通过房间列表跳转到加入页，房间号应自动带入
        joiner.locator(f'[data-join-room="{locked_room_code}"]').click()
        joiner.wait_for_url(f"**/game/join?room={locked_room_code}", timeout=20_000)
        expect(joiner.locator("#join-form")).to_be_visible(timeout=10_000)
        expect(joiner.locator('#join-form input[name=\"room_code\"]')).to_have_value(locked_room_code)

        joiner.locator('#join-form input[name="nickname"]').fill("Joiner")
        joiner.locator('#join-form input[name="password"]').fill("123456")
        joiner.locator("#join-form").get_by_role("button", name="加入房间").click()

        joiner.wait_for_url(f"**/game/{locked_room_id}", timeout=20_000)
        expect(joiner.get_by_role("heading", name="房间大厅")).to_be_visible()

        browser.close()
