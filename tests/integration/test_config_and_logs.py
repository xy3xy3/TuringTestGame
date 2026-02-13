from __future__ import annotations

import pytest

from app.services import config_service, log_service


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_actions_roundtrip(initialized_db) -> None:
    assert await config_service.get_audit_log_actions() == ["create", "update", "delete"]

    saved = await config_service.save_audit_log_actions(["delete", "create", "delete", "x"])
    assert saved == ["create", "delete"]
    assert await config_service.get_audit_log_actions() == ["create", "delete"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_record_action_respects_enabled_types(initialized_db) -> None:
    await config_service.save_audit_log_actions(["create"])

    created = await log_service.record_action(
        action="create",
        module="admin_users",
        operator="tester",
        target="管理员: e2e",
        target_id="1",
        detail="创建账号",
        method="POST",
        path="/admin/users",
        ip="127.0.0.1",
    )
    updated = await log_service.record_action(
        action="update",
        module="admin_users",
        operator="tester",
        target="管理员: e2e",
        target_id="1",
        detail="更新账号",
        method="POST",
        path="/admin/users/1",
        ip="127.0.0.1",
    )

    assert created is True
    assert updated is False

    logs, total = await log_service.list_logs(
        {
            "search_q": "",
            "search_action": "",
            "search_module": "",
            "search_sort": "created_desc",
        },
        page=1,
        page_size=10,
    )
    assert total == 1
    assert len(logs) == 1
    assert logs[0].action == "create"
    assert logs[0].module == "admin_users"
