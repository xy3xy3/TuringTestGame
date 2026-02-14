"""灵魂注入提示词模板模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from beanie import Document
from pymongo import IndexModel
from pydantic import Field


def utc_now() -> datetime:
    """返回 UTC 当前时间。"""

    return datetime.now(timezone.utc)


class PromptTemplatesItem(Document):
    """提示词模板。"""

    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(default="", max_length=200)
    prompt_text: str = Field(..., min_length=1, max_length=4000)
    status: Literal["enabled", "disabled"] = "enabled"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "prompt_templates_items"
        indexes = [
            IndexModel([("name", 1)], name="uniq_prompt_templates_name", unique=True),
            IndexModel([("status", 1), ("updated_at", -1)], name="idx_prompt_templates_status_updated"),
            IndexModel([("updated_at", -1)], name="idx_prompt_templates_updated_at"),
        ]
