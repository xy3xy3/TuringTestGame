"""业务服务层。"""

from app.services import (
    admin_user_service,
    ai_chat_service,
    ai_models_service,
    auth_service,
    backup_service,
    cloud_storage,
    config_service,
    csrf_service,
    game_manager as game_manager_module,
    game_room_service,
    log_service,
    permission_decorator,
    permission_service,
    prompt_templates_service,
    rate_limit_service,
    redis_service,
    role_service,
    validators,
)

# 导出游戏管理器和 SSE 管理器实例
game_manager = game_manager_module.game_manager
sse_manager = game_manager_module.sse_manager

__all__ = [
    "admin_user_service",
    "ai_chat_service",
    "ai_models_service",
    "auth_service",
    "backup_service",
    "cloud_storage",
    "config_service",
    "csrf_service",
    "game_manager",
    "game_room_service",
    "log_service",
    "permission_decorator",
    "permission_service",
    "prompt_templates_service",
    "rate_limit_service",
    "redis_service",
    "role_service",
    "sse_manager",
    "validators",
]
