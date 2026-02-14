from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import cleanup_service


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_cleanup_config_returns_defaults_when_missing(monkeypatch) -> None:
    """未配置清理参数时应回退默认值。"""

    async def fake_find_one(_query):
        return None

    monkeypatch.setattr(cleanup_service.ConfigItem, "find_one", fake_find_one)

    config = await cleanup_service.get_cleanup_config()

    assert config["enabled"] is True
    assert config["retention_days"] == cleanup_service.DEFAULT_RETENTION_DAYS
    assert config["interval_hours"] == cleanup_service.DEFAULT_CLEANUP_INTERVAL_HOURS
    assert config["waiting_timeout_minutes"] == cleanup_service.DEFAULT_WAITING_TIMEOUT_MINUTES


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_cleanup_config_normalizes_dirty_values(monkeypatch) -> None:
    """读取清理参数时应做类型转换与边界裁剪。"""

    mapping = {
        cleanup_service.CLEANUP_ENABLED_KEY: SimpleNamespace(value="false"),
        cleanup_service.CLEANUP_RETENTION_DAYS_KEY: SimpleNamespace(value="-9"),
        cleanup_service.CLEANUP_INTERVAL_HOURS_KEY: SimpleNamespace(value="abc"),
        cleanup_service.CLEANUP_WAITING_TIMEOUT_MINUTES_KEY: SimpleNamespace(value="20000"),
    }

    async def fake_find_one(query):
        return mapping.get(query["key"])

    monkeypatch.setattr(cleanup_service.ConfigItem, "find_one", fake_find_one)

    config = await cleanup_service.get_cleanup_config()

    assert config["enabled"] is False
    assert config["retention_days"] == 1
    assert config["interval_hours"] == cleanup_service.DEFAULT_CLEANUP_INTERVAL_HOURS
    assert config["waiting_timeout_minutes"] == 10080


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_cleanup_config_updates_existing_items(monkeypatch) -> None:
    """保存清理配置时应规范化并写回全部参数。"""

    class FakeConfigItem:
        def __init__(self, value: str) -> None:
            self.value = value
            self.updated_at = None
            self.saved = False

        async def save(self) -> None:
            self.saved = True

    items = {
        cleanup_service.CLEANUP_ENABLED_KEY: FakeConfigItem("true"),
        cleanup_service.CLEANUP_RETENTION_DAYS_KEY: FakeConfigItem("7"),
        cleanup_service.CLEANUP_INTERVAL_HOURS_KEY: FakeConfigItem("24"),
        cleanup_service.CLEANUP_WAITING_TIMEOUT_MINUTES_KEY: FakeConfigItem("30"),
    }

    async def fake_find_one(query):
        return items.get(query["key"])

    monkeypatch.setattr(cleanup_service.ConfigItem, "find_one", fake_find_one)

    config = await cleanup_service.save_cleanup_config(
        enabled=False,
        retention_days=9999,
        interval_hours=0,
        waiting_timeout_minutes=-1,
    )

    assert config["enabled"] is False
    assert config["retention_days"] == 365
    assert config["interval_hours"] == 1
    assert config["waiting_timeout_minutes"] == 1
    assert items[cleanup_service.CLEANUP_ENABLED_KEY].value == "false"
    assert items[cleanup_service.CLEANUP_RETENTION_DAYS_KEY].value == "365"
    assert items[cleanup_service.CLEANUP_INTERVAL_HOURS_KEY].value == "1"
    assert items[cleanup_service.CLEANUP_WAITING_TIMEOUT_MINUTES_KEY].value == "1"
    assert all(item.saved for item in items.values())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_finished_games_includes_waiting_timeout_cleanup(monkeypatch) -> None:
    """清理任务应同时处理已结束超期房间与等待加入超时房间。"""

    async def fake_get_cleanup_config():
        return {
            "enabled": True,
            "retention_days": 7,
            "interval_hours": 24,
            "waiting_timeout_minutes": 30,
        }

    monkeypatch.setattr(cleanup_service, "get_cleanup_config", fake_get_cleanup_config)

    finished_rooms = [SimpleNamespace(room_id="F001", id="oid-f1")]
    waiting_rooms = [SimpleNamespace(room_id="W001", id="oid-w1"), SimpleNamespace(room_id="W002", id="oid-w2")]
    seen_queries: list[dict] = []

    class FakeFindToList:
        def __init__(self, results):
            self._results = results

        async def to_list(self):
            return self._results

    def fake_game_room_find(query):
        seen_queries.append(query)
        phase = query.get("phase")
        if phase == "finished":
            assert "finished_at" in query and "$lt" in query["finished_at"]
            assert isinstance(query["finished_at"]["$lt"], datetime)
            return FakeFindToList(finished_rooms)
        if phase == "waiting":
            assert "created_at" in query and "$lt" in query["created_at"]
            assert isinstance(query["created_at"]["$lt"], datetime)
            return FakeFindToList(waiting_rooms)
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr(cleanup_service.GameRoom, "find", fake_game_room_find)

    async def fake_cleanup_room_batch(rooms):
        if not rooms:
            return {"rooms": 0, "players": 0, "rounds": 0, "votes": 0}
        if rooms[0].room_id.startswith("F"):
            return {"rooms": 1, "players": 3, "rounds": 4, "votes": 5}
        return {"rooms": 2, "players": 7, "rounds": 0, "votes": 0}

    monkeypatch.setattr(cleanup_service, "_cleanup_room_batch", fake_cleanup_room_batch)

    stats = await cleanup_service.cleanup_finished_games()

    assert stats["rooms"] == 3
    assert stats["players"] == 10
    assert stats["rounds"] == 4
    assert stats["votes"] == 5
    assert stats["expired_waiting_rooms"] == 2
    assert stats["expired_waiting_players"] == 7
    assert [query.get("phase") for query in seen_queries] == ["finished", "waiting"]
