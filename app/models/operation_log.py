"""操作日志模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from beanie import Document
from pydantic import Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OperationLog(Document):
    """后台操作日志。"""

    action: Literal["create", "read", "update", "delete"]
    module: str = Field(..., min_length=2, max_length=32)
    target: str = Field(default="", max_length=80)
    target_id: str = Field(default="", max_length=64)
    detail: str = Field(default="", max_length=500)
    operator: str = Field(default="system", max_length=32)
    method: str = Field(default="", max_length=12)
    path: str = Field(default="", max_length=160)
    ip: str = Field(default="", max_length=64)
    created_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "operation_logs"
