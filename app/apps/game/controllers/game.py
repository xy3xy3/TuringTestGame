"""游戏前台控制器 - 处理房间创建、加入、游戏流程等。"""

from __future__ import annotations

import json
from typing import Any

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.services import game_room_service, game_manager, ai_chat_service

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "apps/admin/templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(prefix="/game")


def _get_player_from_cookie(request: Request) -> tuple[str, str] | None:
    """从 Cookie 获取玩家信息。"""
    player_id = request.cookies.get("player_id")
    player_token = request.cookies.get("player_token")
    if not player_id or not player_token:
        return None
    return player_id, player_token


@router.get("", response_class=HTMLResponse)
async def game_index(request: Request) -> HTMLResponse:
    """游戏首页。"""
    return templates.TemplateResponse("pages/game/index.html", {"request": request})


@router.post("/create")
async def create_room(request: Request) -> dict[str, Any]:
    """创建房间。"""
    form_data = await request.form()
    nickname = str(form_data.get("nickname", "")).strip()
    password = str(form_data.get("password", "")).strip()

    if not nickname:
        raise HTTPException(status_code=400, detail="请输入昵称")

    result = await game_room_service.create_room(
        nickname=nickname,
        password=password,
    )

    if result["success"]:
        return {
            "success": True,
            "room_id": str(result["room"].id),
            "room_code": result["room"].room_code,
            "player_id": str(result["player"].id),
            "token": result["token"],
        }

    return {"success": False, "error": result.get("error", "创建失败")}


@router.post("/join")
async def join_room(request: Request) -> dict[str, Any]:
    """加入房间。"""
    form_data = await request.form()
    room_code = str(form_data.get("room_code", "")).strip()
    nickname = str(form_data.get("nickname", "")).strip()
    password = str(form_data.get("password", "")).strip()

    if not room_code or not nickname:
        raise HTTPException(status_code=400, detail="请填写房间号和昵称")

    result = await game_room_service.join_room(
        room_code=room_code,
        nickname=nickname,
        password=password,
    )

    if result["success"]:
        return {
            "success": True,
            "room_id": str(result["room"].id),
            "room_code": result["room"].room_code,
            "player_id": str(result["player"].id),
            "token": result["token"],
        }

    return {"success": False, "error": result.get("error", "加入失败")}


@router.get("/{room_id}", response_class=HTMLResponse)
async def room_page(request: Request, room_id: str) -> HTMLResponse:
    """房间页面。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")

    players = await game_room_service.get_players_in_room(room_id)

    return templates.TemplateResponse(
        "pages/game/room.html",
        {
            "request": request,
            "room": room,
            "players": players,
        },
    )


@router.get("/{room_id}/result", response_class=HTMLResponse)
async def result_page(request: Request, room_id: str) -> HTMLResponse:
    """游戏结算页面。"""
    return templates.TemplateResponse(
        "pages/game/result.html",
        {
            "request": request,
            "room_id": room_id,
        },
    )


@router.get("/{room_id}/play", response_class=HTMLResponse)
async def play_page(request: Request, room_id: str) -> HTMLResponse:
    """游戏进行页面。"""
    player_info = _get_player_from_cookie(request)
    if not player_info:
        raise HTTPException(status_code=401, detail="未登录")

    player_id, _ = player_info

    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")

    players = await game_room_service.get_players_in_room(room_id)

    return templates.TemplateResponse(
        "pages/game/play.html",
        {
            "request": request,
            "room": room,
            "player_id": player_id,
            "current_round": room.current_round,
            "players": players,
        },
    )


@router.get("/{room_id}/setup", response_class=HTMLResponse)
async def setup_page(request: Request, room_id: str) -> HTMLResponse:
    """灵魂注入页面。"""
    player_info = _get_player_from_cookie(request)
    if not player_info:
        raise HTTPException(status_code=401, detail="未登录")

    player_id, _ = player_info

    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")

    players = await game_room_service.get_players_in_room(room_id)
    player = next((p for p in players if str(p.id) == player_id), None)
    if not player:
        raise HTTPException(status_code=404, detail="玩家不存在")

    # 获取可用 AI 模型
    ai_models = await ai_chat_service.get_enabled_models()

    return templates.TemplateResponse(
        "pages/game/setup.html",
        {
            "request": request,
            "room": room,
            "player": player,
            "ai_models": ai_models,
        },
    )


@router.post("/{room_id}/setup")
async def submit_setup(request: Request, room_id: str) -> dict[str, Any]:
    """提交灵魂注入设置。"""
    player_info = _get_player_from_cookie(request)
    if not player_info:
        return {"success": False, "error": "未登录"}

    player_id, _ = player_info

    form_data = await request.form()
    system_prompt = str(form_data.get("system_prompt", "")).strip()
    ai_model_id = str(form_data.get("ai_model_id", "")).strip()

    if not system_prompt:
        return {"success": False, "error": "请输入系统提示词"}

    result = await game_room_service.update_player_setup(
        room_id=room_id,
        player_id=player_id,
        system_prompt=system_prompt,
        ai_model_id=ai_model_id,
    )

    return result


@router.post("/{room_id}/ready")
async def set_ready(request: Request, room_id: str) -> dict[str, Any]:
    """设置准备状态。"""
    player_info = _get_player_from_cookie(request)
    if not player_info:
        return {"success": False, "error": "未登录"}

    player_id, _ = player_info

    form_data = await request.form()
    is_ready = form_data.get("is_ready", "true").strip().lower() == "true"

    result = await game_room_service.set_player_ready(room_id, player_id, is_ready)

    if result["success"] and result["all_ready"]:
        # 所有人准备好了，开始游戏
        await game_manager.start_game(room_id)

    return result


@router.post("/{room_id}/start")
async def start_game(room_id: str) -> dict[str, Any]:
    """开始游戏（房主操作）。"""
    return await game_manager.start_game(room_id)


@router.post("/{room_id}/question")
async def submit_question(request: Request, room_id: str) -> dict[str, Any]:
    """提交问题。"""
    player_info = _get_player_from_cookie(request)
    if not player_info:
        return {"success": False, "error": "未登录"}

    player_id, _ = player_info

    form_data = await request.form()
    question = str(form_data.get("question", "")).strip()
    round_id = str(form_data.get("round_id", "")).strip()

    if not question:
        return {"success": False, "error": "问题不能为空"}

    return await game_manager.submit_question(room_id, round_id, player_id, question)


@router.post("/{room_id}/answer")
async def submit_answer(request: Request, room_id: str) -> dict[str, Any]:
    """提交回答。"""
    player_info = _get_player_from_cookie(request)
    if not player_info:
        return {"success": False, "error": "未登录"}

    player_id, _ = player_info

    form_data = await request.form()
    answer_type = str(form_data.get("answer_type", "")).strip()
    answer_content = str(form_data.get("answer_content", "")).strip()
    round_id = str(form_data.get("round_id", "")).strip()

    if answer_type not in ("human", "ai"):
        return {"success": False, "error": "无效的回答类型"}

    return await game_manager.submit_answer(room_id, round_id, player_id, answer_type, answer_content)


@router.post("/{room_id}/vote")
async def submit_vote(request: Request, room_id: str) -> dict[str, Any]:
    """提交投票。"""
    player_info = _get_player_from_cookie(request)
    if not player_info:
        return {"success": False, "error": "未登录"}

    player_id, _ = player_info

    form_data = await request.form()
    vote = str(form_data.get("vote", "")).strip()
    round_id = str(form_data.get("round_id", "")).strip()

    if vote not in ("human", "ai", "skip"):
        return {"success": False, "error": "无效的投票选项"}

    return await game_manager.submit_vote(room_id, round_id, player_id, vote)


@router.post("/{room_id}/leave")
async def leave_room(request: Request, room_id: str) -> dict[str, Any]:
    """离开房间。"""
    player_info = _get_player_from_cookie(request)
    if not player_info:
        return {"success": False, "error": "未登录"}

    player_id, _ = player_info

    result = await game_room_service.leave_room(room_id, player_id)
    return result


@router.get("/{room_id}/events")
async def sse_events(request: Request, room_id: str):
    """SSE 事件流。"""

    async def event_generator():
        queue = game_manager.sse_manager.subscribe(room_id)
        try:
            while True:
                message = await queue.get()
                yield f"{message}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            game_manager.sse_manager.unsubscribe(room_id, queue)

    import asyncio
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/api/chat")
async def test_chat(request: Request) -> dict[str, Any]:
    """测试聊天 API（用于灵魂注入阶段的调试）。"""
    data = await request.json()
    system_prompt = data.get("system_prompt", "")
    message = data.get("message", "")

    if not system_prompt or not message:
        return {"success": False, "error": "缺少必要参数"}

    result = await ai_chat_service.call_ai(
        system_prompt=system_prompt,
        user_message=message,
    )

    if result["success"]:
        return {"success": True, "content": result["content"]}
    return {"success": False, "error": result.get("error", "调用失败")}


@router.get("/api/{room_id}/state")
async def get_room_state(room_id: str) -> dict[str, Any]:
    """获取房间状态（API）。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        return {"success": False, "error": "房间不存在"}

    players = await game_room_service.get_players_in_room(room_id)

    return {
        "success": True,
        "room": {
            "id": str(room.id),
            "room_code": room.room_code,
            "phase": room.phase,
            "current_round": room.current_round,
            "config": room.config.model_dump(),
        },
        "players": [
            {
                "id": str(p.id),
                "nickname": p.nickname,
                "is_owner": p.is_owner,
                "is_ready": p.is_ready,
                "score": p.score or 0,
            }
            for p in players
        ],
    }
