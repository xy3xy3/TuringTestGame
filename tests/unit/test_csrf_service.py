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
