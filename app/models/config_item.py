"""系统配置项模型。"""

from __future__ import annotations

from datetime import datetime, timezone

from beanie import Document
from pydantic import Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConfigItem(Document):
    """系统配置项（按 key 存储）。"""

    key: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=2, max_length=64)
    value: str = Field(default="")
    group: str = Field(default="default", max_length=32)
    description: str = Field(default="", max_length=120)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "config_items"
