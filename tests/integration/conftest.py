"""集成测试 fixture。"""

from __future__ import annotations

import pytest_asyncio


@pytest_asyncio.fixture
async def initialized_db(monkeypatch, mongo_cleanup, test_mongo_url: str, test_mongo_db_name: str):
    from app import db as app_db

    monkeypatch.setattr(app_db, "MONGO_URL", test_mongo_url)
    monkeypatch.setattr(app_db, "MONGO_DB", test_mongo_db_name)

    await app_db.init_db()
    try:
        yield
    finally:
        await app_db.close_db()
