"""自动备份调度器（基于 asyncio 后台任务）。"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_scheduler_task: asyncio.Task | None = None


async def _scheduler_loop() -> None:
    """调度循环：按配置间隔执行备份。"""
    from app.services import backup_service

    while True:
        try:
            config = await backup_service.get_backup_config()
            if not config.get("enabled"):
                # 未启用自动备份，每 60 秒检查一次配置变更
                await asyncio.sleep(60)
                continue

            interval_hours = max(config.get("interval_hours", 24), 1)
            interval_seconds = interval_hours * 3600

            logger.info("自动备份调度：等待 %d 小时后执行", interval_hours)
            await asyncio.sleep(interval_seconds)

            # 再次检查是否仍启用
            config = await backup_service.get_backup_config()
            if not config.get("enabled"):
                continue

            logger.info("自动备份调度：开始执行备份")
            await backup_service.run_backup()

        except asyncio.CancelledError:
            logger.info("自动备份调度器已停止")
            break
        except Exception as exc:
            logger.error("自动备份调度器异常: %s", exc)
            # 出错后等 5 分钟再重试
            await asyncio.sleep(300)


def start_scheduler() -> None:
    """启动自动备份调度器。"""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("自动备份调度器已启动")


def stop_scheduler() -> None:
    """停止自动备份调度器。"""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        _scheduler_task.cancel()
        logger.info("自动备份调度器已请求停止")
    _scheduler_task = None


def restart_scheduler() -> None:
    """重启调度器（配置变更后调用）。"""
    stop_scheduler()
    start_scheduler()
