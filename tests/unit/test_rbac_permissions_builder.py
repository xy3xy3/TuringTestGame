from __future__ import annotations

import pytest

from app.apps.admin.controllers.rbac import build_permissions, role_errors


class FakeFormData:
    """模拟多值表单数据。"""

    def __init__(self, payload: dict[str, list[str]]):
        self.payload = payload

    def getlist(self, key: str) -> list[str]:
        return self.payload.get(key, [])


@pytest.mark.unit
def test_build_permissions_auto_appends_read_when_mutating_checked() -> None:
    form_data = FakeFormData(
        {
            "perm_admin_users": ["update"],
        }
    )

    permissions = build_permissions(form_data, owner="tester")
    actions = {(item["resource"], item["action"]) for item in permissions}

    assert ("admin_users", "update") in actions
    assert ("admin_users", "read") in actions


@pytest.mark.unit
def test_build_permissions_does_not_strip_valid_actions_by_role_slug() -> None:
    form_data = FakeFormData(
        {
            "perm_admin_users": ["read", "update"],
        }
    )

    permissions = build_permissions(form_data, owner="tester")
    actions = {(item["resource"], item["action"]) for item in permissions}

    assert ("admin_users", "read") in actions
    assert ("admin_users", "update") in actions


@pytest.mark.unit
def test_role_errors_rejects_invalid_slug_pattern() -> None:
    errors = role_errors({"name": "运维", "slug": "Ops Team", "status": "enabled"})
    assert "角色标识仅支持小写字母、数字、下划线，且必须以字母开头" in errors
