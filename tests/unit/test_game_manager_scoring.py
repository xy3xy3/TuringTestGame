from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.game_manager import GameManager


@pytest.mark.unit
def test_calculate_scores_all_voters_scored_when_correct_or_wrong() -> None:
    """提问者与陪审团都参与计分：猜对 +50，猜错 -30。"""
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

    assert scores == {"p1": 50, "p3": -30}


@pytest.mark.unit
def test_calculate_scores_skip_or_subject_vote_not_scored() -> None:
    """跳过不计分，被测者即使出现脏数据投票也不计分。"""
    manager = GameManager()
    game_round = SimpleNamespace(
        interrogator_id="p1",
        subject_id="p2",
        answer_type="human",
    )

    wrong_votes = [
        SimpleNamespace(voter_id="p1", vote="ai"),
        SimpleNamespace(voter_id="p3", vote="human"),
        SimpleNamespace(voter_id="p2", vote="human"),
    ]
    skip_votes = [
        SimpleNamespace(voter_id="p1", vote="skip"),
        SimpleNamespace(voter_id="p3", vote="skip"),
        SimpleNamespace(voter_id="p2", vote="human"),
    ]

    assert manager._calculate_scores(game_round, wrong_votes) == {"p1": -30, "p3": 50}
    assert manager._calculate_scores(game_round, skip_votes) == {}


@pytest.mark.unit
def test_calculate_scores_interrogator_bonus_when_all_jurors_correct() -> None:
    """开启附加机制后，所有陪审团猜对时提问者额外 +50。"""
    manager = GameManager()
    game_round = SimpleNamespace(
        interrogator_id="p1",
        subject_id="p2",
        answer_type="ai",
    )
    votes = [
        SimpleNamespace(voter_id="p1", vote="ai"),
        SimpleNamespace(voter_id="p3", vote="ai"),
        SimpleNamespace(voter_id="p4", vote="ai"),
    ]

    assert manager._calculate_scores(game_round, votes, enable_bonus_scoring=True) == {
        "p1": 100,
        "p3": 50,
        "p4": 50,
    }


@pytest.mark.unit
def test_calculate_scores_subject_bonus_when_all_jurors_fooled() -> None:
    """开启附加机制后，被测者骗过所有陪审团会获得额外分。"""
    manager = GameManager()
    game_round = SimpleNamespace(
        interrogator_id="p1",
        subject_id="p2",
        answer_type="human",
    )
    votes = [
        SimpleNamespace(voter_id="p1", vote="human"),
        SimpleNamespace(voter_id="p3", vote="ai"),
        SimpleNamespace(voter_id="p4", vote="ai"),
    ]

    assert manager._calculate_scores(game_round, votes, enable_bonus_scoring=True) == {
        "p1": 50,
        "p3": -30,
        "p4": -30,
        "p2": 25,
    }


@pytest.mark.unit
def test_calculate_scores_subject_ai_bonus_is_50() -> None:
    """开启附加机制后，被测者使用 AI 且骗过所有陪审团额外 +50。"""
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

    assert manager._calculate_scores(game_round, votes, enable_bonus_scoring=True) == {
        "p1": 50,
        "p3": -30,
        "p2": 50,
    }
