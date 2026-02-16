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
        page.wait_for_url(f"**/game/{room_id}/setup", timeout=20_000)
    expect(page.get_by_role("heading", name="灵魂注入")).to_be_visible()


def _lock_setup(page) -> None:
    textarea = page.locator('textarea[name="system_prompt"]')
    textarea.fill("E2E: 附加给分机制测试，请简短回答。")
    page.get_by_role("button", name="锁定设置").click()
    expect(page.locator("#setup-result")).to_contain_text("设置已保存", timeout=10_000)


def _wait_play(page, room_id: str) -> None:
    if f"/game/{room_id}/play" not in page.url:
        page.wait_for_url(f"**/game/{room_id}/play", timeout=30_000)
    expect(page.locator("#phase-display")).to_be_visible()


def _wait_roles(page) -> tuple[str, str]:
    interrogator = page.locator("#interrogator-name")
    subject = page.locator("#subject-name")
    expect(interrogator).not_to_have_text(re.compile("等待中"), timeout=20_000)
    expect(subject).not_to_have_text(re.compile("等待中"), timeout=20_000)
    return interrogator.inner_text().strip(), subject.inner_text().strip()


def _ask_question(page, question: str) -> None:
    expect(page.locator("#question-input-area")).to_be_visible(timeout=20_000)
    page.locator("#question-input").fill(question)
    page.get_by_role("button", name="提问").click()


def _choose_ai_answer(page) -> None:
    expect(page.locator("#answer-choice-area")).to_be_visible(timeout=20_000)
    page.locator("#answer-choice-area").locator("button").filter(has_text="AI").first.click()


def _vote(page, choice_text: str) -> None:
    expect(page.locator("#vote-area")).to_be_visible(timeout=20_000)
    page.locator("#vote-area").locator("button").filter(has_text=choice_text).first.click()
    expect(page.locator("#wait-area")).to_be_visible(timeout=10_000)


def _assert_bonus_scoring_enabled(owner_page, room_id: str, watchers: list) -> None:
    for watcher in watchers:
        expect(watcher.locator("#bonus-scoring-status")).to_contain_text("已开启", timeout=10_000)
    state = owner_page.evaluate(
        """(rid) => fetch(`/game/api/${rid}/state`)
            .then(r => r.json())
            .then(d => Boolean(d.room?.config?.bonus_scoring_enabled))""",
        room_id,
    )
    assert state is True


def _prepare_three_player_game(e2e_base_url: str):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Error as exc:
            pytest.skip(f"Chromium not installed for Playwright: {exc}")

        owner_ctx = browser.new_context()
        p2_ctx = browser.new_context()
        p3_ctx = browser.new_context()
        owner = owner_ctx.new_page()
        p2 = p2_ctx.new_page()
        p3 = p3_ctx.new_page()

        owner.goto(f"{e2e_base_url}/game/create", wait_until="networkidle")
        owner.locator('#create-form input[name="nickname"]').fill("P1")
        owner.locator('#create-form input[name="bonus_scoring_enabled"]').check()
        owner.get_by_role("button", name="创建房间").click()
        owner.wait_for_url(re.compile(r".*/game/[0-9a-f]{24}$"), timeout=20_000)
        room_id = _parse_room_id(owner.url)
        _wait_room_ready(owner)
        room_code = owner.locator("text=房间号：").locator("span").inner_text().strip()

        for page, nickname in [(p2, "P2"), (p3, "P3")]:
            page.goto(f"{e2e_base_url}/game/join", wait_until="networkidle")
            page.locator('#join-form input[name="room_code"]').fill(room_code)
            page.locator('#join-form input[name="nickname"]').fill(nickname)
            page.get_by_role("button", name="加入房间").click()
            page.wait_for_url(f"**/game/{room_id}", timeout=20_000)
            _wait_room_ready(page)

        _assert_bonus_scoring_enabled(owner, room_id, [owner, p2, p3])

        _click_ready(p2)
        _click_ready(p3)
        _click_ready(owner)

        start_btn = owner.locator("#start-btn")
        expect(start_btn).to_be_enabled(timeout=20_000)
        start_btn.click()

        owner.wait_for_function(
            """(roomId) => fetch(`/game/api/${roomId}/state`)
              .then(r => r.json())
              .then(d => d.success && d.room && (d.room.phase === 'setup' || d.room.phase === 'playing'))
              .catch(() => false)""",
            arg=room_id,
            timeout=20_000,
        )

        for page in [owner, p2, p3]:
            page.goto(f"{e2e_base_url}/game/{room_id}/setup", wait_until="domcontentloaded")
            _wait_setup(page, room_id)

        for page in [owner, p2, p3]:
            _lock_setup(page)

        for page in [owner, p2, p3]:
            _wait_play(page, room_id)

        yield browser, room_id, {"P1": owner, "P2": p2, "P3": p3}

        browser.close()


@pytest.mark.e2e
def test_bonus_scoring_subject_ai_bonus(e2e_base_url: str) -> None:
    """开启附加机制后，被测者使用 AI 且骗过所有陪审团应额外 +50。"""
    for _, room_id, pages in _prepare_three_player_game(e2e_base_url):
        interrogator_name, subject_name = _wait_roles(next(iter(pages.values())))
        juror_name = next(name for name in pages.keys() if name not in {interrogator_name, subject_name})

        interrogator_page = pages[interrogator_name]
        subject_page = pages[subject_name]
        juror_page = pages[juror_name]

        _ask_question(interrogator_page, "E2E: 附加机制测试问题")
        for page in pages.values():
            expect(page.locator("#question-text")).to_contain_text("E2E: 附加机制测试问题", timeout=20_000)

        _choose_ai_answer(subject_page)
        for page in pages.values():
            expect(page.locator("#answer-text")).to_contain_text("MOCK_AI_FIXED_REPLY", timeout=20_000)

        _vote(interrogator_page, "真人")
        _vote(juror_page, "真人")

        expect(subject_page.locator("#round-feedback-card")).to_be_visible(timeout=20_000)
        expect(subject_page.locator("#round-feedback-card")).to_contain_text("触发附加奖励")
        expect(subject_page.locator("#round-feedback-card")).to_contain_text("本轮得分变化：+50 分")

        expect(interrogator_page.locator("#round-feedback-card")).to_contain_text("本轮得分变化：-30 分", timeout=20_000)
        expect(juror_page.locator("#round-feedback-card")).to_contain_text("本轮得分变化：-30 分", timeout=20_000)

        owner_page = pages["P1"]
        owner_page.wait_for_url(f"**/game/{room_id}/result?data=*", timeout=60_000)
        parsed = urlparse(owner_page.url)
        data_param = parse_qs(parsed.query).get("data", [])
        assert data_param
        game_data = json.loads(unquote(data_param[0]))
        leaderboard = game_data.get("leaderboard") or []
        assert len(leaderboard) == 3

        expected_scores = {
            subject_name: 50,
            interrogator_name: -30,
            juror_name: -30,
        }
        for entry in leaderboard:
            assert expected_scores[entry["nickname"]] == entry["score"]


@pytest.mark.e2e
def test_bonus_scoring_interrogator_bonus(e2e_base_url: str) -> None:
    """开启附加机制后，提问者让所有陪审团答对应额外 +50。"""
    for _, room_id, pages in _prepare_three_player_game(e2e_base_url):
        interrogator_name, subject_name = _wait_roles(next(iter(pages.values())))
        juror_name = next(name for name in pages.keys() if name not in {interrogator_name, subject_name})

        interrogator_page = pages[interrogator_name]
        subject_page = pages[subject_name]
        juror_page = pages[juror_name]

        _ask_question(interrogator_page, "E2E: 提问者奖励测试问题")
        _choose_ai_answer(subject_page)
        for page in pages.values():
            expect(page.locator("#answer-text")).to_contain_text("MOCK_AI_FIXED_REPLY", timeout=20_000)

        _vote(interrogator_page, "AI")
        _vote(juror_page, "AI")

        expect(interrogator_page.locator("#round-feedback-card")).to_be_visible(timeout=20_000)
        expect(interrogator_page.locator("#round-feedback-card")).to_contain_text("本轮得分变化：+100 分")
        expect(interrogator_page.locator("#round-feedback-card")).to_contain_text("提问者附加奖励 +50 分")

        expect(subject_page.locator("#round-feedback-card")).to_be_visible(timeout=20_000)
        expect(subject_page.locator("#round-feedback-card")).to_contain_text("未触发被测者奖励")
        expect(subject_page.locator("#round-feedback-card")).to_contain_text("本轮得分变化：0 分")

        owner_page = pages["P1"]
        owner_page.wait_for_url(f"**/game/{room_id}/result?data=*", timeout=60_000)
        parsed = urlparse(owner_page.url)
        data_param = parse_qs(parsed.query).get("data", [])
        assert data_param
        game_data = json.loads(unquote(data_param[0]))
        leaderboard = game_data.get("leaderboard") or []
        assert len(leaderboard) == 3

        expected_scores = {
            interrogator_name: 100,
            juror_name: 50,
            subject_name: 0,
        }
        for entry in leaderboard:
            assert expected_scores[entry["nickname"]] == entry["score"]
