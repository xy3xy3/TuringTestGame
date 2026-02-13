"""游戏玩家模型。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from beanie import Document
from pymongo import IndexModel
from pydantic import Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_player_id() -> str:
    """生成玩家唯一ID。"""
    return uuid.uuid4().hex[:12]


class GamePlayer(Document):
    """游戏玩家。"""

    player_id: str = Field(default_factory=generate_player_id, max_length=32)
    room_id: str = Field(..., max_length=16)
    nickname: str = Field(..., min_length=2, max_length=32)
    system_prompt: str = Field(default="", max_length=2000)
    ai_model_id: str | None = Field(default=None, max_length=32)
    is_ready: bool = Field(default=False)
    is_online: bool = Field(default=True)
    is_owner: bool = Field(default=False)
    setup_completed: bool = Field(default=False)
    total_score: int = Field(default=0)
    deception_count: int = Field(default=0)
    correct_vote_count: int = Field(default=0)
    consecutive_correct: int = Field(default=0)
    times_as_interrogator: int = Field(default=0)
    times_as_subject: int = Field(default=0)
    ai_used_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "game_players"
        indexes = [
            IndexModel([("player_id", 1)], unique=True, name="uniq_player_id"),
            IndexModel([("room_id", 1)], name="idx_player_room"),
        ]
