"""登录、个人资料、修改密码控制器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.services import admin_user_service, auth_service, csrf_service, log_service, permission_decorator, validators

BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(prefix="/admin")

DEFAULT_NEXT_PATH = "/admin/dashboard"


def base_context(request: Request) -> dict[str, Any]:
    return {
        "request": request,
        "current_admin": request.session.get("admin_name"),
    }


def sanitize_next_path(next_url: str | None) -> str:
    """清洗登录跳转地址，避免开放重定向。"""

    raw_value = (next_url or "").strip()
    if not raw_value:
        return DEFAULT_NEXT_PATH

    parsed = urlsplit(raw_value)
    if parsed.scheme or parsed.netloc:
        return DEFAULT_NEXT_PATH
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return DEFAULT_NEXT_PATH
    if not parsed.path.startswith("/admin"):
        return DEFAULT_NEXT_PATH

    if parsed.query:
        return f"{parsed.path}?{parsed.query}"
    return parsed.path


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None) -> HTMLResponse:
    context = {
        "request": request,
        "next": sanitize_next_path(next),
        "error": "",
    }
    return templates.TemplateResponse("pages/login.html", context)


@router.post("/login", response_class=HTMLResponse)
async def login_action(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form(DEFAULT_NEXT_PATH),
) -> HTMLResponse:
    safe_next = sanitize_next_path(next)
    normalized_username = username.strip()
    admin = await auth_service.authenticate(normalized_username, password)
    if not admin:
        await log_service.record_action(
            action="read",
            module="auth",
            operator=normalized_username or "anonymous",
            target="管理员登录",
            detail="登录失败：账号或密码错误，或账号已被禁用",
            method=request.method,
            path=request.url.path,
            ip=log_service.get_request_ip(request),
        )
        context = {
            "request": request,
            "next": safe_next,
            "error": "账号或密码不正确，或账号已被禁用。",
        }
        return templates.TemplateResponse("pages/login.html", context, status_code=401)

    request.session["admin_id"] = str(admin.id)
    request.session["admin_name"] = admin.display_name
    request.state.csrf_token = csrf_service.rotate_csrf_token(request.session)
    await log_service.record_request(
        request,
        action="read",
        module="auth",
        target="管理员登录",
        target_id=str(admin.id),
        detail=f"管理员账号 {admin.username} 登录成功",
    )
    return RedirectResponse(url=safe_next, status_code=302)


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    await log_service.record_request(
        request,
        action="read",
        module="auth",
        target="管理员退出",
        detail="管理员主动退出登录",
    )
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=302)


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request) -> HTMLResponse:
    admin_id = request.session.get("admin_id")
    admin = await auth_service.get_admin_by_id(admin_id)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)

    context = {
        **base_context(request),
        "admin": admin,
        "error": "",
        "saved": False,
    }
    await log_service.record_request(
        request,
        action="read",
        module="auth",
        target="个人资料",
        target_id=str(admin.id),
        detail="访问个人资料页面",
    )
    return templates.TemplateResponse("pages/profile.html", context)


@router.post("/profile", response_class=HTMLResponse)
@permission_decorator.permission_meta("profile", "update_self")
async def profile_update(
    request: Request,
    display_name: str = Form(""),
    email: str = Form(""),
) -> HTMLResponse:
    admin_id = request.session.get("admin_id")
    admin = await auth_service.get_admin_by_id(admin_id)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)

    normalized_email = validators.normalize_email(email)
    email_error = validators.validate_optional_email(normalized_email)
    display_name_value = display_name.strip() or admin.display_name
    if email_error:
        await log_service.record_request(
            request,
            action="update",
            module="auth",
            target="个人资料",
            target_id=str(admin.id),
            detail=f"更新个人资料失败：{email_error}",
        )
        context = {
            **base_context(request),
            "admin": admin,
            "error": email_error,
            "saved": False,
        }
        return templates.TemplateResponse("pages/profile.html", context, status_code=422)

    payload = {
        "display_name": display_name_value,
        "email": normalized_email,
    }
    await admin_user_service.update_admin(admin, payload)
    request.session["admin_name"] = display_name_value
    await log_service.record_request(
        request,
        action="update",
        module="auth",
        target="个人资料",
        target_id=str(admin.id),
        detail="更新个人资料信息",
    )

    context = {
        **base_context(request),
        "admin": admin,
        "error": "",
        "saved": True,
    }
    return templates.TemplateResponse("pages/profile.html", context)


@router.get("/password", response_class=HTMLResponse)
async def password_page(request: Request) -> HTMLResponse:
    admin_id = request.session.get("admin_id")
    admin = await auth_service.get_admin_by_id(admin_id)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)

    context = {
        **base_context(request),
        "error": "",
        "saved": False,
    }
    await log_service.record_request(
        request,
        action="read",
        module="auth",
        target="修改密码",
        target_id=str(admin.id),
        detail="访问修改密码页面",
    )
    return templates.TemplateResponse("pages/password.html", context)


@router.post("/password", response_class=HTMLResponse)
@permission_decorator.permission_meta("password", "update_self")
async def password_update(
    request: Request,
    old_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
) -> HTMLResponse:
    admin_id = request.session.get("admin_id")
    admin = await auth_service.get_admin_by_id(admin_id)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=302)

    if len(new_password) < 6:
        await log_service.record_request(
            request,
            action="update",
            module="auth",
            target="修改密码",
            target_id=str(admin.id),
            detail="修改密码失败：新密码长度不足",
        )
        context = {**base_context(request), "error": "新密码至少 6 位。", "saved": False}
        return templates.TemplateResponse("pages/password.html", context, status_code=422)

    if new_password != confirm_password:
        await log_service.record_request(
            request,
            action="update",
            module="auth",
            target="修改密码",
            target_id=str(admin.id),
            detail="修改密码失败：两次密码输入不一致",
        )
        context = {**base_context(request), "error": "两次输入的密码不一致。", "saved": False}
        return templates.TemplateResponse("pages/password.html", context, status_code=422)

    ok = await auth_service.change_password(admin, old_password, new_password)
    if not ok:
        await log_service.record_request(
            request,
            action="update",
            module="auth",
            target="修改密码",
            target_id=str(admin.id),
            detail="修改密码失败：旧密码校验不通过",
        )
        context = {**base_context(request), "error": "旧密码不正确。", "saved": False}
        return templates.TemplateResponse("pages/password.html", context, status_code=422)

    await log_service.record_request(
        request,
        action="update",
        module="auth",
        target="修改密码",
        target_id=str(admin.id),
        detail="修改密码成功",
    )
    context = {**base_context(request), "error": "", "saved": True}
    return templates.TemplateResponse("pages/password.html", context)
