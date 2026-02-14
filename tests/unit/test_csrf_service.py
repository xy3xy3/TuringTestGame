from __future__ import annotations

import pytest

from app.services import csrf_service


@pytest.mark.unit
def test_ensure_csrf_token_reuses_existing_token() -> None:
    session: dict[str, str] = {}

    first = csrf_service.ensure_csrf_token(session)
    second = csrf_service.ensure_csrf_token(session)

    assert first
    assert second == first
    assert session[csrf_service.CSRF_SESSION_KEY] == first


@pytest.mark.unit
def test_rotate_csrf_token_overwrites_old_value() -> None:
    session: dict[str, str] = {}

    old_token = csrf_service.ensure_csrf_token(session)
    new_token = csrf_service.rotate_csrf_token(session)

    assert new_token
    assert new_token != old_token
    assert session[csrf_service.CSRF_SESSION_KEY] == new_token


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "is_safe"),
    [("GET", True), ("HEAD", True), ("OPTIONS", True), ("POST", False), ("DELETE", False)],
)
def test_is_safe_method(method: str, is_safe: bool) -> None:
    assert csrf_service.is_safe_method(method) is is_safe


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_submitted_token_from_multipart() -> None:
    """multipart/form-data 时应能从表单字段读取 CSRF。"""

    from starlette.requests import Request

    boundary = "----WebKitFormBoundaryTest"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="csrf_token"\r\n\r\n'
        "token123\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/admin/config",
        "headers": [
            (b"content-type", f"multipart/form-data; boundary={boundary}".encode("utf-8")),
        ],
    }

    async def receive():
        nonlocal body
        chunk = body
        body = b""
        return {"type": "http.request", "body": chunk, "more_body": False}

    request = Request(scope, receive)
    token = await csrf_service.extract_submitted_token(request)

    assert token == "token123"
