from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

# 先加载应用入口，避免单独导入控制器时触发模型包的循环导入。
import app.main  # noqa: F401
from app.apps.game.controllers import game as game_controller
from app.models.game_round import GameRound


def _build_request() -> Request:
    """构造最小可用的 Request，供控制器单测调用。"""
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "GET",
            "path": "/game/api/mock-room/round",
            "headers": [],
        }
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_current_round_hides_answer_until_displayed(monkeypatch) -> None:
    room = SimpleNamespace(room_id="FI2037", phase="playing", current_round=1, total_rounds=4)
    players = [
        SimpleNamespace(id="p1", nickname="提问者"),
        SimpleNamespace(id="p2", nickname="被测者"),
        SimpleNamespace(id="p3", nickname="陪审团"),
    ]
    current_round = SimpleNamespace(
        id="round-1",
        round_number=1,
        status="answering",
        question="什么是拉布拉多",
        answer="这是不该提前看到的回答",
        answer_type="ai",
        interrogator_id="p1",
        subject_id="p2",
        question_draft="",
        answer_draft="",
        answer_submitted_at=datetime.now(timezone.utc),
        answer_displayed_at=None,
    )

    monkeypatch.setattr(game_controller.game_room_service, "get_room_by_id", AsyncMock(return_value=room))
    monkeypatch.setattr(game_controller.game_room_service, "get_players_in_room", AsyncMock(return_value=players))
    monkeypatch.setattr(game_controller, "_get_authed_player", AsyncMock(return_value=SimpleNamespace(id="p3")))
    monkeypatch.setattr(
        game_controller.VoteRecord,
        "find_one",
        AsyncMock(return_value=SimpleNamespace(vote="human")),
    )
    monkeypatch.setattr(GameRound, "find_one", AsyncMock(return_value=current_round))

    result = await game_controller.get_current_round(_build_request(), "room-object-id")

    assert result["success"] is True
    assert result["round"]["answer"] == ""
    assert result["round"]["is_answer_visible"] is False
    assert result["round"]["is_answer_submitted"] is True
    assert result["round"]["my_vote"] == "human"
    assert result["round"]["my_question_draft"] == ""
    assert result["round"]["my_answer_draft"] == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_current_round_returns_question_draft_for_interrogator(monkeypatch) -> None:
    room = SimpleNamespace(room_id="FI2037", phase="playing", current_round=2, total_rounds=4)
    players = [
        SimpleNamespace(id="p1", nickname="提问者"),
        SimpleNamespace(id="p2", nickname="被测者"),
    ]
    current_round = SimpleNamespace(
        id="round-2",
        round_number=2,
        status="questioning",
        question="",
        answer="",
        answer_type="human",
        interrogator_id="p1",
        subject_id="p2",
        question_draft="我还在打字",
        answer_draft="",
        answer_submitted_at=None,
        answer_displayed_at=None,
    )

    monkeypatch.setattr(game_controller.game_room_service, "get_room_by_id", AsyncMock(return_value=room))
    monkeypatch.setattr(game_controller.game_room_service, "get_players_in_room", AsyncMock(return_value=players))
    monkeypatch.setattr(game_controller, "_get_authed_player", AsyncMock(return_value=SimpleNamespace(id="p1")))
    monkeypatch.setattr(game_controller.VoteRecord, "find_one", AsyncMock(return_value=None))
    monkeypatch.setattr(GameRound, "find_one", AsyncMock(return_value=current_round))

    result = await game_controller.get_current_round(_build_request(), "room-object-id")

    assert result["success"] is True
    assert result["round"]["my_question_draft"] == "我还在打字"
    assert result["round"]["my_answer_draft"] == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_current_round_returns_answer_and_draft_for_subject(monkeypatch) -> None:
    room = SimpleNamespace(room_id="FI2037", phase="playing", current_round=3, total_rounds=4)
    players = [
        SimpleNamespace(id="p1", nickname="提问者"),
        SimpleNamespace(id="p2", nickname="被测者"),
    ]
    current_round = SimpleNamespace(
        id="round-3",
        round_number=3,
        status="answering",
        question="继续提问",
        answer="这条回答已经可见",
        answer_type="human",
        interrogator_id="p1",
        subject_id="p2",
        question_draft="",
        answer_draft="被测者草稿",
        answer_submitted_at=datetime.now(timezone.utc),
        answer_displayed_at=datetime.now(timezone.utc),
    )

    monkeypatch.setattr(game_controller.game_room_service, "get_room_by_id", AsyncMock(return_value=room))
    monkeypatch.setattr(game_controller.game_room_service, "get_players_in_room", AsyncMock(return_value=players))
    monkeypatch.setattr(game_controller, "_get_authed_player", AsyncMock(return_value=SimpleNamespace(id="p2")))
    monkeypatch.setattr(game_controller.VoteRecord, "find_one", AsyncMock(return_value=None))
    monkeypatch.setattr(GameRound, "find_one", AsyncMock(return_value=current_round))

    result = await game_controller.get_current_round(_build_request(), "room-object-id")

    assert result["success"] is True
    assert result["round"]["answer"] == "这条回答已经可见"
    assert result["round"]["is_answer_visible"] is True
    assert result["round"]["my_answer_draft"] == ""
