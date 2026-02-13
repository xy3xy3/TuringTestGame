from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.middleware.auth import should_enforce_csrf


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "path", "admin_id", "expected"),
    [
        ("GET", "/admin/login", None, False),
        ("POST", "/admin/login", None, True),
        ("POST", "/admin/users", "uid-1", True),
        ("DELETE", "/admin/users/1", "uid-1", True),
        ("POST", "/admin/users", None, False),
        ("POST", "/public/form", "uid-1", False),
    ],
)
def test_should_enforce_csrf(method: str, path: str, admin_id: str | None, expected: bool) -> None:
    request = SimpleNamespace(method=method, session={"admin_id": admin_id} if admin_id else {})
    assert should_enforce_csrf(request, path) is expected
