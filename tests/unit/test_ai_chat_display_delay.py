from __future__ import annotations

import pytest

from app.services import ai_chat_service


@pytest.mark.unit
@pytest.mark.asyncio
async def test_calculate_display_delay_uses_uniform_random_in_prod(monkeypatch) -> None:
    """生产环境应使用统一随机输入中时长，且与回答类型无关。"""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("TEST_AI_DISPLAY_DELAY", raising=False)

    monkeypatch.setattr(ai_chat_service.random, "uniform", lambda _a, _b: 4.25)

    delay_ai = await ai_chat_service.calculate_display_delay("ai", 1.0)
    delay_human = await ai_chat_service.calculate_display_delay("human", 0.2)

    assert delay_ai == 4.25
    assert delay_human == 4.25


@pytest.mark.unit
@pytest.mark.asyncio
async def test_calculate_display_delay_can_be_overridden_in_test_env(monkeypatch) -> None:
    """测试环境可通过环境变量固定输入中时长。"""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TEST_AI_DISPLAY_DELAY", "1.5")

    delay = await ai_chat_service.calculate_display_delay("ai", 0.0)
    assert delay == 1.5
