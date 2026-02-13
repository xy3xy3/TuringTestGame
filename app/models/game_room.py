"""游戏房间模型。"""

from __future__ import annotations

import random
import string
from datetime import datetime, timezone
from typing import Literal

from beanie import Document
from pymongo import IndexModel
from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_room_id(length: int = 6) -> str:
    """生成随机房间号。"""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


class GameConfig(BaseModel):
    """游戏配置。"""

    setup_duration: int = Field(default=60, ge=30, le=300)
    question_duration: int = Field(default=30, ge=15, le=60)
    answer_duration: int = Field(default=45, ge=20, le=90)
    vote_duration: int = Field(default=15, ge=10, le=30)
    reveal_delay: int = Field(default=3, ge=1, le=10)
    min_players: int = Field(default=2, ge=2, le=16)
    max_players: int = Field(default=8, ge=2, le=16)
    rounds_per_game: int = Field(default=0, ge=0, le=20)


class GameRoom(Document):
    """游戏房间。"""

    room_id: str = Field(default_factory=generate_room_id, max_length=16)
    password: str = Field(default="", max_length=64)
    owner_id: str = Field(..., max_length=32)
    status: Literal["waiting", "setup", "playing", "finished"] = "waiting"
    config: GameConfig = Field(default_factory=GameConfig)
    player_ids: list[str] = Field(default_factory=list)
    current_round: int = Field(default=0, ge=0)
    total_rounds: int = Field(default=4, ge=1, le=20)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    class Settings:
        name = "game_rooms"
        indexes = [
            IndexModel([("room_id", 1)], unique=True, name="uniq_room_id"),
            IndexModel([("status", 1)], name="idx_room_status"),
        ]
