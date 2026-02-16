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
    # 等待按钮文案变更（准备/取消准备）以确认 HTMX 请求生效
    expect(page.locator("#ready-btn")).to_contain_text(re.compile(r"准备|取消准备"))


def _wait_setup(page, room_id: str) -> None:
    if f"/game/{room_id}/setup" not in page.url:
        page.wait_for_url(f"**/game/{room_id}/setup", timeout=20_000)
    expect(page.get_by_role("heading", name="灵魂注入")).to_be_visible()


def _lock_setup(page) -> None:
    textarea = page.locator('textarea[name="system_prompt"]')
    textarea.fill("E2E: 你是一个测试机器人，请简短回答。")
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


@pytest.mark.e2e
def test_game_three_players_full_flow_and_leaderboard(e2e_base_url: str) -> None:
    """三人完整跑通：创建/加入/准备/开始/提问/AI 回答/投票/结算/排行榜。"""

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

        # 1) 房主创建房间
        owner.goto(f"{e2e_base_url}/game/create", wait_until="networkidle")
        owner.locator('#create-form input[name="nickname"]').fill("P1")
        owner.get_by_role("button", name="创建房间").click()
        owner.wait_for_url(re.compile(r".*/game/[0-9a-f]{24}$"), timeout=20_000)
        room_id = _parse_room_id(owner.url)
        _wait_room_ready(owner)

        room_code = owner.locator("text=房间号：").locator("span").inner_text().strip()
        assert room_code, "未获取到房间号"

        # 2) 两名玩家加入房间
        for page, nickname in [(p2, "P2"), (p3, "P3")]:
            page.goto(f"{e2e_base_url}/game/join", wait_until="networkidle")
            page.locator('#join-form input[name="room_code"]').fill(room_code)
            page.locator('#join-form input[name="nickname"]').fill(nickname)
            page.get_by_role("button", name="加入房间").click()
            page.wait_for_url(f"**/game/{room_id}", timeout=20_000)
            _wait_room_ready(page)

        # 2.1) 玩家离开再加入（验证 room.html SSE 列表刷新 + join/leave 流程）
        expect(owner.locator("#player-list > div")).to_have_count(3, timeout=10_000)
        expect(p2.locator("#player-list > div")).to_have_count(3, timeout=10_000)
        expect(owner.locator("#player-count")).to_have_text("3", timeout=10_000)
        expect(p2.locator("#player-count")).to_have_text("3", timeout=10_000)

        p3.once("dialog", lambda dialog: dialog.accept())
        p3.get_by_role("button", name="离开").click()
        p3.wait_for_url("**/game", timeout=20_000)

        expect(owner.locator("#player-list > div")).to_have_count(2, timeout=10_000)
        expect(p2.locator("#player-list > div")).to_have_count(2, timeout=10_000)
        expect(owner.locator("#player-count")).to_have_text("2", timeout=10_000)
        expect(p2.locator("#player-count")).to_have_text("2", timeout=10_000)

        p3.goto(f"{e2e_base_url}/game/join", wait_until="networkidle")
        p3.locator('#join-form input[name="room_code"]').fill(room_code)
        p3.locator('#join-form input[name="nickname"]').fill("P3")
        p3.get_by_role("button", name="加入房间").click()
        p3.wait_for_url(f"**/game/{room_id}", timeout=20_000)
        _wait_room_ready(p3)

        expect(owner.locator("#player-list > div")).to_have_count(3, timeout=10_000)
        expect(p2.locator("#player-list > div")).to_have_count(3, timeout=10_000)
        expect(owner.locator("#player-count")).to_have_text("3", timeout=10_000)
        expect(p2.locator("#player-count")).to_have_text("3", timeout=10_000)

        # 3) 三人都准备
        _click_ready(p2)
        _click_ready(p3)
        _click_ready(owner)

        # 4) 房主开始游戏（等待 start 按钮启用）
        start_btn = owner.locator("#start-btn")
        expect(start_btn).to_be_visible()
        expect(start_btn).to_be_enabled(timeout=20_000)
        start_btn.click()

        # 5) 确认游戏已进入 setup/playing（以 API 状态为准），然后显式打开 setup 页面。
        owner.wait_for_function(
            """(roomId) => fetch(`/game/api/${roomId}/state`)
              .then(r => r.json())
              .then(d => d.success && d.room && (d.room.phase === 'setup' || d.room.phase === 'playing'))
              .catch(() => false)""",
            arg=room_id,
            timeout=20_000,
        )

        # 5.0) 游戏已开始后，第 4 人加入应被拒绝
        p4_ctx = browser.new_context()
        p4 = p4_ctx.new_page()
        p4.goto(f"{e2e_base_url}/game/join", wait_until="domcontentloaded")
        p4.locator('#join-form input[name="room_code"]').fill(room_code)
        p4.locator('#join-form input[name="nickname"]').fill("P4")
        p4.get_by_role("button", name="加入房间").click()
        expect(p4.locator("#join-result")).to_contain_text("游戏已开始", timeout=10_000)

        for page in [owner, p2, p3]:
            page.goto(f"{e2e_base_url}/game/{room_id}/setup", wait_until="domcontentloaded")
            _wait_setup(page, room_id)

        # 5.1) reconnect API（只要能返回 redirect 且与当前房间匹配即可）
        reconnect_data = owner.evaluate(
            "fetch('/game/reconnect', {method: 'POST'}).then(r => r.json())"
        )
        assert reconnect_data.get("success") is True
        assert reconnect_data.get("room_id") == room_id
        assert reconnect_data.get("redirect", "").startswith(f"/game/{room_id}")

        # 6) 锁定灵魂注入（不依赖 AI 模型配置）
        _lock_setup(owner)
        _lock_setup(p2)
        _lock_setup(p3)

        # 7) setup 倒计时结束后进入 play（测试环境阶段时长已缩短）
        _wait_play(owner, room_id)
        _wait_play(p2, room_id)
        _wait_play(p3, room_id)

        pages = {"P1": owner, "P2": p2, "P3": p3}

        # 8) 获取本轮角色
        interrogator_name, subject_name = _wait_roles(owner)
        assert interrogator_name in pages, f"未知提问者: {interrogator_name}"
        assert subject_name in pages, f"未知被测者: {subject_name}"

        interrogator_page = pages[interrogator_name]
        subject_page = pages[subject_name]
        juror_names = [n for n in pages.keys() if n not in {interrogator_name, subject_name}]
        assert len(juror_names) == 1, "三人局应只有 1 名陪审团（除提问者与被测者外）"
        juror_page = pages[juror_names[0]]

        # 8.1) 刷新页面后仍能继续接收 SSE 并参与流程（测试 initGameState + SSE 重连）
        juror_page.reload(wait_until="domcontentloaded")
        _wait_play(juror_page, room_id)

        # 9) 提问 -> AI 回答 -> 投票
        question = "E2E: 你是谁？"
        _ask_question(interrogator_page, question)

        for page in [owner, p2, p3]:
            expect(page.locator("#question-text")).to_contain_text(question, timeout=20_000)

        _choose_ai_answer(subject_page)

        # AI 固定回复应出现在所有玩家页面
        for page in [owner, p2, p3]:
            expect(page.locator("#answer-text")).to_contain_text("MOCK_AI_FIXED_REPLY", timeout=20_000)

        # 9.1) 被测者直接请求投票接口应被后端拒绝（即使绕过前端 UI）
        subject_page.wait_for_function(
            "() => document.querySelector('#phase-display')?.textContent?.includes('投票') || false",
            timeout=20_000,
        )
        vote_bypass_response = subject_page.evaluate(
            """async (roomId) => {
              const roundRes = await fetch(`/game/api/${roomId}/round`);
              const roundData = await roundRes.json();
              const rid = roundData?.round?.id || '';
              const resp = await fetch(`/game/${roomId}/vote`, {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: `vote=human&round_id=${encodeURIComponent(rid)}`
              });
              return await resp.text();
            }""",
            room_id,
        )
        assert "被测者不能投票" in vote_bypass_response

        # 被测者不能投票；提问者与陪审团都可投票（两人）
        voter_pages = [pages[interrogator_name], juror_page]
        _vote(voter_pages[0], "AI")  # 猜对
        _vote(voter_pages[1], "真人")  # 猜错

        # 被测者页不应出现投票按钮
        expect(subject_page.locator("#vote-area")).to_be_hidden()

        # 9.2) 投票结算反馈：显示“猜对/猜错”与本轮分值影响
        expect(voter_pages[0].locator("#round-feedback-card")).to_be_visible(timeout=20_000)
        expect(voter_pages[0].locator("#round-feedback-card")).to_contain_text("你本轮猜对了")
        expect(voter_pages[0].locator("#round-feedback-card")).to_contain_text("本轮得分变化：+50 分")

        expect(voter_pages[1].locator("#round-feedback-card")).to_be_visible(timeout=20_000)
        expect(voter_pages[1].locator("#round-feedback-card")).to_contain_text("你本轮猜错了")
        expect(voter_pages[1].locator("#round-feedback-card")).to_contain_text("本轮得分变化：-30 分")

        expect(subject_page.locator("#round-feedback-card")).to_be_visible(timeout=20_000)
        expect(subject_page.locator("#round-feedback-card")).to_contain_text("你本轮作为被测者")
        expect(subject_page.locator("#round-feedback-card")).to_contain_text("本轮不参与计分")
        expect(subject_page.locator("#round-feedback-card")).to_contain_text("本轮得分变化：0 分")

        # 10) 游戏结束并跳转到结果页
        #
        # 说明：
        # - 结果页跳转依赖 SSE 推送与前端重定向，三开页面在 CI/低性能环境下可能出现个别页面未及时跳转的偶发情况。
        # - 这里以“房主能跳转”为强断言，其它玩家若未跳转则直接跟随房主结果页 URL，继续校验排行榜渲染。
        owner.wait_for_url(
            f"**/game/{room_id}/result?data=*",
            timeout=60_000,
            wait_until="domcontentloaded",
        )
        result_url = owner.url

        for page in [owner, p2, p3]:
            if f"/game/{room_id}/result" not in page.url:
                page.goto(result_url, wait_until="domcontentloaded")
            expect(page.locator("#leaderboard")).to_be_visible()
            # 等待 JS 渲染 3 行排行榜
            expect(page.locator("#leaderboard > div")).to_have_count(3, timeout=20_000)

        # 11) 校验排行榜包含三名玩家昵称
        leaderboard_text = owner.locator("#leaderboard").inner_text()
        assert "P1" in leaderboard_text
        assert "P2" in leaderboard_text
        assert "P3" in leaderboard_text

        # 12) 解析结果数据，校验排行榜顺序与分数（新规则：所有投票玩家计分）
        parsed = urlparse(owner.url)
        data_param = parse_qs(parsed.query).get("data", [])
        assert data_param, "结果页 URL 缺少 data 参数"
        game_data = json.loads(unquote(data_param[0]))
        leaderboard = game_data.get("leaderboard") or []
        assert len(leaderboard) == 3

        correct_voter = interrogator_name
        wrong_voter = juror_names[0]
        expected_scores = {
            subject_name: 0,
            correct_voter: 50,
            wrong_voter: -30,
        }

        for entry in leaderboard:
            nickname = entry.get("nickname")
            score = entry.get("score")
            assert nickname in expected_scores
            assert score == expected_scores[nickname]

        assert leaderboard[0]["nickname"] == correct_voter
        assert leaderboard[0]["score"] == 50
        assert leaderboard[1]["score"] == 0
        assert leaderboard[2]["score"] == -30

        browser.close()
