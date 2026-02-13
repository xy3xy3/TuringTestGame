"""管理员模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from beanie import Document
from pymongo import IndexModel
from pydantic import Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdminUser(Document):
    """管理员账号。"""

    username: str = Field(..., min_length=3, max_length=32)
    display_name: str = Field(..., min_length=2, max_length=32)
    email: str = Field(default="", max_length=64)
    role_slug: str = Field(default="super", max_length=32)
    status: Literal["enabled", "disabled"] = "enabled"
    password_hash: str = Field(..., min_length=10)
    last_login: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "admin_users"
        indexes = [IndexModel([("username", 1)], unique=True, name="uniq_admin_username")]
