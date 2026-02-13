"""认证服务层。"""

from __future__ import annotations

from beanie import PydanticObjectId
from passlib.context import CryptContext

from app.config import ADMIN_PASS, ADMIN_USER
from app.models import AdminUser
from app.models.admin_user import utc_now
from app.services.admin_user_service import create_admin, get_admin_by_username

_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(raw: str) -> str:
    return _pwd_context.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return _pwd_context.verify(raw, hashed)


async def authenticate(username: str, password: str) -> AdminUser | None:
    admin = await get_admin_by_username(username)
    if not admin or admin.status != "enabled":
        return None
    if not verify_password(password, admin.password_hash):
        return None
    admin.last_login = utc_now()
    admin.updated_at = utc_now()
    await admin.save()
    return admin


async def ensure_default_admin() -> AdminUser:
    """确保至少存在一个管理员账号。"""
    admin = await get_admin_by_username(ADMIN_USER)
    if admin:
        return admin

    return await create_admin(
        {
            "username": ADMIN_USER,
            "display_name": "超级管理员",
            "email": "",
            "role_slug": "super",
            "status": "enabled",
            "password_hash": hash_password(ADMIN_PASS),
        }
    )


async def get_admin_by_id(admin_id: str | None) -> AdminUser | None:
    if not admin_id:
        return None
    try:
        return await AdminUser.get(PydanticObjectId(admin_id))
    except Exception:
        return None


async def change_password(admin: AdminUser, old_password: str, new_password: str) -> bool:
    if not verify_password(old_password, admin.password_hash):
        return False
    admin.password_hash = hash_password(new_password)
    admin.updated_at = utc_now()
    await admin.save()
    return True
