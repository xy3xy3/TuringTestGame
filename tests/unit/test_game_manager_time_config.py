from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import game_room_service
from app.services.game_manager import GameManager, sse_manager


@pytest.mark.unit
@pytest.mark.asyncio
async def test_start_game_syncs_latest_time_config(monkeypatch) -> None:
    """开始游戏时应先同步系统设置中的最新阶段时长。"""
    manager = GameManager()

    class DummyRoom:
        def __init__(self) -> None:
            self.id = "room-object-id"
            self.room_id = "ROOM01"
            self.phase = "waiting"
            self.current_round = 0
            self.total_rounds = 4
            self.started_at = None
            self.save_called = 0
            self.config = SimpleNamespace(
                min_players=2,
                setup_duration=30,
                question_duration=20,
                answer_duration=25,
                vote_duration=10,
                reveal_delay=2,
                max_rounds=20,
                rounds_per_game=4,
            )

        async def save(self) -> None:
            self.save_called += 1

    room = DummyRoom()

    async def fake_get_room_by_id(_room_id: str):
        return room

    async def fake_get_players_in_room(_room_code: str):
        return [SimpleNamespace(id="p1"), SimpleNamespace(id="p2")]

    async def fake_get_game_time_config():
        return {
            "setup_duration": 120,
            "question_duration": 60,
            "answer_duration": 70,
            "vote_duration": 25,
            "reveal_delay": 6,
        }

    published: list[tuple[str, dict]] = []

    async def fake_publish(_room_id: str, event: str, data: dict):
        published.append((event, data))

    def fake_start_timer(_room_id: str, coro) -> None:
        # 单测中不启动真实异步定时器，避免未等待协程告警。
        coro.close()

    monkeypatch.setattr(game_room_service, "get_room_by_id", fake_get_room_by_id)
    monkeypatch.setattr(game_room_service, "get_players_in_room", fake_get_players_in_room)
    monkeypatch.setattr(
        game_room_service,
        "resolve_total_rounds_by_player_count",
        lambda count, fallback=4, max_rounds=20: min(max_rounds, count * 2),
    )
    monkeypatch.setattr("app.services.config_service.get_game_time_config", fake_get_game_time_config)
    monkeypatch.setattr(sse_manager, "publish", fake_publish)
    monkeypatch.setattr(manager, "_start_timer", fake_start_timer)

    result = await manager.start_game("room-object-id")

    assert result["success"] is True
    assert room.phase == "setup"
    assert room.total_rounds == 4
    assert room.config.rounds_per_game == 4
    assert room.config.setup_duration == 120
    assert room.config.question_duration == 60
    assert room.config.answer_duration == 70
    assert room.config.vote_duration == 25
    assert room.config.reveal_delay == 6
    assert room.save_called == 1
    assert published and published[0][0] == "game_starting"
    assert published[0][1]["countdown"] == 120
