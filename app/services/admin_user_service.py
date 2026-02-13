"""管理员服务层。"""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId

from app.models import AdminUser
from app.models.admin_user import utc_now


async def list_admins(query: str | None = None) -> list[AdminUser]:
    if query:
        regex = {"$regex": query, "$options": "i"}
        return (
            await AdminUser.find(
                {"$or": [{"username": regex}, {"display_name": regex}, {"email": regex}]}
            )
            .sort("-updated_at")
            .to_list()
        )
    return await AdminUser.find_all().sort("-updated_at").to_list()


async def get_admin(item_id: PydanticObjectId) -> AdminUser | None:
    return await AdminUser.get(item_id)


async def get_admin_by_username(username: str) -> AdminUser | None:
    return await AdminUser.find_one(AdminUser.username == username)


async def create_admin(payload: dict[str, Any]) -> AdminUser:
    admin = AdminUser(
        username=payload["username"],
        display_name=payload["display_name"],
        email=payload.get("email", ""),
        role_slug=payload.get("role_slug", "super"),
        status=payload.get("status", "enabled"),
        password_hash=payload["password_hash"],
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    await admin.insert()
    return admin


async def update_admin(admin: AdminUser, payload: dict[str, Any]) -> AdminUser:
    admin.display_name = payload["display_name"]
    admin.email = payload.get("email", "")
    admin.role_slug = payload.get("role_slug", admin.role_slug)
    admin.status = payload.get("status", admin.status)
    if payload.get("password_hash"):
        admin.password_hash = payload["password_hash"]
    admin.updated_at = utc_now()
    await admin.save()
    return admin


async def delete_admin(admin: AdminUser) -> None:
    await admin.delete()
