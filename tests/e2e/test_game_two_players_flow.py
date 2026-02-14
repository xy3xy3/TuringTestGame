from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from playwright.sync_api import Error, expect, sync_playwright


def _parse_room_id(url: str) -> str:
    match = re.search(r"/game/([^/?#]+)", url)
    assert match, f"无法从 URL 解析 room_id: {url}"
    return match.group(1)


def _wait_room_ready(page) -> None:
    expect(page.get_by_role("heading", name="房间大厅")).to_be_visible()
    expect(page.locator("#player-list")).to_be_visible()
    expect(page.locator("#ready-btn")).to_be_visible()


def _click_ready(page) -> None:
    page.locator("#ready-btn").click()
    expect(page.locator("#ready-btn")).to_contain_text(re.compile(r"准备|取消准备"))


def _wait_setup(page, room_id: str) -> None:
    if f"/game/{room_id}/setup" not in page.url:
        page.wait_for_url(f"**/game/{room_id}/setup", timeout=30_000)
    expect(page.get_by_role("heading", name="灵魂注入")).to_be_visible()


def _lock_setup(page) -> None:
    textarea = page.locator('textarea[name="system_prompt"]')
    textarea.fill("E2E: 两人局测试，简短回答。")
    page.get_by_role("button", name="锁定设置").click()
    expect(page.locator("#setup-result")).to_contain_text("设置已保存", timeout=10_000)


def _wait_play(page, room_id: str) -> None:
    if f"/game/{room_id}/play" not in page.url:
        page.wait_for_url(f"**/game/{room_id}/play", timeout=40_000)
    expect(page.locator("#phase-display")).to_be_visible()


def _wait_roles(page) -> tuple[str, str]:
    interrogator = page.locator("#interrogator-name")
    subject = page.locator("#subject-name")
    expect(interrogator).not_to_have_text(re.compile("等待中"), timeout=25_000)
    expect(subject).not_to_have_text(re.compile("等待中"), timeout=25_000)
    return interrogator.inner_text().strip(), subject.inner_text().strip()


def _ask_question(page, question: str) -> None:
    expect(page.locator("#question-input-area")).to_be_visible(timeout=25_000)
    page.locator("#question-input").fill(question)
    page.get_by_role("button", name="提问").click()


def _choose_ai_answer(page) -> None:
    expect(page.locator("#answer-choice-area")).to_be_visible(timeout=25_000)
    page.locator("#answer-choice-area").locator("button").filter(has_text="AI").first.click()


def _vote(page, choice_text: str) -> None:
    expect(page.locator("#vote-area")).to_be_visible(timeout=25_000)
    page.locator("#vote-area").locator("button").filter(has_text=choice_text).first.click()
    expect(page.locator("#wait-area")).to_be_visible(timeout=10_000)


@pytest.mark.e2e
def test_game_two_players_full_flow_and_leaderboard(e2e_base_url: str) -> None:
    """二人完整跑通：创建/加入/准备/开始/灵魂注入/问答投票/结算/排行榜。"""

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Error as exc:
            pytest.skip(f"Chromium not installed for Playwright: {exc}")

        owner_ctx = browser.new_context()
        p2_ctx = browser.new_context()

        owner = owner_ctx.new_page()
        p2 = p2_ctx.new_page()

        # 1) 房主创建房间
        owner.goto(f"{e2e_base_url}/game", wait_until="networkidle")
        owner.locator('#create-form input[name="nickname"]').fill("P1")
        owner.get_by_role("button", name="创建房间").click()
        owner.wait_for_url("**/game/*", timeout=20_000)
        room_id = _parse_room_id(owner.url)
        _wait_room_ready(owner)

        room_code = owner.locator("text=房间号：").locator("span").inner_text().strip()
        assert room_code

        # 2) 第二名玩家加入
        p2.goto(f"{e2e_base_url}/game", wait_until="networkidle")
        p2.locator('#join-form input[name="room_code"]').fill(room_code)
        p2.locator('#join-form input[name="nickname"]').fill("P2")
        p2.get_by_role("button", name="加入房间").click()
        p2.wait_for_url(f"**/game/{room_id}", timeout=20_000)
        _wait_room_ready(p2)

        # 3) 两人准备
        _click_ready(p2)
        _click_ready(owner)

        # 4) 房主开始游戏
        start_btn = owner.locator("#start-btn")
        expect(start_btn).to_be_visible()
        expect(start_btn).to_be_enabled(timeout=20_000)
        start_btn.click()

        # 5) 等待进入 setup/playing，然后显式打开 setup（避免 networkidle + SSE 影响）
        owner.wait_for_function(
            """(roomId) => fetch(`/game/api/${roomId}/state`)
              .then(r => r.json())
              .then(d => d.success && d.room && (d.room.phase === 'setup' || d.room.phase === 'playing'))
              .catch(() => false)""",
            arg=room_id,
            timeout=20_000,
        )

        for page in [owner, p2]:
            page.goto(f"{e2e_base_url}/game/{room_id}/setup", wait_until="domcontentloaded")
            _wait_setup(page, room_id)

        # 6) 锁定灵魂注入
        _lock_setup(owner)
        _lock_setup(p2)

        # 7) 进入 play
        _wait_play(owner, room_id)
        _wait_play(p2, room_id)

        pages = {"P1": owner, "P2": p2}

        interrogator_name, subject_name = _wait_roles(owner)
        assert interrogator_name in pages
        assert subject_name in pages

        interrogator_page = pages[interrogator_name]
        subject_page = pages[subject_name]

        # 8) 提问 -> AI 回答 -> 投票（只有提问者能投票）
        question = "E2E: 两人局你是谁？"
        _ask_question(interrogator_page, question)

        for page in [owner, p2]:
            expect(page.locator("#question-text")).to_contain_text(question, timeout=25_000)

        _choose_ai_answer(subject_page)

        for page in [owner, p2]:
            expect(page.locator("#answer-text")).to_contain_text("MOCK_AI_FIXED_REPLY", timeout=25_000)

        # 被测者不能投票
        expect(subject_page.locator("#vote-area")).to_be_hidden()

        # 选择“真人”（猜错），两人局只有提问者可投票，因此仅提问者扣分
        _vote(interrogator_page, "真人")

        # 8.1) 投票结算反馈：显示“猜对/猜错”与本轮分值影响
        expect(interrogator_page.locator("#round-feedback-card")).to_be_visible(timeout=20_000)
        expect(interrogator_page.locator("#round-feedback-card")).to_contain_text("你本轮猜错了")
        expect(interrogator_page.locator("#round-feedback-card")).to_contain_text("本轮得分变化：-30 分")

        expect(subject_page.locator("#round-feedback-card")).to_be_visible(timeout=20_000)
        expect(subject_page.locator("#round-feedback-card")).to_contain_text("你本轮作为被测者")
        expect(subject_page.locator("#round-feedback-card")).to_contain_text("本轮不参与计分")
        expect(subject_page.locator("#round-feedback-card")).to_contain_text("本轮得分变化：0 分")

        # 9) 结算页
        for page in [owner, p2]:
            page.wait_for_url(f"**/game/{room_id}/result?data=*", timeout=40_000)
            expect(page.locator("#leaderboard")).to_be_visible()
            expect(page.locator("#leaderboard > div")).to_have_count(2, timeout=20_000)

        # 10) 校验结算数据
        # 两人局：仅提问者可投票；提问者猜错 -30，被测者 0 分。
        parsed = urlparse(owner.url)
        data_param = parse_qs(parsed.query).get("data", [])
        assert data_param
        game_data = json.loads(unquote(data_param[0]))
        leaderboard = game_data.get("leaderboard") or []
        assert len(leaderboard) == 2

        expected_scores = {
            subject_name: 0,
            interrogator_name: -30,
        }
        for entry in leaderboard:
            nickname = entry.get("nickname")
            score = entry.get("score")
            assert nickname in expected_scores
            assert score == expected_scores[nickname]

        assert leaderboard[0]["nickname"] == subject_name
        assert leaderboard[1]["nickname"] == interrogator_name

        browser.close()
