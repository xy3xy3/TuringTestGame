from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import config_service
from app.services.config_service import normalize_audit_actions


@pytest.mark.unit
def test_normalize_audit_actions_deduplicate_and_sort() -> None:
    values = ['delete', 'create', 'delete', 'read', 'unknown', 'update']
    assert normalize_audit_actions(values) == ['create', 'read', 'update', 'delete']


@pytest.mark.unit
def test_normalize_audit_actions_handles_empty_values() -> None:
    values = ['', '   ', 'invalid']
    assert normalize_audit_actions(values) == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_audit_log_actions_uses_default_when_missing(monkeypatch) -> None:
    async def fake_find_config_item(_group: str, _key: str):
        return None

    monkeypatch.setattr(config_service, 'find_config_item', fake_find_config_item)

    assert await config_service.get_audit_log_actions() == config_service.AUDIT_DEFAULT_ACTIONS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_audit_log_actions_returns_empty_when_config_blank(monkeypatch) -> None:
    async def fake_find_config_item(_group: str, _key: str):
        return SimpleNamespace(value='   ')

    monkeypatch.setattr(config_service, 'find_config_item', fake_find_config_item)

    assert await config_service.get_audit_log_actions() == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_audit_log_actions_normalizes_config_value(monkeypatch) -> None:
    async def fake_find_config_item(_group: str, _key: str):
        return SimpleNamespace(value=' delete,unknown,create,delete ')

    monkeypatch.setattr(config_service, 'find_config_item', fake_find_config_item)

    assert await config_service.get_audit_log_actions() == ['create', 'delete']
