"""统一字段校验工具。"""

from __future__ import annotations

import re

ROLE_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
ADMIN_USERNAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{2,31}$")
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def normalize_role_slug(value: str) -> str:
    """标准化角色 slug，统一去空白并转为小写。"""

    return str(value or "").strip().lower()


def is_valid_role_slug(value: str) -> bool:
    """判断角色 slug 是否符合格式约束。"""

    return bool(ROLE_SLUG_PATTERN.fullmatch(normalize_role_slug(value)))


def validate_role_slug(value: str) -> str:
    """校验角色 slug，不合法时返回错误信息。"""

    if is_valid_role_slug(value):
        return ""
    return "角色标识仅支持小写字母、数字、下划线，且必须以字母开头"


def normalize_admin_username(value: str) -> str:
    """标准化管理员账号（去除首尾空白）。"""

    return str(value or "").strip()


def validate_admin_username(value: str) -> str:
    """校验管理员账号格式，不合法时返回错误信息。"""

    username = normalize_admin_username(value)
    if ADMIN_USERNAME_PATTERN.fullmatch(username):
        return ""
    return "账号需以字母开头，可包含字母/数字/下划线，长度 3-32"


def normalize_email(value: str) -> str:
    """标准化邮箱值，统一去空白。"""

    return str(value or "").strip()


def validate_optional_email(value: str) -> str:
    """校验可选邮箱字段，空值允许通过。"""

    email = normalize_email(value)
    if not email:
        return ""
    if EMAIL_PATTERN.fullmatch(email):
        return ""
    return "邮箱格式不合法"
