"""IP 限流服务（Redis 优先，内存兜底）。"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from redis.exceptions import RedisError

from . import config_service
from .redis_service import get_redis_client

CONFIG_CACHE_TTL_SECONDS = 5.0

_config_cache: dict[str, Any] | None = None
_config_cache_at = 0.0
_config_cache_lock = asyncio.Lock()

_memory_bucket: dict[str, tuple[int, float]] = {}
_memory_lock = asyncio.Lock()


@dataclass
class RateLimitDecision:
    """限流判定结果。"""

    allowed: bool
    remaining: int
    retry_after: int


def invalidate_config_cache() -> None:
    """主动清空限流配置缓存（配置更新后调用）。"""
    global _config_cache, _config_cache_at
    _config_cache = None
    _config_cache_at = 0.0


async def get_rate_limit_config_cached() -> dict[str, Any]:
    """读取限流配置（带短时缓存，降低数据库压力）。"""
    global _config_cache, _config_cache_at

    now = time.time()
    if _config_cache and now - _config_cache_at < CONFIG_CACHE_TTL_SECONDS:
        return _config_cache

    async with _config_cache_lock:
        now = time.time()
        if _config_cache and now - _config_cache_at < CONFIG_CACHE_TTL_SECONDS:
            return _config_cache
        _config_cache = await config_service.get_rate_limit_config()
        _config_cache_at = now
        return _config_cache


def _extract_ip_from_forwarded_header(value: str) -> str:
    """从 RFC 7239 Forwarded 头中解析 for= 对应 IP。"""
    if not value:
        return ""

    segments = [item.strip() for item in value.split(",") if item.strip()]
    for segment in segments:
        for pair in segment.split(";"):
            key, _, raw = pair.partition("=")
            if key.strip().lower() != "for":
                continue
            candidate = raw.strip().strip('"')
            # 处理 [IPv6]:port 或 IPv4:port
            candidate = re.sub(r"^\[([^\]]+)\](?::\d+)?$", r"\1", candidate)
            if ":" in candidate and candidate.count(":") == 1:
                host, _, port = candidate.partition(":")
                if host and port.isdigit():
                    candidate = host
            if candidate:
                return candidate
    return ""


def extract_client_ip(request: Request, *, trust_proxy_headers: bool) -> str:
    """提取客户端 IP（支持反向代理头）。"""
    if trust_proxy_headers:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            first = forwarded_for.split(",", 1)[0].strip()
            if first:
                return first

        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip

        forwarded = _extract_ip_from_forwarded_header(request.headers.get("forwarded", ""))
        if forwarded:
            return forwarded

    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _resolve_scope_limit(config: dict[str, Any], scope: str) -> int:
    """根据限流场景返回最大请求数。"""
    if scope == "create_room":
        return int(config.get("create_room_max_requests", 20))
    if scope == "join_room":
        return int(config.get("join_room_max_requests", 40))
    if scope == "chat_api":
        return int(config.get("chat_api_max_requests", 30))
    return int(config.get("max_requests", 120))


async def _hit_with_redis(*, key: str, max_requests: int, window_seconds: int) -> RateLimitDecision | None:
    """使用 Redis 固定窗口计数；Redis 不可用时返回 None。"""
    redis_client = await get_redis_client()
    if redis_client is None:
        return None

    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, window_seconds)
        ttl = await redis_client.ttl(key)
    except RedisError:
        return None
    except Exception:
        return None

    remaining = max(0, max_requests - int(count))
    allowed = int(count) <= max_requests
    retry_after = max(int(ttl), 1) if not allowed else max(int(ttl), 0)
    return RateLimitDecision(allowed=allowed, remaining=remaining, retry_after=retry_after)


async def _hit_with_memory(*, key: str, max_requests: int, window_seconds: int) -> RateLimitDecision:
    """内存兜底限流（单进程）。"""
    now = time.time()
    bucket = int(now // max(window_seconds, 1))
    window_key = f"{key}:{bucket}"

    async with _memory_lock:
        # 定期清理过期桶，避免内存无限增长。
        if len(_memory_bucket) > 20_000:
            expired = [k for k, (_, exp) in _memory_bucket.items() if exp <= now]
            for item in expired:
                _memory_bucket.pop(item, None)

        count, expires_at = _memory_bucket.get(window_key, (0, now + window_seconds))
        if expires_at <= now:
            count = 0
            expires_at = now + window_seconds

        count += 1
        _memory_bucket[window_key] = (count, expires_at)

        remaining = max(0, max_requests - count)
        allowed = count <= max_requests
        retry_after = max(1, int(expires_at - now)) if not allowed else max(0, int(expires_at - now))
        return RateLimitDecision(allowed=allowed, remaining=remaining, retry_after=retry_after)


async def check_request_allowed(request: Request, *, scope: str) -> RateLimitDecision:
    """检查请求是否允许继续处理。"""
    config = await get_rate_limit_config_cached()
    if not bool(config.get("enabled", False)):
        return RateLimitDecision(allowed=True, remaining=0, retry_after=0)

    ip = extract_client_ip(request, trust_proxy_headers=bool(config.get("trust_proxy_headers", False)))
    window_seconds = max(1, int(config.get("window_seconds", 60)))
    max_requests = max(1, _resolve_scope_limit(config, scope))
    key = f"rate_limit:{scope}:{ip}"

    decision = await _hit_with_redis(key=key, max_requests=max_requests, window_seconds=window_seconds)
    if decision is not None:
        return decision

    return await _hit_with_memory(key=key, max_requests=max_requests, window_seconds=window_seconds)
