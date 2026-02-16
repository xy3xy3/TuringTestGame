from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import game_room_service


@pytest.mark.unit
@pytest.mark.asyncio
async def test_kick_player_rejects_non_owner(monkeypatch) -> None:
    """非房主踢人时应被拒绝。"""
    room = SimpleNamespace(room_id="FI2037")
    requester = SimpleNamespace(is_owner=False)

    monkeypatch.setattr(game_room_service, "get_room_by_id", AsyncMock(return_value=room))
    monkeypatch.setattr(game_room_service.GamePlayer, "find_one", AsyncMock(return_value=requester))

    result = await game_room_service.kick_player(
        room_id="65f0c0ffee1234567890abcd",
        player_id="65f0c0ffee1234567890abce",
        requester_id="65f0c0ffee1234567890abcf",
    )

    assert result == {"success": False, "error": "只有房主可以踢人"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_kick_player_allows_owner_and_updates_rounds_in_waiting(monkeypatch) -> None:
    """房主踢人成功后，等待阶段应同步更新房间回合上限。"""
    requester = SimpleNamespace(is_owner=True)
    kicked_player = SimpleNamespace(is_owner=False, delete=AsyncMock())
    room = SimpleNamespace(
        room_id="FI2037",
        phase="waiting",
        total_rounds=8,
        config=SimpleNamespace(max_rounds=20, rounds_per_game=8),
        save=AsyncMock(),
    )

    find_one_mock = AsyncMock(side_effect=[requester, kicked_player])
    monkeypatch.setattr(game_room_service, "get_room_by_id", AsyncMock(return_value=room))
    monkeypatch.setattr(game_room_service.GamePlayer, "find_one", find_one_mock)

    class _CountCursor:
        """模拟 Beanie 查询游标，仅实现测试需要的 count 接口。"""

        async def count(self) -> int:
            return 3

    monkeypatch.setattr(game_room_service.GamePlayer, "find", lambda *_args, **_kwargs: _CountCursor())
    monkeypatch.setattr(game_room_service, "resolve_total_rounds_by_player_count", lambda *_args, **_kwargs: 6)

    result = await game_room_service.kick_player(
        room_id="65f0c0ffee1234567890abcd",
        player_id="65f0c0ffee1234567890abce",
        requester_id="65f0c0ffee1234567890abcf",
    )

    assert result == {"success": True}
    kicked_player.delete.assert_awaited_once()
    room.save.assert_awaited_once()
    assert room.total_rounds == 6
    assert room.config.rounds_per_game == 6
