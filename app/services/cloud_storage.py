"""云存储后端抽象与实现（阿里云 OSS / 腾讯云 COS）。"""

from __future__ import annotations

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = {"aliyun_oss", "tencent_cos"}


def _normalize_oss_region(region: str) -> str:
    """规范化 OSS region，兼容误填 oss- 前缀。"""
    normalized = region.strip()
    if normalized.startswith("oss-"):
        return normalized[4:]
    return normalized


@dataclass
class CloudFileInfo:
    """云端文件信息。"""

    key: str
    size: int
    last_modified: str


class CloudStorageBackend(ABC):
    """云存储后端抽象基类。"""

    @abstractmethod
    async def upload_file(self, local_path: Path, remote_key: str) -> None:
        """上传本地文件到云端。"""

    @abstractmethod
    async def download_file(self, remote_key: str, local_path: Path) -> None:
        """从云端下载文件到本地。"""

    @abstractmethod
    async def delete_file(self, remote_key: str) -> None:
        """删除云端文件。"""

    @abstractmethod
    async def list_files(self, prefix: str) -> list[CloudFileInfo]:
        """列出指定前缀下的云端文件。"""

    @abstractmethod
    async def close(self) -> None:
        """释放资源。"""


class AliyunOSSBackend(CloudStorageBackend):
    """阿里云 OSS 后端，优先原生 async，缺依赖时降级为线程封装。"""

    def __init__(
        self,
        region: str,
        access_key_id: str,
        access_key_secret: str,
        bucket: str,
        endpoint: str = "",
    ) -> None:
        from alibabacloud_oss_v2 import Client, Config, Credentials, CredentialsProvider

        class StaticCredentialsProvider(CredentialsProvider):
            """基于固定 AK/SK 的凭证提供器。"""

            def __init__(self, key_id: str, key_secret: str) -> None:
                self._key_id = key_id
                self._key_secret = key_secret

            def get_credentials(self) -> Credentials:
                return Credentials(self._key_id, self._key_secret)

        cred_provider = StaticCredentialsProvider(access_key_id, access_key_secret)
        cfg = Config(region=region, credentials_provider=cred_provider)
        if endpoint:
            cfg.endpoint = endpoint

        self._bucket = bucket
        self._async_client: Any | None = None
        self._sync_client: Any | None = None

        try:
            from alibabacloud_oss_v2.aio import AsyncClient

            self._async_client = AsyncClient(cfg)
        except ImportError:
            # aiohttp 缺失时回退到同步客户端 + asyncio.to_thread，避免功能不可用。
            self._sync_client = Client(cfg)
            logger.warning("未检测到 aiohttp，OSS SDK 降级为线程模式执行")
        else:
            self._sync_client = Client(cfg)

    async def upload_file(self, local_path: Path, remote_key: str) -> None:
        """上传本地文件到阿里云 OSS。"""
        from alibabacloud_oss_v2.models import PutObjectRequest

        request = PutObjectRequest(
            bucket=self._bucket,
            key=remote_key,
            body=local_path.read_bytes(),
        )

        if self._async_client is not None:
            await self._async_client.put_object(request)
        else:
            await asyncio.to_thread(self._sync_client.put_object, request)

        logger.info("OSS 上传完成: %s -> %s", local_path.name, remote_key)

    async def download_file(self, remote_key: str, local_path: Path) -> None:
        """从阿里云 OSS 下载文件到本地。"""
        from alibabacloud_oss_v2.models import GetObjectRequest

        local_path.parent.mkdir(parents=True, exist_ok=True)
        request = GetObjectRequest(bucket=self._bucket, key=remote_key)

        if self._async_client is not None:
            result = await self._async_client.get_object(request)
            stream = result.body
            if stream is None:
                raise RuntimeError("OSS 返回空响应体")

            try:
                # 兼容 SDK 中 iter_bytes 可能返回协程或异步迭代器两种形态。
                stream_iter = stream.iter_bytes()
                if inspect.isawaitable(stream_iter):
                    stream_iter = await stream_iter

                with local_path.open("wb") as handle:
                    async for chunk in stream_iter:
                        if chunk:
                            handle.write(chunk)
            finally:
                close_result = stream.close()
                if inspect.isawaitable(close_result):
                    await close_result
        else:
            await asyncio.to_thread(
                self._sync_client.get_object_to_file,
                request,
                str(local_path),
            )

        logger.info("OSS 下载完成: %s -> %s", remote_key, local_path)

    async def delete_file(self, remote_key: str) -> None:
        """删除阿里云 OSS 文件。"""
        from alibabacloud_oss_v2.models import DeleteObjectRequest

        request = DeleteObjectRequest(bucket=self._bucket, key=remote_key)
        if self._async_client is not None:
            await self._async_client.delete_object(request)
        else:
            await asyncio.to_thread(self._sync_client.delete_object, request)
        logger.info("OSS 删除完成: %s", remote_key)

    async def list_files(self, prefix: str) -> list[CloudFileInfo]:
        """列出阿里云 OSS 指定前缀的文件。"""
        from alibabacloud_oss_v2.models import ListObjectsV2Request

        result: list[CloudFileInfo] = []
        continuation_token: str | None = None

        while True:
            request = ListObjectsV2Request(bucket=self._bucket, prefix=prefix, max_keys=1000)
            if continuation_token:
                request.continuation_token = continuation_token

            if self._async_client is not None:
                response = await self._async_client.list_objects_v2(request)
            else:
                response = await asyncio.to_thread(self._sync_client.list_objects_v2, request)

            for obj in response.contents or []:
                if not obj.key:
                    continue
                result.append(
                    CloudFileInfo(
                        key=str(obj.key),
                        size=int(obj.size or 0),
                        last_modified=str(obj.last_modified or ""),
                    )
                )

            if not response.is_truncated:
                break
            continuation_token = response.next_continuation_token
            if not continuation_token:
                break

        return result

    async def close(self) -> None:
        """关闭 OSS 客户端。"""
        if self._async_client is None:
            return

        close_func = getattr(self._async_client, "close", None)
        if not callable(close_func):
            return

        maybe_awaitable = close_func()
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable


class TencentCOSBackend(CloudStorageBackend):
    """腾讯云 COS 后端（通过 asyncio.to_thread 封装同步调用）。"""

    def __init__(
        self,
        region: str,
        secret_id: str,
        secret_key: str,
        bucket: str,
    ) -> None:
        from qcloud_cos import CosConfig, CosS3Client

        config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key)
        self._client = CosS3Client(config)
        self._bucket = bucket

    async def upload_file(self, local_path: Path, remote_key: str) -> None:
        """上传本地文件到腾讯云 COS。"""
        await asyncio.to_thread(
            self._client.put_object_from_local_file,
            Bucket=self._bucket,
            LocalFilePath=str(local_path),
            Key=remote_key,
        )
        logger.info("COS 上传完成: %s -> %s", local_path.name, remote_key)

    async def download_file(self, remote_key: str, local_path: Path) -> None:
        """从腾讯云 COS 下载文件到本地。"""
        local_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            self._client.download_file,
            Bucket=self._bucket,
            Key=remote_key,
            DestFilePath=str(local_path),
        )
        logger.info("COS 下载完成: %s -> %s", remote_key, local_path)

    async def delete_file(self, remote_key: str) -> None:
        """删除腾讯云 COS 文件。"""
        await asyncio.to_thread(
            self._client.delete_object,
            Bucket=self._bucket,
            Key=remote_key,
        )
        logger.info("COS 删除完成: %s", remote_key)

    async def list_files(self, prefix: str) -> list[CloudFileInfo]:
        """列出腾讯云 COS 指定前缀的文件。"""
        result: list[CloudFileInfo] = []
        marker = ""

        while True:
            response = await asyncio.to_thread(
                self._client.list_objects,
                Bucket=self._bucket,
                Prefix=prefix,
                Marker=marker,
                MaxKeys=1000,
            )

            contents = response.get("Contents", [])
            for obj in contents:
                key = str(obj.get("Key", "")).strip()
                if not key:
                    continue
                result.append(
                    CloudFileInfo(
                        key=key,
                        size=int(obj.get("Size", 0)),
                        last_modified=str(obj.get("LastModified", "")),
                    )
                )

            is_truncated = response.get("IsTruncated")
            if is_truncated in (False, "false", "False", 0, None):
                break

            marker = str(response.get("NextMarker") or "")
            if not marker and contents:
                marker = str(contents[-1].get("Key") or "")
            if not marker:
                break

        return result

    async def close(self) -> None:
        """释放 COS 资源。"""
        return


def create_backend(provider: str, config: dict[str, Any]) -> CloudStorageBackend:
    """根据供应商名称创建云存储后端实例。"""
    normalized = str(provider).strip()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ValueError(f"不支持的云存储供应商: {provider}")

    if normalized == "aliyun_oss":
        oss_region = _normalize_oss_region(str(config.get("oss_region", "")))
        oss_endpoint = str(config.get("oss_endpoint", "")).strip()
        if not oss_region and oss_endpoint.startswith("oss-") and ".aliyuncs.com" in oss_endpoint:
            oss_region = oss_endpoint[len("oss-") :].split(".aliyuncs.com", 1)[0]

        required_values = {
            "OSS Region": oss_region,
            "OSS AccessKeyId": str(config.get("oss_access_key_id", "")).strip(),
            "OSS AccessKeySecret": str(config.get("oss_access_key_secret", "")).strip(),
            "OSS Bucket": str(config.get("oss_bucket", "")).strip(),
        }
        missing = [label for label, value in required_values.items() if not value]
        if missing:
            raise ValueError(f"阿里云 OSS 配置不完整：缺少 {', '.join(missing)}")

        return AliyunOSSBackend(
            region=oss_region,
            access_key_id=required_values["OSS AccessKeyId"],
            access_key_secret=required_values["OSS AccessKeySecret"],
            bucket=required_values["OSS Bucket"],
            endpoint=oss_endpoint,
        )

    required = {
        "cos_region": "COS Region",
        "cos_secret_id": "COS SecretId",
        "cos_secret_key": "COS SecretKey",
        "cos_bucket": "COS Bucket",
    }
    missing = [label for key, label in required.items() if not str(config.get(key, "")).strip()]
    if missing:
        raise ValueError(f"腾讯云 COS 配置不完整：缺少 {', '.join(missing)}")

    return TencentCOSBackend(
        region=str(config.get("cos_region", "")).strip(),
        secret_id=str(config.get("cos_secret_id", "")).strip(),
        secret_key=str(config.get("cos_secret_key", "")).strip(),
        bucket=str(config.get("cos_bucket", "")).strip(),
    )
