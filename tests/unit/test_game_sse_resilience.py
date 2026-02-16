from __future__ import annotations

import asyncio
import json

import pytest

# 先加载应用入口，避免单独导入控制器时触发模型包循环导入。
import app.main  # noqa: F401
from app.apps.game.controllers import game as game_controller
from app.services.game_manager import SSEManager


class _FakeRequest:
    """用于 SSE 路由单测的最小请求对象。"""

    def __init__(self) -> None:
        self._check_count = 0

    async def is_disconnected(self) -> bool:
        # 第一次检查保持连接，允许心跳发送；第二次后模拟客户端断开。
        self._check_count += 1
        return self._check_count > 1


class _FakeSSEManager:
    """用于替换全局 SSE 管理器，便于断言订阅生命周期。"""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.unsubscribed = False

    def subscribe(self, room_id: str) -> asyncio.Queue[str]:
        return self.queue

    def unsubscribe(self, room_id: str, queue: asyncio.Queue[str]) -> None:
        self.unsubscribed = True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sse_events_emits_retry_and_ping_heartbeat(monkeypatch) -> None:
    fake_manager = _FakeSSEManager()
    monkeypatch.setattr(game_controller, "sse_manager", fake_manager)
    monkeypatch.setattr(game_controller, "SSE_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    response = await game_controller.sse_events(_FakeRequest(), "room-demo")

    first_chunk = await anext(response.body_iterator)
    second_chunk = await anext(response.body_iterator)

    first_text = first_chunk.decode("utf-8") if isinstance(first_chunk, bytes) else str(first_chunk)
    second_text = second_chunk.decode("utf-8") if isinstance(second_chunk, bytes) else str(second_chunk)

    assert "retry: 2000" in first_text
    assert "event: ping" in second_text
    assert "data:" in second_text

    with pytest.raises(StopAsyncIteration):
        await anext(response.body_iterator)

    assert fake_manager.unsubscribed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sse_manager_bounded_queue_drops_oldest_and_cleans_empty_room() -> None:
    manager = SSEManager(queue_maxsize=2)
    queue = manager.subscribe("room-1")

    await manager.publish("room-1", "event-1", {"n": 1})
    await manager.publish("room-1", "event-2", {"n": 2})
    await manager.publish("room-1", "event-3", {"n": 3})

    assert queue.qsize() == 2

    msg1 = json.loads(queue.get_nowait())
    msg2 = json.loads(queue.get_nowait())
    assert msg1["event"] == "event-2"
    assert msg2["event"] == "event-3"

    manager.unsubscribe("room-1", queue)
    assert "room-1" not in manager._connections
