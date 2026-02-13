"""AI 模型配置。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from beanie import Document
from pymongo import IndexModel
from pydantic import Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AIModel(Document):
    """AI 模型配置。"""

    name: str = Field(..., min_length=2, max_length=64)
    base_url: str = Field(..., min_length=8, max_length=256)
    api_key: str = Field(..., min_length=8, max_length=256)
    model_name: str = Field(..., min_length=1, max_length=64)
    temperature: float = Field(default=0.8, ge=0.0, le=2.0)
    max_tokens: int = Field(default=500, ge=100, le=2000)
    is_enabled: bool = Field(default=True)
    is_default: bool = Field(default=False)
    description: str = Field(default="", max_length=256)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "ai_models"
        indexes = [
            IndexModel([("name", 1)], unique=True, name="uniq_ai_model_name"),
            IndexModel([("is_enabled", 1)], name="idx_ai_model_enabled"),
        ]
