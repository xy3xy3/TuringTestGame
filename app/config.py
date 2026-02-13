"""应用配置（通过 .env 覆盖）。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 优先加载项目根目录下的 .env
BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

APP_NAME = os.getenv("APP_NAME", "PyFastAdmin")
APP_ENV = os.getenv("APP_ENV", "dev")

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "pyfastadmin")

APP_PORT = int(os.getenv("APP_PORT", "8000"))
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123")
