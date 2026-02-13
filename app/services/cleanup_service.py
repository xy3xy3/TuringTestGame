"""游戏数据垃圾清理服务 - 定期清理已结束的游戏数据。"""

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

# 配置键
CLEANUP_ENABLED_KEY = "cleanup_enabled"
CLEANUP_RETENTION_DAYS_KEY = "cleanup_retention_days"
CLEANUP_INTERVAL_HOURS_KEY = "cleanup_interval_hours"


async def get_cleanup_config() -> dict[str, Any]:
    """获取清理配置。"""
    enabled_item = await ConfigItem.find_one({"group": "cleanup", "key": CLEANUP_ENABLED_KEY})
    retention_item = await ConfigItem.find_one({"group": "cleanup", "key": CLEANUP_RETENTION_DAYS_KEY})
    interval_item = await ConfigItem.find_one({"group": "cleanup", "key": CLEANUP_INTERVAL_HOURS_KEY})

    return {
        "enabled": enabled_item.value.lower() == "true" if enabled_item else True,
        "retention_days": int(retention_item.value) if retention_item else DEFAULT_RETENTION_DAYS,
        "interval_hours": int(interval_item.value) if interval_item else DEFAULT_CLEANUP_INTERVAL_HOURS,
    }


async def save_cleanup_config(
    enabled: bool = True,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    interval_hours: int = DEFAULT_CLEANUP_INTERVAL_HOURS,
) -> dict[str, Any]:
    """保存清理配置。"""
    configs = [
        (CLEANUP_ENABLED_KEY, str(enabled).lower(), "是否启用自动清理"),
        (CLEANUP_RETENTION_DAYS_KEY, str(retention_days), "数据保留天数"),
        (CLEANUP_INTERVAL_HOURS_KEY, str(interval_hours), "清理间隔小时数"),
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


async def cleanup_finished_games() -> dict[str, int]:
    """清理已结束的游戏数据。

    Returns:
        清理统计信息，包含删除的各类记录数量
    """
    config = await get_cleanup_config()
    retention_days = config.get("retention_days", DEFAULT_RETENTION_DAYS)

    # 计算截止日期
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

    logger.info("开始清理数据：保留 %d 天内的数据，截止日期: %s", retention_days, cutoff_date.isoformat())

    stats = {
        "rooms": 0,
        "players": 0,
        "rounds": 0,
        "votes": 0,
    }

    try:
        # 1. 查找已结束且超过保留期的房间
        finished_rooms = await GameRoom.find({
            "phase": "finished",
            "finished_at": {"$lt": cutoff_date},
        }).to_list()

        room_ids = [room.room_id for room in finished_rooms]
        logger.info("找到 %d 个需要清理的已结束房间", len(room_ids))

        if not room_ids:
            return stats

        # 2. 删除这些房间关联的投票记录
        vote_result = await VoteRecord.find({
            "room_id": {"$in": room_ids},
        }).delete_many()
        stats["votes"] = vote_result.deleted_count if hasattr(vote_result, 'deleted_count') else 0
        logger.info("删除投票记录: %d 条", stats["votes"])

        # 3. 删除这些房间关联的回合记录
        round_result = await GameRound.find({
            "room_id": {"$in": room_ids},
        }).delete_many()
        stats["rounds"] = round_result.deleted_count if hasattr(round_result, 'deleted_count') else 0
        logger.info("删除回合记录: %d 条", stats["rounds"])

        # 4. 删除这些房间关联的玩家记录
        player_result = await GamePlayer.find({
            "room_id": {"$in": room_ids},
        }).delete_many()
        stats["players"] = player_result.deleted_count if hasattr(player_result, 'deleted_count') else 0
        logger.info("删除玩家记录: %d 条", stats["players"])

        # 5. 最后删除房间记录
        room_result = await GameRoom.find({
            "_id": {"$in": [room.id for room in finished_rooms]},
        }).delete_many()
        stats["rooms"] = room_result.deleted_count if hasattr(room_result, 'deleted_count') else 0
        logger.info("删除房间记录: %d 条", stats["rooms"])

        logger.info(
            "清理完成：房间=%d, 玩家=%d, 回合=%d, 投票=%d",
            stats["rooms"], stats["players"], stats["rounds"], stats["votes"],
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
                "垃圾清理完成：清理房间 %d 个，玩家 %d 个，回合 %d 个，投票 %d 条",
                stats["rooms"], stats["players"], stats["rounds"], stats["votes"],
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
