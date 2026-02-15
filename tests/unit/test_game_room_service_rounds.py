from __future__ import annotations

import pytest

from app.services import game_room_service


@pytest.mark.unit
def test_resolve_total_rounds_by_player_count_uses_dynamic_rule(monkeypatch) -> None:
    """默认按玩家数 * 2 计算总回合。"""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("TEST_GAME_TOTAL_ROUNDS", raising=False)

    assert game_room_service.resolve_total_rounds_by_player_count(1) == 2
    assert game_room_service.resolve_total_rounds_by_player_count(4) == 8
    assert game_room_service.resolve_total_rounds_by_player_count(8) == 16
    assert game_room_service.resolve_total_rounds_by_player_count(8, max_rounds=12) == 12


@pytest.mark.unit
def test_resolve_total_rounds_by_player_count_supports_test_override(monkeypatch) -> None:
    """测试环境启用覆盖时，应优先使用 TEST_GAME_TOTAL_ROUNDS。"""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TEST_GAME_TOTAL_ROUNDS", "1")
    assert game_room_service.resolve_total_rounds_by_player_count(8) == 1

    monkeypatch.setenv("TEST_GAME_TOTAL_ROUNDS", "999")
    assert game_room_service.resolve_total_rounds_by_player_count(8) == 20
    assert game_room_service.resolve_total_rounds_by_player_count(8, max_rounds=9) == 9
