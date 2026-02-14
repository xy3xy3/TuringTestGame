"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .apps.admin.controllers.admin_users import router as admin_users_router
from .apps.admin.controllers.ai_models import router as ai_models_router
from .apps.admin.controllers.auth import router as auth_router
from .apps.admin.controllers.backup import router as backup_router
from .apps.admin.controllers.config import router as config_router
from .apps.admin.controllers.game_rooms import router as game_rooms_router
from .apps.admin.controllers.logs import router as logs_router
from .apps.admin.controllers.prompt_templates import router as prompt_templates_router
from .apps.admin.controllers.rbac import router as admin_router
from .apps.game.controllers.game import router as game_router
from .config import APP_NAME, SECRET_KEY
from .db import close_db, init_db
from .middleware.auth import AdminAuthMiddleware
from .middleware.rate_limit import GameRateLimitMiddleware
from .services.auth_service import ensure_default_admin
from .services.backup_scheduler import start_scheduler, stop_scheduler
from .services.cleanup_service import start_cleanup_scheduler, stop_cleanup_scheduler
from .services.redis_service import close_redis_client
from .services.role_service import ensure_default_roles

BASE_DIR = Path(__file__).resolve().parent

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化，停止时清理资源。"""
    # 启动时执行
    await init_db()
    await ensure_default_roles()
    await ensure_default_admin()
    start_scheduler()
    start_cleanup_scheduler()

    yield

    # 停止时执行
    stop_scheduler()
    stop_cleanup_scheduler()
    await close_redis_client()
    await close_db()


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.add_middleware(GameRateLimitMiddleware)
app.add_middleware(AdminAuthMiddleware, exempt_paths={"/admin/logout", "/game", "/game/create", "/game/join", "/game/api"})
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="pfa_session")
app.include_router(game_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(admin_users_router)
app.include_router(ai_models_router)
app.include_router(config_router)
app.include_router(prompt_templates_router)
app.include_router(logs_router)
app.include_router(backup_router)
app.include_router(game_rooms_router)


@app.get("/")
async def root() -> RedirectResponse:
    """根路径统一跳转到游戏首页。"""
    return RedirectResponse(url="/game", status_code=302)
