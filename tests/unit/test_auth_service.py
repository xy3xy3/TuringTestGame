from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import auth_service


@pytest.mark.unit
def test_hash_password_roundtrip() -> None:
    raw = 'admin-pass-123'
    hashed = auth_service.hash_password(raw)

    assert hashed != raw
    assert auth_service.verify_password(raw, hashed) is True
    assert auth_service.verify_password('wrong-pass', hashed) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_authenticate_returns_none_when_user_missing(monkeypatch) -> None:
    async def fake_get_admin_by_username(_username: str):
        return None

    monkeypatch.setattr(auth_service, 'get_admin_by_username', fake_get_admin_by_username)

    assert await auth_service.authenticate('ghost', 'pass') is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_authenticate_returns_none_for_disabled_admin(monkeypatch) -> None:
    admin = SimpleNamespace(status='disabled', password_hash='hashed')

    async def fake_get_admin_by_username(_username: str):
        return admin

    monkeypatch.setattr(auth_service, 'get_admin_by_username', fake_get_admin_by_username)

    assert await auth_service.authenticate('demo', 'pass') is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_authenticate_returns_none_for_wrong_password(monkeypatch) -> None:
    admin = SimpleNamespace(status='enabled', password_hash='hashed')

    async def fake_get_admin_by_username(_username: str):
        return admin

    monkeypatch.setattr(auth_service, 'get_admin_by_username', fake_get_admin_by_username)
    monkeypatch.setattr(auth_service, 'verify_password', lambda _raw, _hashed: False)

    assert await auth_service.authenticate('demo', 'bad-pass') is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_authenticate_updates_last_login_on_success(monkeypatch) -> None:
    class FakeAdmin:
        def __init__(self) -> None:
            self.status = 'enabled'
            self.password_hash = 'hashed'
            self.last_login = None
            self.updated_at = None
            self.saved = False

        async def save(self) -> None:
            self.saved = True

    admin = FakeAdmin()
    fixed_now = datetime(2026, 2, 8, tzinfo=timezone.utc)

    async def fake_get_admin_by_username(_username: str):
        return admin

    monkeypatch.setattr(auth_service, 'get_admin_by_username', fake_get_admin_by_username)
    monkeypatch.setattr(auth_service, 'verify_password', lambda _raw, _hashed: True)
    monkeypatch.setattr(auth_service, 'utc_now', lambda: fixed_now)

    result = await auth_service.authenticate('demo', 'good-pass')

    assert result is admin
    assert admin.last_login == fixed_now
    assert admin.updated_at == fixed_now
    assert admin.saved is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_change_password_rejects_wrong_old_password(monkeypatch) -> None:
    class FakeAdmin:
        def __init__(self) -> None:
            self.password_hash = 'old-hash'
            self.saved = False

        async def save(self) -> None:
            self.saved = True

    admin = FakeAdmin()
    monkeypatch.setattr(auth_service, 'verify_password', lambda _raw, _hashed: False)

    result = await auth_service.change_password(admin, 'wrong', 'new-pass')

    assert result is False
    assert admin.password_hash == 'old-hash'
    assert admin.saved is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_change_password_updates_hash_on_success(monkeypatch) -> None:
    class FakeAdmin:
        def __init__(self) -> None:
            self.password_hash = 'old-hash'
            self.updated_at = None
            self.saved = False

        async def save(self) -> None:
            self.saved = True

    admin = FakeAdmin()
    fixed_now = datetime(2026, 2, 8, tzinfo=timezone.utc)

    monkeypatch.setattr(auth_service, 'verify_password', lambda _raw, _hashed: True)
    monkeypatch.setattr(auth_service, 'hash_password', lambda _raw: 'new-hash')
    monkeypatch.setattr(auth_service, 'utc_now', lambda: fixed_now)

    result = await auth_service.change_password(admin, 'old-pass', 'new-pass')

    assert result is True
    assert admin.password_hash == 'new-hash'
    assert admin.updated_at == fixed_now
    assert admin.saved is True
