"""游戏回合模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from beanie import Document
from pymongo import IndexModel
from pydantic import Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GameRound(Document):
    """游戏回合。"""

    room_id: str = Field(..., max_length=16)
    round_number: int = Field(..., ge=1)
    interrogator_id: str = Field(..., max_length=32)
    subject_id: str = Field(..., max_length=32)
    question: str = Field(default="", max_length=1000)
    question_draft: str = Field(default="", max_length=1000)
    answer: str = Field(default="", max_length=2000)
    answer_draft: str = Field(default="", max_length=2000)
    answer_type: Literal["human", "ai"] = "human"
    used_ai_model_id: str | None = Field(default=None, max_length=32)
    question_at: datetime | None = None
    answer_submitted_at: datetime | None = None
    answer_displayed_at: datetime | None = None
    status: Literal["questioning", "answering", "voting", "revealed"] = "questioning"
    created_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "game_rounds"
        indexes = [
            IndexModel([("room_id", 1), ("round_number", 1)], unique=True, name="uniq_room_round"),
        ]
