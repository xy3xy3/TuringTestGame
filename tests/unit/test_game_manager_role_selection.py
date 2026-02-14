from __future__ import annotations

import random

import pytest

from app.services.game_manager import GameManager


class DummyPlayer:
    """用于单测的轻量玩家对象。"""

    def __init__(self, player_id: str, interrogator_count: int = 0, subject_count: int = 0) -> None:
        self.id = player_id
        self.times_as_interrogator = interrogator_count
        self.times_as_subject = subject_count
        self.save_count = 0

    async def save(self) -> None:
        self.save_count += 1


@pytest.mark.unit
def test_choose_player_with_pity_hard_pity_single_min(monkeypatch) -> None:
    """当差值达到阈值时，必须只在最低次数玩家中选。"""
    manager = GameManager()
    players = [
        DummyPlayer("A", interrogator_count=0),
        DummyPlayer("B", interrogator_count=2),
        DummyPlayer("C", interrogator_count=3),
    ]

    def should_not_call_choices(*_args, **_kwargs):
        raise AssertionError("硬保底场景不应走加权 random.choices")

    monkeypatch.setattr(random, "choices", should_not_call_choices)

    selected = manager._choose_player_with_pity(
        players,
        role="interrogator",
        settings=manager.ROLE_BALANCE_DEFAULTS,
    )
    assert selected.id == "A"


@pytest.mark.unit
def test_choose_player_with_pity_hard_pity_pool(monkeypatch) -> None:
    """硬保底且有多个最低次数玩家时，应在保底池中随机。"""
    manager = GameManager()
    players = [
        DummyPlayer("A", interrogator_count=0),
        DummyPlayer("B", interrogator_count=0),
        DummyPlayer("C", interrogator_count=2),
    ]
    seen_pool: list[str] = []

    def fake_choice(pool):
        seen_pool.extend(str(player.id) for player in pool)
        return pool[1]

    def should_not_call_choices(*_args, **_kwargs):
        raise AssertionError("硬保底场景不应走加权 random.choices")

    monkeypatch.setattr(random, "choice", fake_choice)
    monkeypatch.setattr(random, "choices", should_not_call_choices)

    selected = manager._choose_player_with_pity(
        players,
        role="interrogator",
        settings=manager.ROLE_BALANCE_DEFAULTS,
    )
    assert selected.id == "B"
    assert seen_pool == ["A", "B"]


@pytest.mark.unit
def test_choose_player_with_pity_weighted_and_exclude(monkeypatch) -> None:
    """加权随机时要正确排除已选提问者，避免同人兼任两个角色。"""
    manager = GameManager()
    players = [
        DummyPlayer("A", subject_count=1),
        DummyPlayer("B", subject_count=1),
        DummyPlayer("C", subject_count=1),
    ]

    def fake_choices(population, weights, k):
        assert [str(player.id) for player in population] == ["B", "C"]
        assert len(weights) == 2
        assert k == 1
        return [population[0]]

    monkeypatch.setattr(random, "choices", fake_choices)

    selected = manager._choose_player_with_pity(
        players,
        role="subject",
        settings=manager.ROLE_BALANCE_DEFAULTS,
        exclude_player_id="A",
    )
    assert selected.id == "B"


@pytest.mark.unit
def test_resolve_role_balance_settings_clamp_values() -> None:
    """解析角色配置时应回退默认并裁剪范围。"""
    manager = GameManager()
    room_config = type(
        "RoomConfig",
        (),
        {
            "role_pity_gap_threshold": "99",
            "role_weight_base": "abc",
            "role_weight_deficit_step": -8,
            "role_weight_zero_bonus": 500,
        },
    )()

    settings = manager._resolve_role_balance_settings(room_config)

    assert settings["pity_gap_threshold"] == 10
    assert settings["weight_base"] == manager.ROLE_BALANCE_DEFAULTS["weight_base"]
    assert settings["weight_deficit_step"] == 0
    assert settings["weight_zero_bonus"] == 500


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mark_role_usage_increase_counts() -> None:
    """记录角色次数时应正确递增并保存。"""
    manager = GameManager()
    interrogator = DummyPlayer("A", interrogator_count=2)
    subject = DummyPlayer("B", subject_count=3)

    await manager._mark_role_usage(interrogator, subject)

    assert interrogator.times_as_interrogator == 3
    assert subject.times_as_subject == 4
    assert interrogator.save_count == 1
    assert subject.save_count == 1
