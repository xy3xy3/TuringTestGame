from __future__ import annotations

import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.db as db_module
from app.services import backup_service


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_backup_config_ignores_test_env_in_dev(monkeypatch) -> None:
    """开发环境默认不使用 TEST_BACKUP_* 覆盖。"""

    async def fake_find_one(_cls, _query: dict):
        return None

    monkeypatch.setattr(backup_service.ConfigItem, "find_one", classmethod(fake_find_one))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("TEST_BACKUP_CLOUD_ENABLED", "true")
    monkeypatch.setenv("TEST_BACKUP_CLOUD_PROVIDERS", "aliyun_oss,tencent_cos")

    config = await backup_service.get_backup_config()

    assert config["cloud_enabled"] is False
    assert config["cloud_providers"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_backup_config_applies_test_env_in_test_env(monkeypatch) -> None:
    """测试环境自动应用 TEST_BACKUP_* 覆盖。"""

    async def fake_find_one(_cls, _query: dict):
        return None

    monkeypatch.setattr(backup_service.ConfigItem, "find_one", classmethod(fake_find_one))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TEST_BACKUP_CLOUD_ENABLED", "true")
    monkeypatch.setenv("TEST_BACKUP_CLOUD_PROVIDERS", "aliyun_oss,invalid,tencent_cos,aliyun_oss")
    monkeypatch.setenv("TEST_BACKUP_CLOUD_PATH", "e2e/backup-path")
    monkeypatch.setenv("TEST_BACKUP_CLOUD_RETENTION", "7")
    monkeypatch.setenv("TEST_BACKUP_EXCLUDED_COLLECTIONS", "logs,temp,logs")

    config = await backup_service.get_backup_config()

    assert config["cloud_enabled"] is True
    assert config["cloud_providers"] == ["aliyun_oss", "tencent_cos"]
    assert config["cloud_path"] == "e2e/backup-path"
    assert config["cloud_retention"] == 7
    assert config["excluded_collections"] == ["logs", "temp"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_backup_config_allows_forced_override_in_dev(monkeypatch) -> None:
    """开发环境可通过开关强制启用 TEST_BACKUP_* 覆盖。"""

    async def fake_find_one(_cls, _query: dict):
        return None

    monkeypatch.setattr(backup_service.ConfigItem, "find_one", classmethod(fake_find_one))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("TEST_BACKUP_USE_ENV", "1")
    monkeypatch.setenv("TEST_BACKUP_ENABLED", "on")
    monkeypatch.setenv("TEST_BACKUP_INTERVAL_HOURS", "6")
    monkeypatch.setenv("TEST_BACKUP_LOCAL_RETENTION", "3")

    config = await backup_service.get_backup_config()

    assert config["enabled"] is True
    assert config["interval_hours"] == 6
    assert config["local_retention"] == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_restore_backup_record_rejects_invalid_id() -> None:
    """非法记录 ID 应该直接返回失败。"""
    success, message = await backup_service.restore_backup_record("invalid-id")

    assert success is False
    assert message == "备份记录 ID 不合法"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_restore_backup_record_rejects_missing_record(monkeypatch) -> None:
    """不存在的记录应返回明确错误。"""

    async def fake_get(_cls, _object_id):
        return None

    monkeypatch.setattr(backup_service.BackupRecord, "get", classmethod(fake_get))

    success, message = await backup_service.restore_backup_record("507f1f77bcf86cd799439011")

    assert success is False
    assert message == "备份记录不存在"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_restore_backup_record_downloads_from_cloud_when_local_missing(monkeypatch, tmp_path: Path) -> None:
    """本地不存在时应自动尝试云端回源后再执行恢复。"""

    fake_record = SimpleNamespace(
        filename="backup_20260210_120000.tar.gz",
        cloud_uploads=[{"provider": "aliyun_oss", "path": "backup/key", "status": "success"}],
    )

    async def fake_get(_cls, _object_id):
        return fake_record

    async def fake_get_backup_config():
        return {"local_dir": str(tmp_path)}

    async def fake_download(_record, _config, archive_path: Path):
        source_json = tmp_path / "users.json"
        source_json.write_text('[{"_id": {"$oid": "507f1f77bcf86cd799439011"}, "username": "alice"}]', encoding="utf-8")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(source_json, arcname="users.json")
        return True, "ok"

    class FakeCollection:
        def __init__(self) -> None:
            self.deleted_called = False
            self.inserted_docs: list[dict] = []

        async def delete_many(self, _query: dict) -> None:
            self.deleted_called = True

        async def insert_many(self, docs: list[dict], ordered: bool = False) -> None:
            _ = ordered
            self.inserted_docs = docs

    class FakeDB:
        def __init__(self) -> None:
            self.collections: dict[str, FakeCollection] = {}

        def __getitem__(self, name: str) -> FakeCollection:
            if name not in self.collections:
                self.collections[name] = FakeCollection()
            return self.collections[name]

    class FakeMongoClient:
        def __init__(self) -> None:
            self.db = FakeDB()

        def __getitem__(self, _name: str) -> FakeDB:
            return self.db

    fake_client = FakeMongoClient()

    monkeypatch.setattr(backup_service.BackupRecord, "get", classmethod(fake_get))
    monkeypatch.setattr(backup_service, "get_backup_config", fake_get_backup_config)
    monkeypatch.setattr(backup_service, "_download_archive_from_cloud", fake_download)
    monkeypatch.setattr(db_module, "_mongo_client", fake_client)

    success, message = await backup_service.restore_backup_record("507f1f77bcf86cd799439011")

    assert success is True
    assert "已恢复 1 个集合" in message
    restored = fake_client.db.collections["users"]
    assert restored.deleted_called is True
    assert len(restored.inserted_docs) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_restore_backup_record_returns_cloud_error_when_local_missing(monkeypatch, tmp_path: Path) -> None:
    """本地与云端都不可用时应返回云端回源错误。"""

    fake_record = SimpleNamespace(
        filename="backup_20260210_120000.tar.gz",
        cloud_uploads=[{"provider": "aliyun_oss", "path": "backup/key", "status": "success"}],
    )

    async def fake_get(_cls, _object_id):
        return fake_record

    async def fake_get_backup_config():
        return {"local_dir": str(tmp_path)}

    async def fake_download(_record, _config, _archive_path: Path):
        return False, "云端回源失败：鉴权错误"

    monkeypatch.setattr(backup_service.BackupRecord, "get", classmethod(fake_get))
    monkeypatch.setattr(backup_service, "get_backup_config", fake_get_backup_config)
    monkeypatch.setattr(backup_service, "_download_archive_from_cloud", fake_download)

    success, message = await backup_service.restore_backup_record("507f1f77bcf86cd799439011")

    assert success is False
    assert message == "云端回源失败：鉴权错误"
