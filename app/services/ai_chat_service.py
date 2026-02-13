"""AI 对话服务 - 用于图灵测试游戏中调用 AI 生成回答。"""

from __future__ import annotations

import random
from typing import Any

from openai import AsyncOpenAI

from app.models.ai_model import AIModel


async def get_enabled_models() -> list[AIModel]:
    """获取所有启用的 AI 模型。"""
    return await AIModel.find({"is_enabled": True}).to_list()


async def get_model_by_id(model_id: str) -> AIModel | None:
    """根据 ID 获取 AI 模型。"""
    from beanie import PydanticObjectId
    try:
        object_id = PydanticObjectId(model_id)
    except Exception:
        return None
    return await AIModel.get(object_id)


async def get_default_model() -> AIModel | None:
    """获取默认 AI 模型。"""
    return await AIModel.find_one({"is_default": True, "is_enabled": True})


async def call_ai(
    system_prompt: str,
    user_message: str,
    model_id: str | None = None,
) -> dict[str, Any]:
    """调用 AI 生成回答。

    Args:
        system_prompt: 系统提示词（玩家的"灵魂"设定）
        user_message: 用户消息（提问者的问题）
        model_id: 可选的模型 ID，如果不提供则使用默认模型

    Returns:
        {"success": True, "content": "AI 的回答"}
        或
        {"success": False, "error": "错误信息"}
    """
    # 获取模型配置
    model_config: AIModel | None = None
    
    if model_id:
        model_config = await get_model_by_id(model_id)
    else:
        model_config = await get_default_model()
    
    if not model_config:
        return {"success": False, "error": "没有可用的 AI 模型"}

    try:
        # 创建异步 OpenAI 客户端
        client = AsyncOpenAI(
            base_url=model_config.base_url,
            api_key=model_config.api_key,
            timeout=30.0,  # 30 秒超时
        )

        # 调用 AI
        response = await client.chat.completions.create(
            model=model_config.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=model_config.temperature,
            max_tokens=model_config.max_tokens,
        )

        content = response.choices[0].message.content
        if not content:
            return {"success": False, "error": "AI 返回为空"}

        return {"success": True, "content": content}

    except Exception as e:
        return {"success": False, "error": str(e)}


async def calculate_display_delay(answer_type: str, submit_time_seconds: float) -> float:
    """计算回答显示延迟（防作弊机制）。

    规则：
    1. AI 代答：随机延迟 5-15 秒
    2. 真人回答：最低显示 5 秒延迟（如果提交过快）
    3. 统一再加 0-3 秒网络延迟模拟

    Args:
        answer_type: "ai" 或 "human"
        submit_time_seconds: 提交时经过的秒数

    Returns:
        延迟秒数
    """
    base_delay = 0.0

    if answer_type == "ai":
        # AI 生成可能需要一些时间，模拟"思考"
        base_delay = random.uniform(5, 15)
    else:
        # 真人回答：如果提交过快，强制等待
        if submit_time_seconds < 5:
            base_delay = 5 - submit_time_seconds

    # 额外网络延迟模拟
    network_delay = random.uniform(0, 3)

    return base_delay + network_delay
