from __future__ import annotations

import re

import pytest
from playwright.sync_api import Error, expect, sync_playwright


def _parse_room_id(url: str) -> str:
    match = re.search(r"/game/([^/?#]+)", url)
    assert match, f"无法从 URL 解析 room_id: {url}"
    return match.group(1)


def _wait_room_ready(page) -> None:
    expect(page.get_by_role("heading", name="房间大厅")).to_be_visible()
    expect(page.locator("#player-list")).to_be_visible()


def _wait_setup(page, room_id: str) -> None:
    if f"/game/{room_id}/setup" not in page.url:
        page.wait_for_url(f"**/game/{room_id}/setup", timeout=20_000)
    expect(page.get_by_role("heading", name="灵魂注入")).to_be_visible()


def _lock_setup(page, prompt: str) -> None:
    textarea = page.locator('textarea[name="system_prompt"]')
    textarea.fill(prompt)
    page.get_by_role("button", name="锁定设置").click()
    expect(page.locator("#setup-result")).to_contain_text("设置已保存", timeout=10_000)


@pytest.mark.e2e
def test_room_page_polling_recovers_when_sse_unavailable(e2e_base_url: str) -> None:
    """房间页 SSE 断开后，仍可通过轮询同步玩家准备状态。"""

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Error as exc:
            pytest.skip(f"Chromium not installed for Playwright: {exc}")

        owner_ctx = browser.new_context()
        p2_ctx = browser.new_context()

        owner = owner_ctx.new_page()
        p2 = p2_ctx.new_page()

        owner.goto(f"{e2e_base_url}/game", wait_until="networkidle")
        owner.locator('#create-form input[name="nickname"]').fill("P1")
        owner.get_by_role("button", name="创建房间").click()
        owner.wait_for_url("**/game/*", timeout=20_000)
        room_id = _parse_room_id(owner.url)
        _wait_room_ready(owner)

        room_code = owner.locator("text=房间号：").locator("span").inner_text().strip()
        assert room_code

        p2.goto(f"{e2e_base_url}/game", wait_until="networkidle")
        p2.locator('#join-form input[name="room_code"]').fill(room_code)
        p2.locator('#join-form input[name="nickname"]').fill("P2")
        p2.get_by_role("button", name="加入房间").click()
        p2.wait_for_url(f"**/game/{room_id}", timeout=20_000)
        _wait_room_ready(p2)

        # 阻断 owner 页 SSE，请求将持续失败，触发前端轮询兜底。
        owner.route(f"**/game/{room_id}/events", lambda route: route.abort())
        owner.reload(wait_until="domcontentloaded")
        _wait_room_ready(owner)

        p2.locator("#ready-btn").click()
        expect(p2.locator("#ready-btn")).to_contain_text(re.compile(r"准备|取消准备"))

        # owner 页虽无 SSE，但应通过 /state + /players 轮询感知 P2 已准备。
        expect(owner.locator('#player-list > div:has-text("P2"):has-text("已准备")')).to_be_visible(timeout=15_000)

        browser.close()


@pytest.mark.e2e
def test_setup_page_polling_can_enter_play_without_sse(e2e_base_url: str) -> None:
    """setup 页 SSE 不可用时，依赖状态轮询仍可跳转到 play。"""

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Error as exc:
            pytest.skip(f"Chromium not installed for Playwright: {exc}")

        owner_ctx = browser.new_context()
        p2_ctx = browser.new_context()

        owner = owner_ctx.new_page()
        p2 = p2_ctx.new_page()

        owner.goto(f"{e2e_base_url}/game", wait_until="networkidle")
        owner.locator('#create-form input[name="nickname"]').fill("P1")
        owner.get_by_role("button", name="创建房间").click()
        owner.wait_for_url("**/game/*", timeout=20_000)
        room_id = _parse_room_id(owner.url)
        _wait_room_ready(owner)

        room_code = owner.locator("text=房间号：").locator("span").inner_text().strip()
        assert room_code

        p2.goto(f"{e2e_base_url}/game", wait_until="networkidle")
        p2.locator('#join-form input[name="room_code"]').fill(room_code)
        p2.locator('#join-form input[name="nickname"]').fill("P2")
        p2.get_by_role("button", name="加入房间").click()
        p2.wait_for_url(f"**/game/{room_id}", timeout=20_000)
        _wait_room_ready(p2)

        p2.locator("#ready-btn").click()
        owner.locator("#ready-btn").click()

        start_btn = owner.locator("#start-btn")
        expect(start_btn).to_be_visible()
        expect(start_btn).to_be_enabled(timeout=20_000)
        start_btn.click()

        # 等待进入 setup 阶段后再打开 setup 页，避免已进入 playing 导致跳转干扰断言。
        owner.wait_for_function(
            """(rid) => fetch(`/game/api/${rid}/state`)
              .then(r => r.json())
              .then(d => d.success && d.room && d.room.phase === 'setup')
              .catch(() => false)""",
            arg=room_id,
            timeout=20_000,
        )

        # 阻断 p2 setup 页 SSE，验证仅靠轮询也可在 setup 结束后进入 play。
        p2.route(f"**/game/{room_id}/events", lambda route: route.abort())
        p2.goto(f"{e2e_base_url}/game/{room_id}/setup", wait_until="domcontentloaded")
        _wait_setup(p2, room_id)

        _lock_setup(p2, "E2E: p2 setup without SSE")

        p2.wait_for_url(f"**/game/{room_id}/play", timeout=40_000)
        expect(p2.locator("#phase-display")).to_be_visible(timeout=10_000)

        browser.close()
