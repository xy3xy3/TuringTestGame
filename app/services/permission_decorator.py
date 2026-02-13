"""路由显式权限声明装饰器。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def permission_meta(resource: str, action: str, method: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """为路由处理函数附加显式权限元数据。"""

    normalized_resource = str(resource or "").strip()
    normalized_action = str(action or "").strip()
    normalized_method = str(method or "").strip().upper()

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        existing = getattr(func, "__permission_meta__", {})
        if not isinstance(existing, dict):
            existing = {}

        if normalized_method:
            existing[normalized_method] = {
                "resource": normalized_resource,
                "action": normalized_action,
            }
        else:
            existing.update(
                {
                    "resource": normalized_resource,
                    "action": normalized_action,
                }
            )

        setattr(func, "__permission_meta__", existing)
        return func

    return decorator
