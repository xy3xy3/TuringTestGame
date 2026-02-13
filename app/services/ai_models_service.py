"""AI 模型配置服务。"""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId

from app.models.ai_model import AIModel


async def list_ai_models(enabled_only: bool = False) -> list[AIModel]:
    """查询 AI 模型列表。"""

    query = AIModel.find_all()
    if enabled_only:
        query = AIModel.find(AIModel.is_enabled == True)
    return await query.sort("-created_at").to_list()


async def get_ai_model_by_id(model_id: str) -> AIModel | None:
    """按 ID 查询 AI 模型。"""

    try:
        object_id = PydanticObjectId(model_id)
    except Exception:
        return None
    return await AIModel.get(object_id)


async def get_default_ai_model() -> AIModel | None:
    """获取默认 AI 模型。"""

    return await AIModel.find_one(AIModel.is_default == True)


async def get_enabled_ai_models() -> list[AIModel]:
    """获取所有启用的 AI 模型。"""

    return await AIModel.find(AIModel.is_enabled == True).sort("name").to_list()


async def create_ai_model(payload: dict[str, Any]) -> AIModel:
    """创建 AI 模型配置。"""

    is_enabled = str(payload.get("is_enabled", "true")).strip().lower() == "true"
    is_default = str(payload.get("is_default", "false")).strip().lower() == "true"

    # 如果设为默认，先取消其他默认
    if is_default:
        await AIModel.find(AIModel.is_default == True).update(
            {"$set": {"is_default": False}}
        )

    model = AIModel(
        name=str(payload.get("name", "")).strip()[:64],
        base_url=str(payload.get("base_url", "")).strip()[:256],
        api_key=str(payload.get("api_key", "")).strip()[:256],
        model_name=str(payload.get("model_name", "")).strip()[:64],
        temperature=float(payload.get("temperature", 0.8)),
        max_tokens=int(payload.get("max_tokens", 500)),
        is_enabled=is_enabled,
        is_default=is_default,
        description=str(payload.get("description", "")).strip()[:256],
    )
    await model.insert()
    return model


async def update_ai_model(model: AIModel, payload: dict[str, Any]) -> AIModel:
    """更新 AI 模型配置。"""

    if "name" in payload:
        model.name = str(payload["name"]).strip()[:64]
    if "base_url" in payload:
        model.base_url = str(payload["base_url"]).strip()[:256]
    if "api_key" in payload:
        model.api_key = str(payload["api_key"]).strip()[:256]
    if "model_name" in payload:
        model.model_name = str(payload["model_name"]).strip()[:64]
    if "temperature" in payload:
        model.temperature = float(payload["temperature"])
    if "max_tokens" in payload:
        model.max_tokens = int(payload["max_tokens"])
    if "description" in payload:
        model.description = str(payload["description"]).strip()[:256]

    if "is_enabled" in payload:
        model.is_enabled = str(payload["is_enabled"]).strip().lower() == "true"
    if "is_default" in payload:
        is_default = str(payload["is_default"]).strip().lower() == "true"
        if is_default and not model.is_default:
            # 取消其他默认
            await AIModel.find(AIModel.is_default == True).update(
                {"$set": {"is_default": False}}
            )
        model.is_default = is_default

    await model.save()
    return model


async def delete_ai_model(model: AIModel) -> None:
    """删除 AI 模型配置。"""

    await model.delete()


async def toggle_ai_model(model: AIModel) -> AIModel:
    """切换 AI 模型启用状态。"""

    model.is_enabled = not model.is_enabled
    await model.save()
    return model