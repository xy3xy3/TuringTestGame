"""游戏数据垃圾清理服务 - 定期清理过期房间与关联数据。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.models.game_room import GameRoom
from app.models.game_player import GamePlayer
from app.models.game_round import GameRound
from app.models.vote_record import VoteRecord
from app.models import ConfigItem

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_RETENTION_DAYS = 7  # 默认保留7天
DEFAULT_CLEANUP_INTERVAL_HOURS = 24  # 默认每天清理一次
DEFAULT_WAITING_TIMEOUT_MINUTES = 30  # 等待加入超时，默认30分钟

# 配置键
CLEANUP_ENABLED_KEY = "cleanup_enabled"
CLEANUP_RETENTION_DAYS_KEY = "cleanup_retention_days"
CLEANUP_INTERVAL_HOURS_KEY = "cleanup_interval_hours"
CLEANUP_WAITING_TIMEOUT_MINUTES_KEY = "cleanup_waiting_timeout_minutes"


def _to_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    """把任意输入转换为整数，并裁剪到指定范围。"""
    try:
        parsed = int(str(value).strip())
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _extract_deleted_count(result: object) -> int:
    """兼容不同删除返回值，统一读取删除数量。"""
    if hasattr(result, "deleted_count"):
        return int(getattr(result, "deleted_count", 0) or 0)
    try:
        return int(result)  # 兼容某些驱动直接返回 int 的场景
    except Exception:
        return 0


async def get_cleanup_config() -> dict[str, Any]:
    """获取清理配置。"""
    enabled_item = await ConfigItem.find_one({"group": "cleanup", "key": CLEANUP_ENABLED_KEY})
    retention_item = await ConfigItem.find_one({"group": "cleanup", "key": CLEANUP_RETENTION_DAYS_KEY})
    interval_item = await ConfigItem.find_one({"group": "cleanup", "key": CLEANUP_INTERVAL_HOURS_KEY})
    waiting_timeout_item = await ConfigItem.find_one({"group": "cleanup", "key": CLEANUP_WAITING_TIMEOUT_MINUTES_KEY})

    return {
        "enabled": enabled_item.value.lower() == "true" if enabled_item else True,
        "retention_days": _to_int(
            retention_item.value if retention_item else DEFAULT_RETENTION_DAYS,
            default=DEFAULT_RETENTION_DAYS,
            minimum=1,
            maximum=365,
        ),
        "interval_hours": _to_int(
            interval_item.value if interval_item else DEFAULT_CLEANUP_INTERVAL_HOURS,
            default=DEFAULT_CLEANUP_INTERVAL_HOURS,
            minimum=1,
            maximum=168,
        ),
        "waiting_timeout_minutes": _to_int(
            waiting_timeout_item.value if waiting_timeout_item else DEFAULT_WAITING_TIMEOUT_MINUTES,
            default=DEFAULT_WAITING_TIMEOUT_MINUTES,
            minimum=1,
            maximum=10080,
        ),
    }


async def save_cleanup_config(
    enabled: bool = True,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    interval_hours: int = DEFAULT_CLEANUP_INTERVAL_HOURS,
    waiting_timeout_minutes: int = DEFAULT_WAITING_TIMEOUT_MINUTES,
) -> dict[str, Any]:
    """保存清理配置。"""
    normalized_retention_days = _to_int(
        retention_days,
        default=DEFAULT_RETENTION_DAYS,
        minimum=1,
        maximum=365,
    )
    normalized_interval_hours = _to_int(
        interval_hours,
        default=DEFAULT_CLEANUP_INTERVAL_HOURS,
        minimum=1,
        maximum=168,
    )
    normalized_waiting_timeout_minutes = _to_int(
        waiting_timeout_minutes,
        default=DEFAULT_WAITING_TIMEOUT_MINUTES,
        minimum=1,
        maximum=10080,
    )

    configs = [
        (CLEANUP_ENABLED_KEY, str(enabled).lower(), "是否启用自动清理"),
        (CLEANUP_RETENTION_DAYS_KEY, str(normalized_retention_days), "已结束房间数据保留天数"),
        (CLEANUP_INTERVAL_HOURS_KEY, str(normalized_interval_hours), "清理间隔小时数"),
        (CLEANUP_WAITING_TIMEOUT_MINUTES_KEY, str(normalized_waiting_timeout_minutes), "等待加入超时清理分钟数"),
    ]

    for key, value, description in configs:
        item = await ConfigItem.find_one({"group": "cleanup", "key": key})
        if item:
            item.value = value
            item.updated_at = datetime.now(timezone.utc)
            await item.save()
        else:
            await ConfigItem(
                key=key,
                name=description,
                value=value,
                group="cleanup",
                description=description,
                updated_at=datetime.now(timezone.utc),
            ).insert()

    return await get_cleanup_config()


async def _cleanup_room_batch(rooms: list[GameRoom]) -> dict[str, int]:
    """按房间批量清理房间及关联玩家/回合/投票记录。"""
    if not rooms:
        return {"rooms": 0, "players": 0, "rounds": 0, "votes": 0}

    room_ids = [room.room_id for room in rooms]
    room_object_ids = [room.id for room in rooms]

    vote_result = await VoteRecord.find({"room_id": {"$in": room_ids}}).delete_many()
    round_result = await GameRound.find({"room_id": {"$in": room_ids}}).delete_many()
    player_result = await GamePlayer.find({"room_id": {"$in": room_ids}}).delete_many()
    room_result = await GameRoom.find({"_id": {"$in": room_object_ids}}).delete_many()

    return {
        "votes": _extract_deleted_count(vote_result),
        "rounds": _extract_deleted_count(round_result),
        "players": _extract_deleted_count(player_result),
        "rooms": _extract_deleted_count(room_result),
    }


async def cleanup_finished_games() -> dict[str, int]:
    """清理过期游戏数据（已结束超保留期 + 等待加入超时房间）。

    Returns:
        清理统计信息，包含删除的各类记录数量
    """
    config = await get_cleanup_config()
    retention_days = config.get("retention_days", DEFAULT_RETENTION_DAYS)
    waiting_timeout_minutes = config.get("waiting_timeout_minutes", DEFAULT_WAITING_TIMEOUT_MINUTES)

    now = datetime.now(timezone.utc)
    finished_cutoff_date = now - timedelta(days=retention_days)
    waiting_cutoff_date = now - timedelta(minutes=waiting_timeout_minutes)

    logger.info(
        "开始清理数据：已结束保留=%d天(截止%s)，等待加入超时=%d分钟(截止%s)",
        retention_days,
        finished_cutoff_date.isoformat(),
        waiting_timeout_minutes,
        waiting_cutoff_date.isoformat(),
    )

    stats = {
        "rooms": 0,
        "players": 0,
        "rounds": 0,
        "votes": 0,
        "expired_waiting_rooms": 0,
        "expired_waiting_players": 0,
    }

    try:
        # 1) 清理已结束且超过保留期的房间
        finished_rooms = await GameRoom.find({
            "phase": "finished",
            "finished_at": {"$lt": finished_cutoff_date},
        }).to_list()
        logger.info("找到 %d 个需要清理的已结束房间", len(finished_rooms))
        finished_batch_stats = await _cleanup_room_batch(finished_rooms)
        for key in ("rooms", "players", "rounds", "votes"):
            stats[key] += finished_batch_stats[key]

        # 2) 清理等待加入超时房间，防止恶意堆积
        expired_waiting_rooms = await GameRoom.find({
            "phase": "waiting",
            "created_at": {"$lt": waiting_cutoff_date},
        }).to_list()
        logger.info("找到 %d 个等待加入超时房间", len(expired_waiting_rooms))
        waiting_batch_stats = await _cleanup_room_batch(expired_waiting_rooms)
        for key in ("rooms", "players", "rounds", "votes"):
            stats[key] += waiting_batch_stats[key]
        stats["expired_waiting_rooms"] = waiting_batch_stats["rooms"]
        stats["expired_waiting_players"] = waiting_batch_stats["players"]

        logger.info(
            "清理完成：房间=%d(等待超时=%d), 玩家=%d(等待超时=%d), 回合=%d, 投票=%d",
            stats["rooms"],
            stats["expired_waiting_rooms"],
            stats["players"],
            stats["expired_waiting_players"],
            stats["rounds"],
            stats["votes"],
        )

    except Exception as exc:
        logger.error("清理数据时发生错误: %s", exc, exc_info=True)
        raise

    return stats


# 调度器任务引用
_cleanup_task: asyncio.Task | None = None


async def _cleanup_scheduler_loop() -> None:
    """清理调度循环。"""
    while True:
        try:
            config = await get_cleanup_config()
            if not config.get("enabled", True):
                # 未启用自动清理，每60秒检查一次配置变更
                await asyncio.sleep(60)
                continue

            interval_hours = max(config.get("interval_hours", DEFAULT_CLEANUP_INTERVAL_HOURS), 1)
            interval_seconds = interval_hours * 3600

            logger.info("垃圾清理调度：等待 %d 小时后执行清理", interval_hours)
            await asyncio.sleep(interval_seconds)

            # 再次检查是否仍启用
            config = await get_cleanup_config()
            if not config.get("enabled", True):
                continue

            logger.info("垃圾清理调度：开始执行清理任务")
            stats = await cleanup_finished_games()
            logger.info(
                "垃圾清理完成：房间 %d 个(等待超时 %d)，玩家 %d 个(等待超时 %d)，回合 %d 个，投票 %d 条",
                stats["rooms"],
                stats["expired_waiting_rooms"],
                stats["players"],
                stats["expired_waiting_players"],
                stats["rounds"],
                stats["votes"],
            )

        except asyncio.CancelledError:
            logger.info("垃圾清理调度器已停止")
            break
        except Exception as exc:
            logger.error("垃圾清理调度器异常: %s", exc, exc_info=True)
            # 出错后等30分钟再重试
            await asyncio.sleep(1800)


def start_cleanup_scheduler() -> None:
    """启动垃圾清理调度器。"""
    global _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        return
    _cleanup_task = asyncio.create_task(_cleanup_scheduler_loop())
    logger.info("垃圾清理调度器已启动")


def stop_cleanup_scheduler() -> None:
    """停止垃圾清理调度器。"""
    global _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        _cleanup_task.cancel()
        logger.info("垃圾清理调度器已请求停止")
    _cleanup_task = None


def restart_cleanup_scheduler() -> None:
    """重启清理调度器（配置变更后调用）。"""
    stop_cleanup_scheduler()
    start_cleanup_scheduler()
