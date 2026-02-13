from __future__ import annotations

import pytest

from app.services import cloud_storage


@pytest.mark.unit
def test_create_backend_normalizes_oss_region_prefix(monkeypatch) -> None:
    """OSS region 误填 oss- 前缀时应自动清洗。"""
    captured: dict[str, str] = {}

    class FakeAliyunBackend:
        def __init__(self, region: str, access_key_id: str, access_key_secret: str, bucket: str, endpoint: str = "") -> None:
            captured.update(
                {
                    "region": region,
                    "access_key_id": access_key_id,
                    "access_key_secret": access_key_secret,
                    "bucket": bucket,
                    "endpoint": endpoint,
                }
            )

    monkeypatch.setattr(cloud_storage, "AliyunOSSBackend", FakeAliyunBackend)

    backend = cloud_storage.create_backend(
        "aliyun_oss",
        {
            "oss_region": "oss-cn-guangzhou",
            "oss_endpoint": "oss-cn-guangzhou.aliyuncs.com",
            "oss_access_key_id": "ak",
            "oss_access_key_secret": "sk",
            "oss_bucket": "bucket-demo",
        },
    )

    assert isinstance(backend, FakeAliyunBackend)
    assert captured["region"] == "cn-guangzhou"


@pytest.mark.unit
def test_create_backend_can_infer_oss_region_from_endpoint(monkeypatch) -> None:
    """未显式填写 OSS region 时，允许从 endpoint 推导。"""
    captured: dict[str, str] = {}

    class FakeAliyunBackend:
        def __init__(self, region: str, access_key_id: str, access_key_secret: str, bucket: str, endpoint: str = "") -> None:
            captured["region"] = region
            captured["endpoint"] = endpoint

    monkeypatch.setattr(cloud_storage, "AliyunOSSBackend", FakeAliyunBackend)

    backend = cloud_storage.create_backend(
        "aliyun_oss",
        {
            "oss_region": "",
            "oss_endpoint": "oss-cn-hangzhou.aliyuncs.com",
            "oss_access_key_id": "ak",
            "oss_access_key_secret": "sk",
            "oss_bucket": "bucket-demo",
        },
    )

    assert isinstance(backend, FakeAliyunBackend)
    assert captured["region"] == "cn-hangzhou"
