"""E2E 测试 fixture。"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError

ROOT_DIR = Path(__file__).resolve().parents[2]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_server_ready(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/admin/login", timeout=2.0)
            if response.status_code < 500:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Server did not start in time: {base_url}")


def _terminate_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    stdout = ""
    stderr = ""
    if process.stdout:
        stdout = process.stdout.read().strip()
    if process.stderr:
        stderr = process.stderr.read().strip()
    return stdout, stderr


@pytest.fixture(scope="function")
def e2e_base_url(test_mongo_url: str, e2e_mongo_db_name: str) -> Iterator[str]:
    client = MongoClient(test_mongo_url)
    try:
        client.drop_database(e2e_mongo_db_name)
    except OperationFailure as exc:
        pytest.skip(
            "MongoDB 用户无 dropDatabase 权限，请配置 TEST_MONGO_URL 为有测试库权限的连接串: "
            f"{exc.details.get('errmsg', str(exc))}"
        )
    except PyMongoError as exc:
        pytest.skip(f"MongoDB 不可用，跳过 E2E: {exc}")

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "APP_PORT": str(port),
            "MONGO_URL": test_mongo_url,
            "MONGO_DB": e2e_mongo_db_name,
            "ADMIN_USER": os.getenv("TEST_ADMIN_USER", "e2e_admin"),
            "ADMIN_PASS": os.getenv("TEST_ADMIN_PASS", "e2e_pass_123"),
            "SECRET_KEY": os.getenv("TEST_SECRET_KEY", "test-secret-key"),
        }
    )

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        try:
            _wait_server_ready(base_url)
        except RuntimeError as exc:
            stdout, stderr = _terminate_process(process)
            debug = "\n".join(part for part in [stdout[-800:], stderr[-800:]] if part)
            raise RuntimeError(f"{exc}\n{debug}") from exc

        yield base_url
    finally:
        _terminate_process(process)
        try:
            client.drop_database(e2e_mongo_db_name)
        except PyMongoError:
            pass
        client.close()
