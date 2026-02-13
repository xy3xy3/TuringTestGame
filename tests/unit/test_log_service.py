from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import log_service
from app.services.log_service import get_request_ip, normalize_log_action


@pytest.mark.unit
def test_normalize_log_action() -> None:
    assert normalize_log_action('CREATE') == 'create'
    assert normalize_log_action(' update ') == 'update'
    assert normalize_log_action('noop') == ''


@pytest.mark.unit
def test_get_request_ip_prefers_x_forwarded_for() -> None:
    request = SimpleNamespace(
        headers={'x-forwarded-for': '10.10.1.1, 192.168.0.1'},
        client=SimpleNamespace(host='127.0.0.1'),
    )
    assert get_request_ip(request) == '10.10.1.1'


@pytest.mark.unit
def test_get_request_ip_falls_back_to_client_host() -> None:
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host='127.0.0.1'))
    assert get_request_ip(request) == '127.0.0.1'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_record_action_returns_false_for_invalid_action() -> None:
    result = await log_service.record_action(action='unknown', module='logs', operator='tester')
    assert result is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_record_action_respects_enabled_types(monkeypatch) -> None:
    async def fake_get_audit_log_actions() -> list[str]:
        return ['create']

    monkeypatch.setattr(log_service.config_service, 'get_audit_log_actions', fake_get_audit_log_actions)

    result = await log_service.record_action(action='update', module='logs', operator='tester')
    assert result is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_record_action_writes_normalized_payload(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class FakeOperationLog:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def insert(self) -> None:
            captured['inserted'] = 'yes'

    async def fake_get_audit_log_actions() -> list[str]:
        return ['create', 'update', 'delete']

    monkeypatch.setattr(log_service, 'OperationLog', FakeOperationLog)
    monkeypatch.setattr(log_service.config_service, 'get_audit_log_actions', fake_get_audit_log_actions)

    result = await log_service.record_action(
        action=' CREATE ',
        module='admin_users',
        operator='  ',
        target=' user ',
        target_id=' 42 ',
        detail=' created ',
        method='post',
        path=' /admin/users ',
        ip=' 127.0.0.1 ',
    )

    assert result is True
    assert captured['inserted'] == 'yes'
    assert captured['action'] == 'create'
    assert captured['operator'] == 'system'
    assert captured['method'] == 'POST'
    assert captured['target'] == 'user'
    assert captured['target_id'] == '42'
    assert captured['detail'] == 'created'
    assert captured['path'] == '/admin/users'
    assert captured['ip'] == '127.0.0.1'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_record_request_delegates_to_record_action(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_record_action(**kwargs):
        captured.update(kwargs)
        return True

    request = SimpleNamespace(
        session={'admin_name': 'alice'},
        method='PATCH',
        url=SimpleNamespace(path='/admin/config'),
        headers={'x-forwarded-for': '1.2.3.4'},
        client=SimpleNamespace(host='127.0.0.1'),
    )

    monkeypatch.setattr(log_service, 'record_action', fake_record_action)

    result = await log_service.record_request(
        request,
        action='update',
        module='config',
        target='smtp',
        target_id='smtp_host',
        detail='changed host',
    )

    assert result is True
    assert captured['operator'] == 'alice'
    assert captured['method'] == 'PATCH'
    assert captured['path'] == '/admin/config'
    assert captured['ip'] == '1.2.3.4'
    assert captured['action'] == 'update'
