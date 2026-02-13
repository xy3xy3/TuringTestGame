"""FastAPI 应用入口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .apps.admin.controllers.admin_users import router as admin_users_router
from .apps.admin.controllers.auth import router as auth_router
from .apps.admin.controllers.backup import router as backup_router
from .apps.admin.controllers.config import router as config_router
from .apps.admin.controllers.logs import router as logs_router
from .apps.admin.controllers.rbac import router as admin_router
from .config import APP_NAME, SECRET_KEY
from .db import close_db, init_db
from .middleware.auth import AdminAuthMiddleware
from .services.auth_service import ensure_default_admin
from .services.backup_scheduler import start_scheduler, stop_scheduler
from .services.role_service import ensure_default_roles

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.add_middleware(AdminAuthMiddleware, exempt_paths={"/admin/logout"})
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="pfa_session")
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(admin_users_router)
app.include_router(config_router)
app.include_router(logs_router)
app.include_router(backup_router)


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/admin/dashboard", status_code=302)


@app.on_event("startup")
async def on_startup() -> None:
    """应用启动时初始化数据库、默认数据和备份调度器。"""
    await init_db()
    await ensure_default_roles()
    await ensure_default_admin()
    start_scheduler()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """应用停止时关闭备份调度器与数据库连接。"""
    stop_scheduler()
    await close_db()
