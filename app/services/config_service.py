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


# 站点底部版权配置（用于前后端页脚展示）
FOOTER_COPYRIGHT_TEXT_KEY = "footer_copyright_text"
FOOTER_COPYRIGHT_URL_KEY = "footer_copyright_url"
FOOTER_COPYRIGHT_TEXT_DEFAULT = "TuringTestGame"
FOOTER_COPYRIGHT_URL_DEFAULT = "https://github.com/xy3xy3/TuringTestGame"


def _normalize_footer_copyright_text(text: str) -> str:
    """规范化底部版权文案，空值时回退默认文案。"""
    value = str(text or "").strip()
    return value or FOOTER_COPYRIGHT_TEXT_DEFAULT


def _normalize_footer_copyright_url(url: str) -> str:
    """规范化底部版权链接，空值时回退默认仓库地址。"""
    value = str(url or "").strip()
    return value or FOOTER_COPYRIGHT_URL_DEFAULT


async def get_footer_copyright() -> dict[str, str]:
    """获取底部版权配置。"""
    text_item = await find_config_item("system", FOOTER_COPYRIGHT_TEXT_KEY)
    url_item = await find_config_item("system", FOOTER_COPYRIGHT_URL_KEY)
    text = _normalize_footer_copyright_text(text_item.value if text_item else "")
    url = _normalize_footer_copyright_url(url_item.value if url_item else "")
    return {
        "text": text,
        "url": url,
    }


async def save_footer_copyright(text: str, url: str) -> dict[str, str]:
    """保存底部版权配置。"""
    normalized_text = _normalize_footer_copyright_text(text)
    normalized_url = _normalize_footer_copyright_url(url)

    text_item = await find_config_item("system", FOOTER_COPYRIGHT_TEXT_KEY)
    if text_item:
        text_item.value = normalized_text
        text_item.name = "底部版权文案"
        text_item.description = "用于页面底部展示的版权文案"
        text_item.updated_at = utc_now()
        await text_item.save()
    else:
        await ConfigItem(
            key=FOOTER_COPYRIGHT_TEXT_KEY,
            name="底部版权文案",
            value=normalized_text,
            group="system",
            description="用于页面底部展示的版权文案",
            updated_at=utc_now(),
        ).insert()

    url_item = await find_config_item("system", FOOTER_COPYRIGHT_URL_KEY)
    if url_item:
        url_item.value = normalized_url
        url_item.name = "底部版权链接"
        url_item.description = "用于页面底部展示的版权链接"
        url_item.updated_at = utc_now()
        await url_item.save()
    else:
        await ConfigItem(
            key=FOOTER_COPYRIGHT_URL_KEY,
            name="底部版权链接",
            value=normalized_url,
            group="system",
            description="用于页面底部展示的版权链接",
            updated_at=utc_now(),
        ).insert()

    return {
        "text": normalized_text,
        "url": normalized_url,
    }


# 游戏 IP 限流配置
RATE_LIMIT_CONFIG_GROUP = "rate_limit"
RATE_LIMIT_DEFAULT_CONFIG: dict[str, int | bool] = {
    "enabled": False,
    "trust_proxy_headers": False,
    "window_seconds": 60,
    "max_requests": 120,
    "create_room_max_requests": 20,
    "join_room_max_requests": 40,
    "chat_api_max_requests": 30,
}
RATE_LIMIT_INT_RANGES: dict[str, tuple[int, int]] = {
    "window_seconds": (1, 3600),
    "max_requests": (1, 100000),
    "create_room_max_requests": (1, 100000),
    "join_room_max_requests": (1, 100000),
    "chat_api_max_requests": (1, 100000),
}
RATE_LIMIT_META: dict[str, tuple[str, str]] = {
    "enabled": ("启用 IP 限流", "用于防止恶意高频请求（CC）"),
    "trust_proxy_headers": ("信任代理 IP 头", "启用后会使用 X-Forwarded-For / X-Real-IP 识别真实来源 IP"),
    "window_seconds": ("限流窗口（秒）", "固定窗口长度，单位秒"),
    "max_requests": ("通用写接口上限", "除创建/加入/测试对话外，其他游戏写接口的窗口请求上限"),
    "create_room_max_requests": ("创建房间上限", "创建房间接口在限流窗口内的请求上限"),
    "join_room_max_requests": ("加入房间上限", "加入房间接口在限流窗口内的请求上限"),
    "chat_api_max_requests": ("测试对话上限", "灵魂注入测试对话接口在限流窗口内的请求上限"),
}


def _to_bool(value: object, *, default: bool) -> bool:
    """将任意输入转换为布尔值。"""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "on", "yes", "y"}:
        return True
    if text in {"0", "false", "off", "no", "n"}:
        return False
    return default


def _to_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    """将任意输入转换为整数并裁剪到区间内。"""
    try:
        parsed = int(str(value).strip())
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _normalize_rate_limit_config(payload: dict[str, object]) -> dict[str, int | bool]:
    """规范化限流配置。"""
    normalized: dict[str, int | bool] = {
        "enabled": _to_bool(payload.get("enabled"), default=bool(RATE_LIMIT_DEFAULT_CONFIG["enabled"])),
        "trust_proxy_headers": _to_bool(
            payload.get("trust_proxy_headers"),
            default=bool(RATE_LIMIT_DEFAULT_CONFIG["trust_proxy_headers"]),
        ),
    }
    for key, (minimum, maximum) in RATE_LIMIT_INT_RANGES.items():
        default = int(RATE_LIMIT_DEFAULT_CONFIG[key])
        normalized[key] = _to_int(payload.get(key), default=default, minimum=minimum, maximum=maximum)
    return normalized


async def get_rate_limit_config() -> dict[str, int | bool]:
    """读取游戏 IP 限流配置。"""
    payload: dict[str, object] = {}
    for key in RATE_LIMIT_DEFAULT_CONFIG:
        item = await find_config_item(RATE_LIMIT_CONFIG_GROUP, key)
        if item:
            payload[key] = item.value
    return _normalize_rate_limit_config(payload)


async def save_rate_limit_config(payload: dict[str, object]) -> dict[str, int | bool]:
    """保存游戏 IP 限流配置。"""
    normalized = _normalize_rate_limit_config(payload)
    for key, value in normalized.items():
        name, description = RATE_LIMIT_META[key]
        stored = "true" if isinstance(value, bool) else str(value)
        item = await find_config_item(RATE_LIMIT_CONFIG_GROUP, key)
        if item:
            item.value = stored
            item.name = name
            item.description = description
            item.updated_at = utc_now()
            await item.save()
        else:
            await ConfigItem(
                key=key,
                name=name,
                value=stored,
                group=RATE_LIMIT_CONFIG_GROUP,
                description=description,
                updated_at=utc_now(),
            ).insert()
    return normalized


# 游戏时间配置（各阶段时长）
GAME_TIME_CONFIG_KEYS = {
    "setup_duration": ("灵魂注入时长", 60, 15, 300),
    "question_duration": ("提问阶段时长", 30, 15, 300),
    "answer_duration": ("回答阶段时长", 45, 15, 300),
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


# 游戏角色伪随机保底配置（提问者/被测者选角）
GAME_ROLE_BALANCE_CONFIG_GROUP = "game_role_balance"
GAME_ROLE_BALANCE_CONFIG_KEYS = {
    "pity_gap_threshold": ("硬保底触发差值", 2, 1, 10),
    "weight_base": ("权重基础值", 100, 1, 10000),
    "weight_deficit_step": ("次数差权重增量", 40, 0, 10000),
    "weight_zero_bonus": ("零次数额外权重", 60, 0, 10000),
}


def _normalize_game_role_balance_config(payload: dict[str, object]) -> dict[str, int]:
    """规范化游戏角色伪随机保底配置。"""
    normalized: dict[str, int] = {}
    for key, (_name, default, minimum, maximum) in GAME_ROLE_BALANCE_CONFIG_KEYS.items():
        normalized[key] = _to_int(payload.get(key), default=default, minimum=minimum, maximum=maximum)
    return normalized


async def get_game_role_balance_config() -> dict[str, int]:
    """获取游戏角色伪随机保底配置。"""
    payload: dict[str, object] = {}
    for key in GAME_ROLE_BALANCE_CONFIG_KEYS:
        item = await find_config_item(GAME_ROLE_BALANCE_CONFIG_GROUP, key)
        if item:
            payload[key] = item.value
    return _normalize_game_role_balance_config(payload)


async def save_game_role_balance_config(payload: dict[str, object]) -> dict[str, int]:
    """保存游戏角色伪随机保底配置。"""
    normalized = _normalize_game_role_balance_config(payload)
    for key, value in normalized.items():
        name, _default, minimum, maximum = GAME_ROLE_BALANCE_CONFIG_KEYS[key]
        item = await find_config_item(GAME_ROLE_BALANCE_CONFIG_GROUP, key)
        if item:
            item.value = str(value)
            item.name = name
            item.description = f"游戏选角保底配置：{name}（范围 {minimum}-{maximum}）"
            item.updated_at = utc_now()
            await item.save()
        else:
            await ConfigItem(
                key=key,
                name=name,
                value=str(value),
                group=GAME_ROLE_BALANCE_CONFIG_GROUP,
                description=f"游戏选角保底配置：{name}（范围 {minimum}-{maximum}）",
                updated_at=utc_now(),
            ).insert()
    return normalized
