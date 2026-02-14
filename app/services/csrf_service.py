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


def _extract_multipart_boundary(content_type: str) -> str:
    """从 Content-Type 中提取 multipart boundary。"""

    for segment in str(content_type or "").split(";")[1:]:
        key, _, value = segment.strip().partition("=")
        if key.strip().lower() != "boundary":
            continue
        return value.strip().strip('"')
    return ""


def _extract_multipart_token(body: bytes, content_type: str) -> str:
    """从 multipart 请求体中提取 CSRF 字段值。"""

    boundary = _extract_multipart_boundary(content_type)
    if not boundary or not body:
        return ""

    boundary_bytes = f"--{boundary}".encode("utf-8")
    for raw_part in body.split(boundary_bytes):
        part = raw_part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].rstrip(b"\r\n")

        headers, marker, value_blob = part.partition(b"\r\n\r\n")
        if not marker:
            continue

        header_text = headers.decode("latin-1", errors="ignore").lower()
        if "content-disposition:" not in header_text:
            continue
        if f'name="{CSRF_FORM_FIELD}"' not in header_text:
            continue
        # 仅接受普通字段，避免命中文件 part。
        if "filename=" in header_text:
            continue

        value = value_blob.rstrip(b"\r\n")
        return value.decode("utf-8", errors="ignore").strip()

    return ""


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
    """提取请求中提交的 CSRF Token（优先 Header，其次表单字段）。"""

    header_token = (request.headers.get(CSRF_HEADER_NAME) or "").strip()
    if header_token:
        return header_token

    content_type_raw = request.headers.get("content-type") or ""
    content_type = content_type_raw.lower()
    if "multipart/form-data" in content_type:
        # 在 BaseHTTPMiddleware 中提前调用 request.form() 会导致下游拿不到 UploadFile，
        # multipart 场景改为直接读取 body 解析 token，避免文件上传字段丢失。
        try:
            body = await request.body()
        except Exception:
            return ""
        return _extract_multipart_token(body, content_type_raw)

    if "application/x-www-form-urlencoded" in content_type:
        # body() 会缓存请求体，后续 FastAPI 依然能继续读取 Form 参数。
        body = await request.body()
        decoded = body.decode("utf-8", errors="ignore")
        parsed = parse_qs(decoded)
        values = parsed.get(CSRF_FORM_FIELD, [])
        return str(values[0]).strip() if values else ""

    return ""


async def validate_request_token(request: Request, session_token: str) -> bool:
    """校验请求中的 CSRF Token 是否与会话一致。"""

    if not session_token:
        return False

    submitted_token = await extract_submitted_token(request)
    if not submitted_token:
        return False

    return secrets.compare_digest(session_token, submitted_token)
