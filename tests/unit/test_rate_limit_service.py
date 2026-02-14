from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import rate_limit_service


def _fake_request(*, headers: dict[str, str], client_ip: str):
    return SimpleNamespace(
        headers=headers,
        client=SimpleNamespace(host=client_ip),
        url=SimpleNamespace(path="/game/create"),
        method="POST",
    )


@pytest.mark.unit
def test_extract_client_ip_prefers_forwarded_when_enabled() -> None:
    request = _fake_request(
        headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1", "x-real-ip": "198.51.100.1"},
        client_ip="127.0.0.1",
    )
    ip = rate_limit_service.extract_client_ip(request, trust_proxy_headers=True)
    assert ip == "203.0.113.9"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_request_allowed_blocks_after_limit(monkeypatch) -> None:
    async def fake_get_config():
        return {
            "enabled": True,
            "trust_proxy_headers": True,
            "window_seconds": 60,
            "max_requests": 10,
            "create_room_max_requests": 1,
            "join_room_max_requests": 10,
            "chat_api_max_requests": 10,
        }

    async def fake_hit_with_redis(**_kwargs):
        return None

    monkeypatch.setattr(rate_limit_service, "get_rate_limit_config_cached", fake_get_config)
    monkeypatch.setattr(rate_limit_service, "_hit_with_redis", fake_hit_with_redis)
    rate_limit_service._memory_bucket.clear()

    request = _fake_request(headers={"x-forwarded-for": "203.0.113.9"}, client_ip="127.0.0.1")

    first = await rate_limit_service.check_request_allowed(request, scope="create_room")
    second = await rate_limit_service.check_request_allowed(request, scope="create_room")

    assert first.allowed is True
    assert second.allowed is False
    assert second.retry_after >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_request_allowed_pass_when_disabled(monkeypatch) -> None:
    async def fake_get_config():
        return {
            "enabled": False,
            "trust_proxy_headers": False,
            "window_seconds": 60,
            "max_requests": 1,
            "create_room_max_requests": 1,
            "join_room_max_requests": 1,
            "chat_api_max_requests": 1,
        }

    monkeypatch.setattr(rate_limit_service, "get_rate_limit_config_cached", fake_get_config)

    request = _fake_request(headers={}, client_ip="127.0.0.1")
    decision = await rate_limit_service.check_request_allowed(request, scope="create_room")
    assert decision.allowed is True
