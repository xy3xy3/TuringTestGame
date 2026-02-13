"""测试公共 fixture。"""

from __future__ import annotations

import os
import sys
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
    return os.getenv("TEST_MONGO_DB", "pyfastadmin_test")


@pytest.fixture(scope="session")
def e2e_mongo_db_name() -> str:
    return os.getenv("TEST_E2E_MONGO_DB", "pyfastadmin_e2e_test")


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
