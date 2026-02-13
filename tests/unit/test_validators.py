from __future__ import annotations

import pytest

from app.services import validators


@pytest.mark.unit
def test_validate_role_slug() -> None:
    assert validators.validate_role_slug("ops_team") == ""
    assert validators.validate_role_slug("Ops Team") != ""


@pytest.mark.unit
def test_validate_admin_username() -> None:
    assert validators.validate_admin_username("Admin_001") == ""
    assert validators.validate_admin_username("1admin") != ""


@pytest.mark.unit
def test_validate_optional_email() -> None:
    assert validators.validate_optional_email("") == ""
    assert validators.validate_optional_email("ops@example.com") == ""
    assert validators.validate_optional_email("invalid-email") != ""
