from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.game_manager import GameManager


@pytest.mark.unit
def test_calculate_scores_only_interrogator_scored_when_correct() -> None:
    """仅提问者参与计分，猜对时 +50。"""
    manager = GameManager()
    game_round = SimpleNamespace(
        interrogator_id="p1",
        subject_id="p2",
        answer_type="ai",
    )
    votes = [
        SimpleNamespace(voter_id="p1", vote="ai"),
        SimpleNamespace(voter_id="p3", vote="human"),
    ]

    scores = manager._calculate_scores(game_round, votes)

    assert scores == {"p1": 50}


@pytest.mark.unit
def test_calculate_scores_only_interrogator_scored_when_wrong_or_skip() -> None:
    """提问者猜错时 -30；若跳过则本轮无人得分。"""
    manager = GameManager()
    game_round = SimpleNamespace(
        interrogator_id="p1",
        subject_id="p2",
        answer_type="human",
    )

    wrong_votes = [
        SimpleNamespace(voter_id="p1", vote="ai"),
        SimpleNamespace(voter_id="p3", vote="human"),
    ]
    skip_votes = [
        SimpleNamespace(voter_id="p1", vote="skip"),
        SimpleNamespace(voter_id="p3", vote="human"),
    ]

    assert manager._calculate_scores(game_round, wrong_votes) == {"p1": -30}
    assert manager._calculate_scores(game_round, skip_votes) == {}
