from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from app.services import game_room_service
from app.services.game_manager import GameManager, sse_manager

game_manager_module = importlib.import_module("app.services.game_manager")


class _FakeRound:
    """用于测试回合倒计时强制提交的轻量对象。"""

    def __init__(self) -> None:
        self.question = ""
        self.question_draft = ""
        self.question_at = None
        self.interrogator_id = "player-1"
        self.answer = ""
        self.answer_draft = ""
        self.answer_type = "human"
        self.answer_submitted_at = None
        self.saved = 0

    async def save(self) -> None:
        self.saved += 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_question_timer_uses_draft_content(monkeypatch) -> None:
    """提问倒计时结束时应优先使用草稿问题。"""

    manager = GameManager()
    fake_room = SimpleNamespace(config=SimpleNamespace(question_duration=1))
    fake_round = _FakeRound()
    fake_round.question_draft = "这是草稿问题"
    published_events: list[tuple[str, dict[str, object]]] = []
    started: list[tuple[str, str]] = []

    async def fake_get_room_by_id(_room_id: str):
        return fake_room

    async def fake_get_round(_round_id):
        return fake_round

    async def fake_publish(_room_id: str, event: str, data: dict[str, object]):
        published_events.append((event, data))

    async def fake_sleep(_seconds: float):
        return None

    async def fake_start_answer_phase(room_id: str, round_id: str):
        started.append((room_id, round_id))

    monkeypatch.setattr(game_room_service, "get_room_by_id", fake_get_room_by_id)
    monkeypatch.setattr(game_manager_module.GameRound, "get", fake_get_round)
    monkeypatch.setattr(sse_manager, "publish", fake_publish)
    monkeypatch.setattr(game_manager_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(manager, "_start_answer_phase", fake_start_answer_phase)

    await manager._start_question_timer("room-1", "507f1f77bcf86cd799439011")

    assert fake_round.question == "这是草稿问题"
    assert fake_round.question_at is not None
    assert any(event == "new_question" for event, _data in published_events)
    assert started == [("room-1", "507f1f77bcf86cd799439011")]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_answer_timer_uses_draft_content(monkeypatch) -> None:
    """回答倒计时结束时应优先使用草稿回答并视为真人回答。"""

    manager = GameManager()
    fake_room = SimpleNamespace(config=SimpleNamespace(answer_duration=1))
    fake_round = _FakeRound()
    fake_round.answer_draft = "我已经输入了一半"
    published_events: list[tuple[str, dict[str, object]]] = []
    scheduled_tasks: list[tuple[str, str, float]] = []

    async def fake_get_room_by_id(_room_id: str):
        return fake_room

    async def fake_get_round(_round_id):
        return fake_round

    async def fake_publish(_room_id: str, event: str, data: dict[str, object]):
        published_events.append((event, data))

    async def fake_sleep(_seconds: float):
        return None

    async def fake_delay(*_args, **_kwargs):
        return 0.0

    async def fake_delayed_answer_display(room_id: str, round_id: str, delay: float):
        scheduled_tasks.append((room_id, round_id, delay))

    def fake_create_task(coro):
        try:
            coro.send(None)
        except StopIteration:
            pass
        return SimpleNamespace()

    monkeypatch.setattr(game_room_service, "get_room_by_id", fake_get_room_by_id)
    monkeypatch.setattr(game_manager_module.GameRound, "get", fake_get_round)
    monkeypatch.setattr(sse_manager, "publish", fake_publish)
    monkeypatch.setattr(game_manager_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(game_manager_module.ai_chat_service, "calculate_display_delay", fake_delay)
    monkeypatch.setattr(manager, "_delayed_answer_display", fake_delayed_answer_display)
    monkeypatch.setattr(game_manager_module.asyncio, "create_task", fake_create_task)

    await manager._start_answer_timer("room-2", "507f1f77bcf86cd799439012")

    assert fake_round.answer == "我已经输入了一半"
    assert fake_round.answer_type == "human"
    assert fake_round.answer_draft == ""
    assert fake_round.answer_submitted_at is not None
    assert any(event == "answer_submitted" for event, _data in published_events)
    assert scheduled_tasks == [("room-2", "507f1f77bcf86cd799439012", 0.0)]
