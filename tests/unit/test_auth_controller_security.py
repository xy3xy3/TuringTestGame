from __future__ import annotations

import pytest

from app.apps.admin.controllers.auth import sanitize_next_path


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_next", "expected"),
    [
        ("/admin/dashboard", "/admin/dashboard"),
        ("/admin/users?page=2", "/admin/users?page=2"),
        ("", "/admin/dashboard"),
        (None, "/admin/dashboard"),
        ("https://evil.example/pwn", "/admin/dashboard"),
        ("//evil.example/pwn", "/admin/dashboard"),
        ("javascript:alert(1)", "/admin/dashboard"),
        ("/profile", "/admin/dashboard"),
    ],
)
def test_sanitize_next_path_blocks_open_redirect(raw_next: str | None, expected: str) -> None:
    assert sanitize_next_path(raw_next) == expected
