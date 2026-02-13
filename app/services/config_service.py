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


# Base URL 配置（用于生成邀请链接）
BASE_URL_KEY = "base_url"
BASE_URL_DEFAULT = "http://127.0.0.1:8000"


async def get_base_url() -> str:
    """获取系统 base URL，用于生成邀请链接。"""
    item = await find_config_item("system", BASE_URL_KEY)
    if item and item.value.strip():
        return item.value.strip().rstrip("/")
    return BASE_URL_DEFAULT


async def save_base_url(url: str) -> str:
    """保存系统 base URL。"""
    value = url.strip().rstrip("/") if url else BASE_URL_DEFAULT
    item = await find_config_item("system", BASE_URL_KEY)
    if item:
        item.value = value
        item.name = "系统访问地址"
        item.description = "用于生成邀请链接等外部访问地址"
        item.updated_at = utc_now()
        await item.save()
    else:
        await ConfigItem(
            key=BASE_URL_KEY,
            name="系统访问地址",
            value=value,
            group="system",
            description="用于生成邀请链接等外部访问地址",
            updated_at=utc_now(),
        ).insert()
    return value


# 游戏时间配置（各阶段时长）
GAME_TIME_CONFIG_KEYS = {
    "setup_duration": ("灵魂注入时长", 60, 30, 300),
    "question_duration": ("提问阶段时长", 30, 15, 60),
    "answer_duration": ("回答阶段时长", 45, 20, 90),
    "vote_duration": ("投票阶段时长", 15, 10, 30),
    "reveal_delay": ("结果揭晓延迟", 3, 1, 10),
}

GAME_TIME_CONFIG_GROUP = "game_time"


async def get_game_time_config() -> dict[str, int]:
    """获取游戏各阶段时间配置（秒）。"""
    config = {}
    for key, (name, default, _, _) in GAME_TIME_CONFIG_KEYS.items():
        item = await find_config_item(GAME_TIME_CONFIG_GROUP, key)
        if item and item.value.isdigit():
            config[key] = int(item.value)
        else:
            config[key] = default
    return config


async def save_game_time_config(config: dict[str, int]) -> dict[str, int]:
    """保存游戏各阶段时间配置。"""
    for key, (name, default, min_val, max_val) in GAME_TIME_CONFIG_KEYS.items():
        value = config.get(key, default)
        # 确保值在有效范围内
        value = max(min_val, min(max_val, int(value)))

        item = await find_config_item(GAME_TIME_CONFIG_GROUP, key)
        if item:
            item.value = str(value)
            item.name = name
            item.description = f"游戏配置：{name}（秒）"
            item.updated_at = utc_now()
            await item.save()
        else:
            await ConfigItem(
                key=key,
                name=name,
                value=str(value),
                group=GAME_TIME_CONFIG_GROUP,
                description=f"游戏配置：{name}（秒），范围 {min_val}-{max_val}",
                updated_at=utc_now(),
            ).insert()

    return await get_game_time_config()
