"""测试公共 fixture。"""

from __future__ import annotations

import os
import sys
import hashlib
import uuid
from pathlib import Path
from typing import Iterator

import pytest
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Load project .env for local test runs so TEST_MONGO_URL can follow dev compose settings.
load_dotenv(ROOT_DIR / ".env")


@pytest.fixture(scope="session")
def test_mongo_url() -> str:
    return os.getenv("TEST_MONGO_URL") or os.getenv("MONGO_URL", "mongodb://localhost:27017")


@pytest.fixture(scope="session")
def test_mongo_db_name() -> str:
    return os.getenv("TEST_MONGO_DB", "TuringTestGame_test")


@pytest.fixture(scope="function")
def e2e_mongo_db_name(request: pytest.FixtureRequest) -> str:
    """为每个 E2E 用例生成独立的测试库名，避免并发启动服务时互相污染数据。"""

    base = os.getenv("TEST_E2E_MONGO_DB", "pyfastadmin_e2e_test").strip() or "pyfastadmin_e2e_test"
    worker = os.getenv("PYTEST_XDIST_WORKER", "gw0")

    # MongoDB 数据库名长度上限为 63，且 pytest nodeid 可能包含 "::" 等字符，不适合直接拼接。
    # 这里使用哈希来稳定区分用例，确保名称短且安全。
    node_key = f"{request.node.nodeid}"
    digest = hashlib.sha1(node_key.encode("utf-8")).hexdigest()[:12]
    nonce = uuid.uuid4().hex[:6]
    name = f"{base}_{worker}_{digest}_{nonce}"
    if len(name) <= 63:
        return name

    # 尽量保留 base 的前缀以便排查；超长时截断到 63 以内。
    overflow = len(name) - 63
    base_trimmed = base[:-overflow] if overflow < len(base) else base[:8]
    name = f"{base_trimmed}_{worker}_{digest}_{nonce}"
    return name[:63]


@pytest.fixture
def mongo_cleanup(test_mongo_url: str, test_mongo_db_name: str) -> Iterator[None]:
    client = MongoClient(test_mongo_url)
    try:
        try:
            client.drop_database(test_mongo_db_name)
        except OperationFailure as exc:
            pytest.skip(
                "MongoDB 用户无 dropDatabase 权限，请配置 TEST_MONGO_URL 为有测试库权限的连接串: "
                f"{exc.details.get('errmsg', str(exc))}"
            )
        except PyMongoError as exc:
            pytest.skip(f"MongoDB 不可用，跳过集成测试: {exc}")

        yield
    finally:
        try:
            client.drop_database(test_mongo_db_name)
        except PyMongoError:
            pass
        client.close()
