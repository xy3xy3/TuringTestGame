"""AI 对话服务 - 用于图灵测试游戏中调用 AI 生成回答。"""

from __future__ import annotations

import random
from typing import Any

from openai import AsyncOpenAI

from app.models.ai_model import AIModel


async def get_enabled_models() -> list[AIModel]:
    """获取所有启用的 AI 模型。"""
    import logging
    logger = logging.getLogger(__name__)

    models = await AIModel.find({"is_enabled": True}).to_list()
    logger.info(f"找到 {len(models)} 个启用的 AI 模型")
    for m in models:
        logger.info(f"  - {m.name} (id={m.id}, model_name={m.model_name})")

    return models


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
        model_id: 可选的模型 ID，如果不提供则使用默认模型或第一个启用的模型

    Returns:
        {"success": True, "content": "AI 的回答"}
        或
        {"success": False, "error": "错误信息"}
    """
    # 获取模型配置
    model_config: AIModel | None = None
    import logging
    logger = logging.getLogger(__name__)

    if model_id:
        logger.info(f"查找模型 ID: {model_id}")
        model_config = await get_model_by_id(model_id)
    else:
        # 尝试获取默认模型
        logger.info("查找默认模型")
        model_config = await get_default_model()

        # 如果没有默认模型，使用第一个启用的模型
        if not model_config:
            logger.info("未找到默认模型，查找第一个启用的模型")
            enabled_models = await get_enabled_models()
            if enabled_models:
                model_config = enabled_models[0]
                logger.info(f"使用启用的模型: {model_config.name}")
            else:
                logger.warning("没有找到任何启用的 AI 模型")

    if not model_config:
        return {"success": False, "error": "没有可用的 AI 模型"}

    try:
        import logging
        logger = logging.getLogger(__name__)

        # 记录调用信息
        logger.info(f"调用 AI - 模型: {model_config.name} ({model_config.model_name}), base_url: {model_config.base_url}")
        logger.info(f"系统提示词: {system_prompt[:100]}...")
        logger.info(f"用户消息: {user_message[:100]}...")

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

        logger.info(f"AI 响应 - choices 数量: {len(response.choices)}")

        if not response.choices:
            return {"success": False, "error": "AI 返回的 choices 为空"}

        content = response.choices[0].message.content
        logger.info(f"AI 返回内容长度: {len(content) if content else 0}")

        if not content:
            return {"success": False, "error": "AI 返回为空"}

        return {"success": True, "content": content}

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"AI 调用失败: {str(e)}", exc_info=True)
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
