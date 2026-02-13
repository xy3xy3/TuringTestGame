"""备份记录模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from beanie import Document
from pydantic import Field
from pymongo import DESCENDING, IndexModel


def utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class BackupRecord(Document):
    """数据库备份记录。"""

    filename: str = Field(..., min_length=1, max_length=256)
    size: int = Field(default=0)
    status: str = Field(default="running")  # running / success / failed
    collections: list[str] = Field(default_factory=list)
    cloud_uploads: list[dict[str, Any]] = Field(default_factory=list)
    error: str = Field(default="")
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "backup_records"
        indexes = [
            IndexModel([("created_at", DESCENDING)], name="idx_backup_created_at"),
            IndexModel([("status", DESCENDING), ("created_at", DESCENDING)], name="idx_backup_status_created"),
        ]
