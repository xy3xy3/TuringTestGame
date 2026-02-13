"""系统配置服务层。"""

from __future__ import annotations

from collections.abc import Iterable

from app.models import ConfigItem
from app.models.config_item import utc_now

SMTP_DEFAULTS = {
    "smtp_host": "smtp.example.com",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_pass": "",
    "smtp_from": "no-reply@example.com",
    "smtp_ssl": "false",
}

SMTP_META = {
    "smtp_host": "SMTP 主机",
    "smtp_port": "SMTP 端口",
    "smtp_user": "SMTP 用户名",
    "smtp_pass": "SMTP 密码",
    "smtp_from": "发件人地址",
    "smtp_ssl": "启用 SSL (true/false)",
}

AUDIT_ACTION_ORDER = ["create", "read", "update", "delete"]
AUDIT_ACTION_LABELS = {
    "create": "新增",
    "read": "查询",
    "update": "修改",
    "delete": "删除",
}
AUDIT_DEFAULT_ACTIONS = ["create", "update", "delete"]
AUDIT_CONFIG_KEY = "audit_log_actions"


async def find_config_item(group: str, key: str) -> ConfigItem | None:
    return await ConfigItem.find_one({"group": group, "key": key})


def normalize_audit_actions(actions: Iterable[str]) -> list[str]:
    selected = {str(item).strip().lower() for item in actions if str(item).strip()}
    return [item for item in AUDIT_ACTION_ORDER if item in selected]


async def get_smtp_config() -> dict[str, str]:
    items = await ConfigItem.find(ConfigItem.group == "smtp").to_list()
    mapping = {item.key: item.value for item in items}
    merged = SMTP_DEFAULTS | mapping
    return merged


async def save_smtp_config(payload: dict[str, str]) -> None:
    for key, name in SMTP_META.items():
        value = payload.get(key, "").strip()
        item = await find_config_item("smtp", key)
        if item:
            item.value = value
            item.name = name
            item.updated_at = utc_now()
            await item.save()
        else:
            await ConfigItem(
                key=key,
                name=name,
                value=value,
                group="smtp",
                description="SMTP 配置",
                updated_at=utc_now(),
            ).insert()


async def get_audit_log_actions() -> list[str]:
    item = await find_config_item("audit", AUDIT_CONFIG_KEY)
    if not item:
        return AUDIT_DEFAULT_ACTIONS.copy()
    if not item.value.strip():
        return []
    return normalize_audit_actions(item.value.split(","))


async def save_audit_log_actions(actions: list[str]) -> list[str]:
    normalized = normalize_audit_actions(actions)
    value = ",".join(normalized)
    item = await find_config_item("audit", AUDIT_CONFIG_KEY)
    if item:
        item.value = value
        item.name = "操作日志记录类型"
        item.description = "用于控制日志系统记录的操作类型"
        item.updated_at = utc_now()
        await item.save()
    else:
        await ConfigItem(
            key=AUDIT_CONFIG_KEY,
            name="操作日志记录类型",
            value=value,
            group="audit",
            description="用于控制日志系统记录的操作类型",
            updated_at=utc_now(),
        ).insert()
    return normalized
