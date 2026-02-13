"""投票记录模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from beanie import Document
from pymongo import IndexModel
from pydantic import Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VoteRecord(Document):
    """投票记录。"""

    room_id: str = Field(..., max_length=16)
    round_number: int = Field(..., ge=1)
    voter_id: str = Field(..., max_length=32)
    vote: Literal["human", "ai", "skip"] = "skip"
    is_correct: bool | None = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "vote_records"
        indexes = [
            IndexModel(
                [("room_id", 1), ("round_number", 1), ("voter_id", 1)],
                unique=True,
                name="uniq_vote",
            ),
        ]
