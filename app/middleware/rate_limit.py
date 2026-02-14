"""游戏接口 IP 限流中间件。"""

from __future__ import annotations

from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.services import rate_limit_service


def _resolve_scope(path: str, method: str) -> str | None:
    """根据请求路径与方法映射限流场景。"""
    upper_method = method.upper()
    if upper_method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if path == "/game/create":
        return "create_room"
    if path == "/game/join":
        return "join_room"
    if path == "/game/api/chat":
        return "chat_api"
    if path.startswith("/game/"):
        return "game_write"
    return None


def _build_reject_response(request: Request, retry_after: int) -> Response:
    """根据请求类型返回 429 响应。"""
    headers = {"Retry-After": str(max(retry_after, 1))}
    message = f"请求过于频繁，请 {max(retry_after, 1)} 秒后重试。"

    if request.url.path.startswith("/game/api/"):
        return JSONResponse({"success": False, "error": message}, status_code=429, headers=headers)

    if request.headers.get("HX-Request") == "true":
        return HTMLResponse(content=f'<div class="text-red-400">{message}</div>', status_code=429, headers=headers)

    return HTMLResponse(content=message, status_code=429, headers=headers)


class GameRateLimitMiddleware(BaseHTTPMiddleware):
    """仅针对游戏接口执行的 IP 级别限流。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        scope = _resolve_scope(path, request.method)
        if scope is None:
            return await call_next(request)

        try:
            decision = await rate_limit_service.check_request_allowed(request, scope=scope)
        except Exception:
            # 配置或存储暂时不可用时按放行处理，避免影响主业务可用性。
            return await call_next(request)

        if decision.allowed:
            return await call_next(request)
        return _build_reject_response(request, decision.retry_after)
