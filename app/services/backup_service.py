"""数据库备份核心服务。"""

from __future__ import annotations

import json
import logging
import os
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bson import json_util
from pymongo import ASCENDING

from app.config import APP_ENV
from app.models import ConfigItem
from app.models.backup_record import BackupRecord
from app.models.config_item import utc_now
from app.services.cloud_storage import (
    CloudFileInfo,
    CloudStorageBackend,
    SUPPORTED_PROVIDERS,
    create_backend,
)

logger = logging.getLogger(__name__)

# ---------- 默认配置 ----------

BACKUP_CONFIG_GROUP = "backup"
BACKUP_CONFIG_KEY = "backup_config"
BACKUP_FILENAME_PREFIX = "backup_"
BACKUP_FILENAME_SUFFIX = ".tar.gz"

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_BACKUP_CONFIG: dict[str, Any] = {
    "enabled": False,
    "local_dir": "backups",
    "local_retention": 5,
    "interval_hours": 24,
    "excluded_collections": [],
    "cloud_enabled": False,
    "cloud_providers": [],
    "cloud_path": "backups/pyfastadmin",
    "cloud_retention": 10,
    # 阿里云 OSS
    "oss_region": "",
    "oss_endpoint": "",
    "oss_access_key_id": "",
    "oss_access_key_secret": "",
    "oss_bucket": "",
    # 腾讯云 COS
    "cos_region": "",
    "cos_secret_id": "",
    "cos_secret_key": "",
    "cos_bucket": "",
}

# 不参与备份的系统集合
SYSTEM_COLLECTIONS = {"system.buckets", "system.views"}

# 测试环境变量（仅 APP_ENV=test/e2e 或 TEST_BACKUP_USE_ENV 开启时生效）
TEST_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "enabled": ("TEST_BACKUP_ENABLED",),
    "local_dir": ("TEST_BACKUP_LOCAL_DIR",),
    "local_retention": ("TEST_BACKUP_LOCAL_RETENTION",),
    "interval_hours": ("TEST_BACKUP_INTERVAL_HOURS",),
    "excluded_collections": ("TEST_BACKUP_EXCLUDED_COLLECTIONS",),
    "cloud_enabled": ("TEST_BACKUP_CLOUD_ENABLED",),
    "cloud_providers": ("TEST_BACKUP_CLOUD_PROVIDERS",),
    "cloud_path": ("TEST_BACKUP_CLOUD_PATH",),
    "cloud_retention": ("TEST_BACKUP_CLOUD_RETENTION",),
    "oss_region": ("TEST_BACKUP_OSS_REGION",),
    "oss_endpoint": ("TEST_BACKUP_OSS_ENDPOINT",),
    "oss_access_key_id": ("TEST_BACKUP_OSS_ACCESS_KEY_ID",),
    "oss_access_key_secret": ("TEST_BACKUP_OSS_ACCESS_KEY_SECRET",),
    "oss_bucket": ("TEST_BACKUP_OSS_BUCKET",),
    "cos_region": ("TEST_BACKUP_COS_REGION",),
    "cos_secret_id": ("TEST_BACKUP_COS_SECRET_ID",),
    "cos_secret_key": ("TEST_BACKUP_COS_SECRET_KEY",),
    "cos_bucket": ("TEST_BACKUP_COS_BUCKET",),
}

BOOL_CONFIG_KEYS = {"enabled", "cloud_enabled"}
INT_CONFIG_KEYS = {"local_retention", "interval_hours", "cloud_retention"}
LIST_CONFIG_KEYS = {"excluded_collections", "cloud_providers"}


# ---------- 内部工具 ----------


def _to_bool(value: Any, default: bool) -> bool:
    """把输入值转换为布尔值。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def _to_int(value: Any, default: int, *, minimum: int = 0) -> int:
    """把输入值转换为整数并保证下限。"""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _to_string(value: Any, default: str = "") -> str:
    """把输入值转换为去空格字符串。"""
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _split_csv(raw_value: str) -> list[str]:
    """把逗号分隔字符串切分为去重列表。"""
    result: list[str] = []
    for item in raw_value.split(","):
        normalized = item.strip()
        if not normalized or normalized in result:
            continue
        result.append(normalized)
    return result


def _read_test_env_value(keys: tuple[str, ...]) -> str | None:
    """读取第一个存在且非空的环境变量值。"""
    for key in keys:
        value = os.getenv(key)
        if value is None:
            continue
        normalized = value.strip()
        if normalized:
            return normalized
    return None


def _should_apply_test_env_overrides() -> bool:
    """判断是否启用测试环境变量覆盖。"""
    forced = os.getenv("TEST_BACKUP_USE_ENV", "").strip().lower()
    if forced in {"1", "true", "on", "yes"}:
        return True

    app_env = os.getenv("APP_ENV", APP_ENV).strip().lower()
    return app_env in {"test", "e2e"}


def _load_test_env_overrides() -> dict[str, Any]:
    """加载测试环境变量中的备份配置覆盖项。"""
    if not _should_apply_test_env_overrides():
        return {}

    overrides: dict[str, Any] = {}
    for config_key, env_keys in TEST_ENV_KEYS.items():
        raw_value = _read_test_env_value(env_keys)
        if raw_value is None:
            continue

        if config_key in BOOL_CONFIG_KEYS:
            overrides[config_key] = _to_bool(raw_value, default=False)
            continue

        if config_key in INT_CONFIG_KEYS:
            default_value = int(DEFAULT_BACKUP_CONFIG[config_key])
            minimum = 1 if config_key in {"local_retention", "interval_hours", "cloud_retention"} else 0
            overrides[config_key] = _to_int(raw_value, default=default_value, minimum=minimum)
            continue

        if config_key in LIST_CONFIG_KEYS:
            overrides[config_key] = _split_csv(raw_value)
            continue

        overrides[config_key] = raw_value

    return overrides


def _normalize_cloud_providers(raw_values: Any) -> list[str]:
    """清洗并去重云端供应商列表。"""
    if not isinstance(raw_values, list):
        return []

    result: list[str] = []
    for item in raw_values:
        provider = str(item).strip()
        if provider not in SUPPORTED_PROVIDERS:
            continue
        if provider not in result:
            result.append(provider)
    return result


def _normalize_excluded_collections(raw_values: Any) -> list[str]:
    """清洗排除集合列表并去重。"""
    if not isinstance(raw_values, list):
        return []

    result: list[str] = []
    for item in raw_values:
        name = str(item).strip()
        if not name or name in SYSTEM_COLLECTIONS:
            continue
        if name not in result:
            result.append(name)
    return result


def _normalize_config(payload: dict[str, Any]) -> dict[str, Any]:
    """将备份配置统一清洗为内部标准结构。"""
    config = DEFAULT_BACKUP_CONFIG.copy()

    config["enabled"] = _to_bool(payload.get("enabled"), default=config["enabled"])
    config["local_dir"] = _to_string(payload.get("local_dir"), default=config["local_dir"])
    config["local_retention"] = _to_int(
        payload.get("local_retention"),
        default=config["local_retention"],
        minimum=1,
    )
    config["interval_hours"] = _to_int(
        payload.get("interval_hours"),
        default=config["interval_hours"],
        minimum=1,
    )
    config["excluded_collections"] = _normalize_excluded_collections(payload.get("excluded_collections"))

    config["cloud_enabled"] = _to_bool(payload.get("cloud_enabled"), default=config["cloud_enabled"])
    config["cloud_providers"] = _normalize_cloud_providers(payload.get("cloud_providers"))
    config["cloud_path"] = _to_string(payload.get("cloud_path"), default=config["cloud_path"])
    config["cloud_retention"] = _to_int(
        payload.get("cloud_retention"),
        default=config["cloud_retention"],
        minimum=1,
    )

    config["oss_region"] = _to_string(payload.get("oss_region"))
    config["oss_endpoint"] = _to_string(payload.get("oss_endpoint"))
    config["oss_access_key_id"] = _to_string(payload.get("oss_access_key_id"))
    config["oss_access_key_secret"] = _to_string(payload.get("oss_access_key_secret"))
    config["oss_bucket"] = _to_string(payload.get("oss_bucket"))

    config["cos_region"] = _to_string(payload.get("cos_region"))
    config["cos_secret_id"] = _to_string(payload.get("cos_secret_id"))
    config["cos_secret_key"] = _to_string(payload.get("cos_secret_key"))
    config["cos_bucket"] = _to_string(payload.get("cos_bucket"))

    if not config["cloud_path"]:
        config["cloud_path"] = DEFAULT_BACKUP_CONFIG["cloud_path"]

    return config


def _resolve_local_dir(config: dict[str, Any]) -> Path:
    """将配置中的本地目录转换为绝对路径。"""
    local_dir = Path(str(config.get("local_dir", "backups")).strip() or "backups")
    if not local_dir.is_absolute():
        local_dir = PROJECT_ROOT / local_dir
    return local_dir


def _is_backup_archive(path: Path) -> bool:
    """判断是否为备份归档文件名。"""
    return path.name.startswith(BACKUP_FILENAME_PREFIX) and path.name.endswith(BACKUP_FILENAME_SUFFIX)


# ---------- 配置读写 ----------


async def get_backup_config() -> dict[str, Any]:
    """读取备份配置并合并默认值。"""
    item = await ConfigItem.find_one({"group": BACKUP_CONFIG_GROUP, "key": BACKUP_CONFIG_KEY})

    loaded: dict[str, Any] = {}
    if item and item.value.strip():
        try:
            parsed = json.loads(item.value)
        except json.JSONDecodeError:
            logger.warning("备份配置解析失败，已回退默认配置")
        else:
            if isinstance(parsed, dict):
                loaded = parsed

    config = _normalize_config(loaded)

    # 测试环境可通过 TEST_BACKUP_* 变量覆盖配置，方便 CI/E2E 直连云端。
    env_overrides = _load_test_env_overrides()
    if env_overrides:
        config = _normalize_config({**config, **env_overrides})

    return config


async def save_backup_config(payload: dict[str, Any]) -> dict[str, Any]:
    """保存备份配置到 ConfigItem。"""
    cleaned = _normalize_config(payload)
    json_value = json.dumps(cleaned, ensure_ascii=False)

    item = await ConfigItem.find_one({"group": BACKUP_CONFIG_GROUP, "key": BACKUP_CONFIG_KEY})
    if item is None:
        await ConfigItem(
            key=BACKUP_CONFIG_KEY,
            name="数据库备份配置",
            value=json_value,
            group=BACKUP_CONFIG_GROUP,
            description="自动备份与云端存储配置",
            updated_at=utc_now(),
        ).insert()
        return cleaned

    item.value = json_value
    item.updated_at = utc_now()
    await item.save()
    return cleaned


# ---------- 集合名称 ----------


async def get_collection_names() -> list[str]:
    """获取当前数据库的所有集合名称（排除系统集合）。"""
    from app.config import MONGO_DB
    from app.db import _mongo_client

    if _mongo_client is None:
        return []

    names = await _mongo_client[MONGO_DB].list_collection_names()
    return sorted(name for name in names if name not in SYSTEM_COLLECTIONS)


# ---------- 备份记录查询 ----------


async def list_backup_records(page: int = 1, page_size: int = 20) -> tuple[list[BackupRecord], int]:
    """分页查询备份记录，按创建时间倒序。"""
    safe_page = page if page > 0 else 1
    safe_size = page_size if page_size > 0 else 20

    total = await BackupRecord.find_all().count()
    skip = (safe_page - 1) * safe_size
    records = (
        await BackupRecord.find_all()
        .sort("-created_at")
        .skip(skip)
        .limit(safe_size)
        .to_list()
    )
    return records, total


async def delete_backup_record(record_id: str) -> bool:
    """删除备份记录及其本地文件和云端文件。"""
    from beanie import PydanticObjectId

    try:
        object_id = PydanticObjectId(record_id)
    except Exception:
        return False

    record = await BackupRecord.get(object_id)
    if record is None:
        return False

    config = await get_backup_config()
    local_file = _resolve_local_dir(config) / record.filename
    if local_file.exists():
        local_file.unlink()
        logger.info("已删除本地备份文件: %s", local_file)

    for upload in record.cloud_uploads:
        if upload.get("status") != "success":
            continue

        provider = str(upload.get("provider") or "").strip()
        remote_path = str(upload.get("path") or "").strip()
        if not provider or not remote_path:
            continue

        backend: CloudStorageBackend | None = None
        try:
            backend = create_backend(provider, config)
            await backend.delete_file(remote_path)
        except Exception as exc:
            logger.warning("删除云端文件失败 [%s] %s: %s", provider, remote_path, exc)
        finally:
            if backend is not None:
                await backend.close()

    await record.delete()
    return True


# ---------- 执行备份 ----------


async def run_backup() -> BackupRecord:
    """执行一次完整的数据库备份。"""
    from app.config import MONGO_DB
    from app.db import _mongo_client

    config = await get_backup_config()
    now = datetime.now(timezone.utc)
    filename = f"{BACKUP_FILENAME_PREFIX}{now.strftime('%Y%m%d_%H%M%S')}{BACKUP_FILENAME_SUFFIX}"

    record = BackupRecord(
        filename=filename,
        status="running",
        started_at=now,
        created_at=now,
        cloud_uploads=[],
    )
    await record.insert()

    try:
        if _mongo_client is None:
            raise RuntimeError("数据库未连接")

        db = _mongo_client[MONGO_DB]
        all_collections = await db.list_collection_names()
        excluded = set(config.get("excluded_collections", [])) | SYSTEM_COLLECTIONS
        target_collections = sorted(name for name in all_collections if name not in excluded)

        local_dir = _resolve_local_dir(config)
        local_dir.mkdir(parents=True, exist_ok=True)
        tar_path = local_dir / filename

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            for coll_name in target_collections:
                collection = db[coll_name]
                file_path = tmpdir_path / f"{coll_name}.json"

                with file_path.open("w", encoding="utf-8") as handle:
                    handle.write("[\n")
                    is_first = True
                    cursor = collection.find({}, sort=[("_id", ASCENDING)])
                    async for document in cursor:
                        if not is_first:
                            handle.write(",\n")
                        handle.write(json_util.dumps(document, ensure_ascii=False))
                        is_first = False
                    handle.write("\n]\n")

            with tarfile.open(tar_path, "w:gz") as tar:
                for json_file in sorted(tmpdir_path.glob("*.json")):
                    tar.add(json_file, arcname=json_file.name)

        record.collections = target_collections
        record.size = tar_path.stat().st_size

        cloud_uploads: list[dict[str, str]] = []
        if config.get("cloud_enabled") and config.get("cloud_providers"):
            cloud_path = str(config.get("cloud_path") or DEFAULT_BACKUP_CONFIG["cloud_path"]).strip().strip("/")
            if not cloud_path:
                cloud_path = DEFAULT_BACKUP_CONFIG["cloud_path"]

            for provider in config["cloud_providers"]:
                remote_key = f"{cloud_path}/{filename}" if cloud_path else filename
                upload_info: dict[str, str] = {
                    "provider": provider,
                    "path": remote_key,
                    "status": "success",
                    "error": "",
                }

                backend: CloudStorageBackend | None = None
                try:
                    backend = create_backend(provider, config)
                    await backend.upload_file(tar_path, remote_key)
                except Exception as exc:
                    upload_info["status"] = "failed"
                    upload_info["error"] = str(exc)
                    logger.error("云端上传失败 [%s]: %s", provider, exc)
                finally:
                    if backend is not None:
                        await backend.close()

                cloud_uploads.append(upload_info)

        record.cloud_uploads = cloud_uploads
        record.status = "success"
        record.error = ""
        record.finished_at = datetime.now(timezone.utc)
        await record.save()

        await _cleanup_local(local_dir, int(config.get("local_retention", 5)))

        if config.get("cloud_enabled") and config.get("cloud_providers"):
            cloud_retention = int(config.get("cloud_retention", 10))
            cloud_path = str(config.get("cloud_path") or DEFAULT_BACKUP_CONFIG["cloud_path"]).strip().strip("/")
            prefix = f"{cloud_path}/" if cloud_path else ""

            for provider in config["cloud_providers"]:
                backend = None
                try:
                    backend = create_backend(provider, config)
                    await _cleanup_cloud(backend, prefix, cloud_retention)
                except Exception as exc:
                    logger.warning("云端清理失败 [%s]: %s", provider, exc)
                finally:
                    if backend is not None:
                        await backend.close()

        logger.info("备份完成: %s (%d 字节, %d 集合)", filename, record.size, len(target_collections))
        return record

    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)
        record.finished_at = datetime.now(timezone.utc)
        await record.save()
        logger.error("备份失败: %s", exc)
        return record


async def _download_archive_from_cloud(
    record: BackupRecord,
    config: dict[str, Any],
    archive_path: Path,
) -> tuple[bool, str]:
    """当本地备份缺失时，尝试从云端回源归档文件。"""
    uploads = [
        item
        for item in (record.cloud_uploads or [])
        if item.get("status") == "success" and item.get("provider") and item.get("path")
    ]
    if not uploads:
        return False, "本地备份文件不存在，且没有可用的云端备份路径"

    errors: list[str] = []
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    for upload in uploads:
        provider = str(upload.get("provider") or "").strip()
        remote_key = str(upload.get("path") or "").strip()
        if not provider or not remote_key:
            continue

        backend: CloudStorageBackend | None = None
        try:
            backend = create_backend(provider, config)
            await backend.download_file(remote_key, archive_path)
            logger.info("已从云端回源备份文件 [%s]: %s", provider, remote_key)
            return True, f"已从 {provider} 下载备份文件"
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
            archive_path.unlink(missing_ok=True)
            logger.warning("云端回源失败 [%s] %s: %s", provider, remote_key, exc)
        finally:
            if backend is not None:
                await backend.close()

    if not errors:
        return False, "本地备份文件不存在，且没有可用的云端备份路径"
    return False, "云端回源失败：" + "；".join(errors)


async def restore_backup_record(record_id: str) -> tuple[bool, str]:
    """按备份记录恢复数据库数据。"""
    from beanie import PydanticObjectId

    from app.config import MONGO_DB
    from app.db import _mongo_client

    try:
        object_id = PydanticObjectId(record_id)
    except Exception:
        return False, "备份记录 ID 不合法"

    record = await BackupRecord.get(object_id)
    if record is None:
        return False, "备份记录不存在"

    config = await get_backup_config()
    archive_path = _resolve_local_dir(config) / record.filename
    if not archive_path.exists():
        downloaded, message = await _download_archive_from_cloud(record, config, archive_path)
        if not downloaded:
            return False, message

    if _mongo_client is None:
        return False, "数据库未连接"

    db = _mongo_client[MONGO_DB]
    restored_collections = 0

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            with tarfile.open(archive_path, "r:gz") as tar:
                _safe_extract_tar(tar, tmpdir_path)

            json_files = sorted(tmpdir_path.glob("*.json"))
            if not json_files:
                return False, "备份包中未找到可恢复的数据文件"

            for json_file in json_files:
                collection_name = json_file.stem.strip()
                if not collection_name or collection_name in SYSTEM_COLLECTIONS:
                    continue

                raw_text = json_file.read_text(encoding="utf-8")
                documents = json_util.loads(raw_text)
                if not isinstance(documents, list):
                    return False, f"集合 {collection_name} 的备份数据格式不合法"

                collection = db[collection_name]
                # 先清空再回灌，确保恢复结果与备份快照一致。
                await collection.delete_many({})
                if documents:
                    await collection.insert_many(documents, ordered=False)
                restored_collections += 1

    except Exception as exc:
        logger.error("恢复备份失败 [%s]: %s", record.filename, exc)
        return False, f"恢复失败：{exc}"

    if restored_collections == 0:
        return False, "备份包中没有可恢复的业务集合"

    return True, f"恢复完成，已恢复 {restored_collections} 个集合"



def _safe_extract_tar(tar: tarfile.TarFile, target_dir: Path) -> None:
    """安全解压 tar 包，防止路径穿越。"""
    base_dir = target_dir.resolve()
    for member in tar.getmembers():
        member_path = (target_dir / member.name).resolve()
        if os.path.commonpath([str(base_dir), str(member_path)]) != str(base_dir):
            raise ValueError("备份包包含非法路径，已拒绝恢复")

    tar.extractall(path=target_dir, filter="data")


# ---------- 清理逻辑 ----------


async def _cleanup_local(local_dir: Path, retention: int) -> None:
    """按保留份数清理本地旧备份文件。"""
    if retention <= 0 or not local_dir.exists():
        return

    files = sorted(
        [path for path in local_dir.glob(f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}") if _is_backup_archive(path)],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old_file in files[retention:]:
        old_file.unlink(missing_ok=True)
        logger.info("已清理本地旧备份: %s", old_file.name)


async def _cleanup_cloud(backend: CloudStorageBackend, prefix: str, retention: int) -> None:
    """按保留份数清理云端旧备份文件。"""
    if retention <= 0:
        return

    files: list[CloudFileInfo] = await backend.list_files(prefix)
    backup_files = [item for item in files if _is_backup_archive(Path(item.key))]
    backup_files.sort(key=lambda item: item.key, reverse=True)

    for old_file in backup_files[retention:]:
        await backend.delete_file(old_file.key)
        logger.info("已清理云端旧备份: %s", old_file.key)
