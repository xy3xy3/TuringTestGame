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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_footer_copyright_returns_default_when_missing(monkeypatch) -> None:
    """未配置页脚版权时，应该回退到默认文案和仓库链接。"""

    async def fake_find_config_item(_group: str, _key: str):
        return None

    monkeypatch.setattr(config_service, "find_config_item", fake_find_config_item)

    footer = await config_service.get_footer_copyright()

    assert footer["text"] == config_service.FOOTER_COPYRIGHT_TEXT_DEFAULT
    assert footer["url"] == config_service.FOOTER_COPYRIGHT_URL_DEFAULT


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_footer_copyright_updates_existing_items(monkeypatch) -> None:
    """保存页脚版权时，应该规范化输入并更新已有配置项。"""

    class FakeConfigItem:
        def __init__(self, value: str) -> None:
            self.value = value
            self.name = ""
            self.description = ""
            self.updated_at = None
            self.saved = False

        async def save(self) -> None:
            self.saved = True

    text_item = FakeConfigItem("旧文案")
    url_item = FakeConfigItem("https://old.example.com")

    async def fake_find_config_item(_group: str, key: str):
        if key == config_service.FOOTER_COPYRIGHT_TEXT_KEY:
            return text_item
        if key == config_service.FOOTER_COPYRIGHT_URL_KEY:
            return url_item
        return None

    monkeypatch.setattr(config_service, "find_config_item", fake_find_config_item)

    footer = await config_service.save_footer_copyright("  新版权文案  ", "   ")

    assert footer["text"] == "新版权文案"
    assert footer["url"] == config_service.FOOTER_COPYRIGHT_URL_DEFAULT
    assert text_item.value == "新版权文案"
    assert url_item.value == config_service.FOOTER_COPYRIGHT_URL_DEFAULT
    assert text_item.saved is True
    assert url_item.saved is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_rate_limit_config_returns_default_when_missing(monkeypatch) -> None:
    """未配置限流参数时应返回默认配置。"""

    async def fake_find_config_item(_group: str, _key: str):
        return None

    monkeypatch.setattr(config_service, "find_config_item", fake_find_config_item)

    config = await config_service.get_rate_limit_config()

    assert config["enabled"] is False
    assert config["trust_proxy_headers"] is False
    assert config["window_seconds"] == 60
    assert config["create_room_max_requests"] == 20


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_rate_limit_config_normalizes_dirty_values(monkeypatch) -> None:
    """读取限流配置时应清洗脏值。"""

    raw = {
        "enabled": "on",
        "trust_proxy_headers": "1",
        "window_seconds": "-8",
        "max_requests": "abc",
        "create_room_max_requests": "5",
        "join_room_max_requests": "0",
        "chat_api_max_requests": "200001",
    }

    async def fake_find_config_item(_group: str, key: str):
        if key in raw:
            return SimpleNamespace(value=raw[key])
        return None

    monkeypatch.setattr(config_service, "find_config_item", fake_find_config_item)

    config = await config_service.get_rate_limit_config()

    assert config["enabled"] is True
    assert config["trust_proxy_headers"] is True
    assert config["window_seconds"] == 1
    assert config["max_requests"] == 120
    assert config["create_room_max_requests"] == 5
    assert config["join_room_max_requests"] == 1
    assert config["chat_api_max_requests"] == 100000


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_rate_limit_config_updates_existing_items(monkeypatch) -> None:
    """保存限流配置时应更新已有配置项并规范化值。"""

    class FakeConfigItem:
        def __init__(self, value: str) -> None:
            self.value = value
            self.name = ""
            self.description = ""
            self.updated_at = None
            self.saved = False

        async def save(self) -> None:
            self.saved = True

    items = {
        key: FakeConfigItem(str(default))
        for key, default in config_service.RATE_LIMIT_DEFAULT_CONFIG.items()
    }

    async def fake_find_config_item(_group: str, key: str):
        return items.get(key)

    monkeypatch.setattr(config_service, "find_config_item", fake_find_config_item)

    config = await config_service.save_rate_limit_config(
        {
            "enabled": True,
            "trust_proxy_headers": True,
            "window_seconds": "120",
            "max_requests": "240",
            "create_room_max_requests": "30",
            "join_room_max_requests": "50",
            "chat_api_max_requests": "70",
        }
    )

    assert config["enabled"] is True
    assert config["trust_proxy_headers"] is True
    assert config["window_seconds"] == 120
    assert config["max_requests"] == 240
    assert items["enabled"].value == "true"
    assert items["trust_proxy_headers"].value == "true"
    assert items["window_seconds"].value == "120"
    assert items["max_requests"].value == "240"
    assert all(item.saved for item in items.values())
