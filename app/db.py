"""数据库初始化与连接管理。"""

from __future__ import annotations

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from .config import MONGO_DB, MONGO_URL
from .models import (
    AIModel,
    AdminUser,
    BackupRecord,
    ConfigItem,
    GamePlayer,
    GameRoom,
    GameRound,
    OperationLog,
    PromptTemplatesItem,
    Role,
    VoteRecord,
)

_mongo_client: AsyncIOMotorClient | None = None


async def init_db() -> None:
    """初始化 Beanie，并保留客户端用于关闭。"""
    global _mongo_client
    _mongo_client = AsyncIOMotorClient(MONGO_URL)
    await init_beanie(
        database=_mongo_client[MONGO_DB],
        document_models=[
            Role,
            AdminUser,
            ConfigItem,
            OperationLog,
            BackupRecord,
            AIModel,
            GameRoom,
            GamePlayer,
            GameRound,
            VoteRecord,
            PromptTemplatesItem,
        ],
    )


async def close_db() -> None:
    """关闭 Mongo 连接。"""
    if _mongo_client is not None:
        _mongo_client.close()
