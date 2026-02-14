"""Redis 连接管理服务。"""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis

from app.config import REDIS_URL

_redis_client: Redis | None = None
_redis_init_failed = False
_redis_lock = asyncio.Lock()


async def get_redis_client() -> Redis | None:
    """获取 Redis 客户端；未配置或初始化失败时返回 None。"""
    global _redis_client, _redis_init_failed

    if not REDIS_URL:
        return None
    if _redis_client is not None:
        return _redis_client
    if _redis_init_failed:
        return None

    async with _redis_lock:
        if _redis_client is not None:
            return _redis_client
        if _redis_init_failed:
            return None

        client = Redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
        try:
            await client.ping()
            _redis_client = client
            return _redis_client
        except Exception:
            _redis_init_failed = True
            try:
                await client.aclose()
            except Exception:
                pass
            return None


async def close_redis_client() -> None:
    """关闭 Redis 客户端连接。"""
    global _redis_client, _redis_init_failed

    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception:
            pass
    _redis_client = None
    _redis_init_failed = False
