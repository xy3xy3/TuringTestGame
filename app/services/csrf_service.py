"""CSRF 防护服务。"""

from __future__ import annotations

import secrets
from collections.abc import MutableMapping
from typing import Any
from urllib.parse import parse_qs

from starlette.requests import Request

CSRF_SESSION_KEY = "csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def ensure_csrf_token(session: MutableMapping[str, Any]) -> str:
    """确保会话中存在 CSRF Token，并返回该值。"""

    token = str(session.get(CSRF_SESSION_KEY) or "")
    if token:
        return token

    token = secrets.token_urlsafe(32)
    session[CSRF_SESSION_KEY] = token
    return token


def rotate_csrf_token(session: MutableMapping[str, Any]) -> str:
    """重置会话中的 CSRF Token，常用于登录后轮换。"""

    token = secrets.token_urlsafe(32)
    session[CSRF_SESSION_KEY] = token
    return token


def is_safe_method(method: str) -> bool:
    """判断请求方法是否属于无需 CSRF 校验的安全方法。"""

    return method.upper() in SAFE_METHODS


async def extract_submitted_token(request: Request) -> str:
    """提取请求中提交的 CSRF Token（优先 Header，其次 urlencoded 表单）。"""

    header_token = (request.headers.get(CSRF_HEADER_NAME) or "").strip()
    if header_token:
        return header_token

    content_type = (request.headers.get("content-type") or "").lower()
    if "application/x-www-form-urlencoded" not in content_type:
        return ""

    # body() 会缓存请求体，后续 FastAPI 依然能继续读取 Form 参数。
    body = await request.body()
    decoded = body.decode("utf-8", errors="ignore")
    parsed = parse_qs(decoded)
    values = parsed.get(CSRF_FORM_FIELD, [])
    return str(values[0]).strip() if values else ""


async def validate_request_token(request: Request, session_token: str) -> bool:
    """校验请求中的 CSRF Token 是否与会话一致。"""

    if not session_token:
        return False

    submitted_token = await extract_submitted_token(request)
    if not submitted_token:
        return False

    return secrets.compare_digest(session_token, submitted_token)
