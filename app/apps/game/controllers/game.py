"""游戏前台控制器 - 处理房间创建、加入、游戏流程等。"""

from __future__ import annotations

import json
from typing import Any

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from beanie import PydanticObjectId

from app.models.game_player import GamePlayer
from app.models.game_room import GameRoom
from app.services import ai_chat_service, config_service, game_manager, game_room_service, prompt_templates_service, sse_manager

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(prefix="/game")


def _get_player_from_cookie(request: Request) -> tuple[str, str] | None:
    """从 Cookie 获取玩家信息。"""
    player_id = request.cookies.get("player_id")
    player_token = request.cookies.get("player_token")
    if not player_id or not player_token:
        return None
    return player_id, player_token


async def _get_authed_player(request: Request, room: GameRoom | None = None) -> GamePlayer | None:
    """从 Cookie 获取并校验当前玩家，必要时校验是否属于指定房间。"""
    player_info = _get_player_from_cookie(request)
    if not player_info:
        return None

    player_id, player_token = player_info
    try:
        object_id = PydanticObjectId(player_id)
    except Exception:
        return None

    query: dict[str, Any] = {
        "_id": object_id,
        "token": player_token,
    }
    if room:
        query["room_id"] = room.room_id

    return await GamePlayer.find_one(query)


def _redirect_by_phase(room_id: str, phase: str) -> RedirectResponse | None:
    """根据房间阶段返回前端应该跳转的页面。"""
    if phase == "setup":
        return RedirectResponse(url=f"/game/{room_id}/setup", status_code=302)
    if phase == "playing":
        return RedirectResponse(url=f"/game/{room_id}/play", status_code=302)
    if phase == "finished":
        return RedirectResponse(url=f"/game/{room_id}/result", status_code=302)
    return None


async def _list_waiting_room_rows() -> list[dict[str, Any]]:
    """查询前台可加入的房间列表（等待中），包含密码标记与人数信息。"""
    rooms = await GameRoom.find({"phase": "waiting"}).sort("-created_at").to_list()
    if not rooms:
        return []

    room_codes = [room.room_id for room in rooms]
    players = await GamePlayer.find({"room_id": {"$in": room_codes}}).to_list()
    player_count_map: dict[str, int] = {}
    for player in players:
        player_count_map[player.room_id] = player_count_map.get(player.room_id, 0) + 1

    rows: list[dict[str, Any]] = []
    for room in rooms:
        player_count = player_count_map.get(room.room_id, 0)
        max_players = room.config.max_players
        rows.append(
            {
                "room_id": str(room.id),
                "room_code": room.room_id,
                "has_password": bool(room.password),
                "phase": room.phase,
                "player_count": player_count,
                "max_players": max_players,
                "can_join": player_count < max_players,
            }
        )
    return rows


@router.get("", response_class=HTMLResponse)
async def game_index(request: Request) -> HTMLResponse:
    """游戏首页。"""
    # 获取邀请链接参数
    room_code = request.query_params.get("room", "")
    password = request.query_params.get("pwd", "")

    return templates.TemplateResponse(
        "pages/index.html",
        {
            "request": request,
            "invite_room_code": room_code,
            "invite_password": password,
            "bgm_stage": "waiting",
        },
    )


@router.get("/rooms", response_class=HTMLResponse)
async def game_rooms_page(request: Request) -> HTMLResponse:
    """房间列表页（前台）。"""
    rooms = await _list_waiting_room_rows()
    return templates.TemplateResponse(
        "pages/rooms.html",
        {
            "request": request,
            "rooms": rooms,
            "bgm_stage": "waiting",
        },
    )


@router.get("/rooms/table", response_class=HTMLResponse)
async def game_rooms_table(request: Request) -> HTMLResponse:
    """房间列表 partial。"""
    rooms = await _list_waiting_room_rows()
    return templates.TemplateResponse(
        "partials/room_cards.html",
        {
            "request": request,
            "rooms": rooms,
        },
    )


@router.post("/create", response_class=HTMLResponse)
async def create_room(request: Request) -> HTMLResponse:
    """创建房间。"""
    form_data = await request.form()
    nickname = str(form_data.get("nickname", "")).strip()
    password = str(form_data.get("password", "")).strip()

    if not nickname:
        return HTMLResponse(content='<div class="text-red-400">请输入昵称</div>')

    result = await game_room_service.create_room(
        nickname=nickname,
        password=password,
    )

    if result["success"]:
        # 设置 Cookie 并返回跳转脚本
        response = HTMLResponse(content=f'<script>window.location.href="/game/{result["room"].id}";</script>')
        response.set_cookie("player_id", str(result["player"].id), max_age=86400, path="/")
        response.set_cookie("player_token", result["token"], max_age=86400, path="/")
        return response

    return HTMLResponse(content=f'<div class="text-red-400">{result.get("error", "创建失败")}</div>')


@router.post("/join", response_class=HTMLResponse)
async def join_room(request: Request) -> HTMLResponse:
    """加入房间。"""
    form_data = await request.form()
    room_code = str(form_data.get("room_code", "")).strip()
    nickname = str(form_data.get("nickname", "")).strip()
    password = str(form_data.get("password", "")).strip()

    if not room_code or not nickname:
        return HTMLResponse(content='<div class="text-red-400">请填写房间号和昵称</div>')

    result = await game_room_service.join_room(
        room_code=room_code,
        nickname=nickname,
        password=password,
    )

    if result["success"]:
        # 设置 Cookie 并返回跳转脚本
        response = HTMLResponse(content=f'<script>window.location.href="/game/{result["room"].id}";</script>')
        response.set_cookie("player_id", str(result["player"].id), max_age=86400, path="/")
        response.set_cookie("player_token", result["token"], max_age=86400, path="/")
        return response

    return HTMLResponse(content=f'<div class="text-red-400">{result.get("error", "加入失败")}</div>')


@router.get("/{room_id}", response_class=HTMLResponse)
async def room_page(request: Request, room_id: str) -> HTMLResponse:
    """房间页面。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")

    redirect = _redirect_by_phase(room_id, room.phase)
    if redirect:
        return redirect

    # 使用房间的 room_id（6位码）来查询玩家
    players = await game_room_service.get_players_in_room(room.room_id)

    # 获取当前玩家信息并校验 token
    current_player = await _get_authed_player(request, room)

    # 生成邀请链接
    base_url = await config_service.get_base_url()
    invite_params = f"?room={room.room_id}"
    if room.password:
        invite_params += f"&pwd={room.password}"
    invite_link = f"{base_url}/game{invite_params}"

    return templates.TemplateResponse(
        "pages/room.html",
        {
            "request": request,
            "room": room,
            "players": players,
            "current_player": current_player,
            "invite_link": invite_link,
            "bgm_stage": "waiting",
        },
    )


@router.get("/{room_id}/result", response_class=HTMLResponse)
async def result_page(request: Request, room_id: str) -> HTMLResponse:
    """游戏结算页面。"""
    return templates.TemplateResponse(
        "pages/result.html",
        {
            "request": request,
            "room_id": room_id,
            "bgm_stage": "finished",
        },
    )


@router.post("/reconnect")
async def reconnect(request: Request) -> dict[str, Any]:
    """重新连接（通过 player_id）。"""
    player = await _get_authed_player(request)
    if not player:
        return {"success": False, "error": "未登录"}

    # player.room_id 存的是 6 位房间码，需要按邀请码查询房间
    room = await game_room_service.get_room_by_code(player.room_id)
    if not room:
        return {"success": False, "error": "房间不存在"}

    redirect_path = f"/game/{room.id}"
    if room.phase == "setup":
        redirect_path = f"/game/{room.id}/setup"
    elif room.phase == "playing":
        redirect_path = f"/game/{room.id}/play"
    elif room.phase == "finished":
        redirect_path = f"/game/{room.id}/result"

    # 返回房间信息，让前端跳转到对应页面
    return {
        "success": True,
        "room_id": str(room.id),
        "room_code": room.room_id,
        "phase": room.phase,
        "redirect": redirect_path,
    }


@router.get("/{room_id}/play", response_class=HTMLResponse)
async def play_page(request: Request, room_id: str) -> HTMLResponse:
    """游戏进行页面。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")

    redirect = _redirect_by_phase(room_id, room.phase)
    if redirect and room.phase != "playing":
        return redirect

    player = await _get_authed_player(request, room)
    if not player:
        raise HTTPException(status_code=401, detail="未登录")

    # 使用房间的 room_id（6位码）来查询玩家
    players = await game_room_service.get_players_in_room(room.room_id)

    return templates.TemplateResponse(
        "pages/play.html",
        {
            "request": request,
            "room": room,
            "player_id": str(player.id),
            "current_round": room.current_round,
            "players": players,
            "bgm_stage": "playing_questioning",
        },
    )


@router.get("/{room_id}/setup", response_class=HTMLResponse)
async def setup_page(request: Request, room_id: str) -> HTMLResponse:
    """灵魂注入页面。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")

    redirect = _redirect_by_phase(room_id, room.phase)
    if redirect and room.phase != "setup":
        return redirect

    player = await _get_authed_player(request, room)
    if not player:
        raise HTTPException(status_code=401, detail="未登录或玩家不存在")

    # 获取可用 AI 模型
    ai_models = await ai_chat_service.get_enabled_models()
    prompt_templates = await prompt_templates_service.list_enabled_template_options()

    return templates.TemplateResponse(
        "pages/setup.html",
        {
            "request": request,
            "room": room,
            "player": player,
            "ai_models": ai_models,
            "prompt_templates": prompt_templates,
            "bgm_stage": "setup",
        },
    )


@router.post("/{room_id}/setup", response_class=HTMLResponse)
async def submit_setup(request: Request, room_id: str) -> HTMLResponse:
    """提交灵魂注入设置。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        return HTMLResponse(content='<div class="text-red-400">房间不存在</div>')

    player = await _get_authed_player(request, room)
    if not player:
        return HTMLResponse(content='<div class="text-red-400">未登录</div>')

    form_data = await request.form()
    system_prompt = str(form_data.get("system_prompt", "")).strip()
    ai_model_id = str(form_data.get("ai_model_id", "")).strip()

    if not system_prompt:
        return HTMLResponse(content='<div class="text-red-400">请输入系统提示词</div>')

    result = await game_room_service.update_player_setup(
        room_id=room_id,
        player_id=str(player.id),
        system_prompt=system_prompt,
        ai_model_id=ai_model_id,
    )

    if result["success"]:
        return HTMLResponse(content='<div class="text-green-400">设置已保存</div>')
    return HTMLResponse(content=f'<div class="text-red-400">{result.get("error", "设置失败")}</div>')


@router.post("/{room_id}/ready", response_class=HTMLResponse)
async def set_ready(request: Request, room_id: str) -> HTMLResponse:
    """设置准备状态。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        return HTMLResponse(content='<div class="text-red-400">房间不存在</div>')

    player = await _get_authed_player(request, room)
    if not player:
        return HTMLResponse(content='<div class="text-red-400">未登录</div>')

    form_data = await request.form()
    is_ready = form_data.get("is_ready", "true").strip().lower() == "true"

    result = await game_room_service.set_player_ready(room_id, str(player.id), is_ready)

    if result["success"]:
        # 只更新准备状态，不自动开始游戏
        return HTMLResponse(content='')

    return HTMLResponse(content=f'<div class="text-red-400">{result.get("error", "操作失败")}</div>')


@router.post("/{room_id}/start", response_class=HTMLResponse)
async def start_game(request: Request, room_id: str) -> HTMLResponse:
    """开始游戏（房主操作）。"""
    # 验证房主身份
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        return HTMLResponse(content='<div class="text-red-400">房间不存在</div>')

    player = await _get_authed_player(request, room)
    if not player:
        return HTMLResponse(content='<div class="text-red-400">未登录</div>')

    players = await game_room_service.get_players_in_room(room.room_id)
    current_player = next((p for p in players if str(p.id) == str(player.id)), None)
    if not current_player or not current_player.is_owner:
        return HTMLResponse(content='<div class="text-red-400">只有房主才能开始游戏</div>')

    # 检查是否所有玩家都已准备
    not_ready = [p for p in players if not p.is_ready]
    if not_ready:
        names = ', '.join(p.nickname for p in not_ready)
        return HTMLResponse(content=f'<div class="text-red-400">以下玩家未准备：{names}</div>')

    # 检查人数
    if len(players) < room.config.min_players:
        return HTMLResponse(content=f'<div class="text-red-400">需要至少 {room.config.min_players} 名玩家</div>')

    result = await game_manager.start_game(room_id)
    if result["success"]:
        # 成功启动游戏，返回跳转脚本
        return HTMLResponse(content='<script>window.location.href="/game/' + room_id + '/setup";</script>')
    return HTMLResponse(content=f'<div class="text-red-400">{result.get("error", "开始游戏失败")}</div>')


@router.post("/{room_id}/kick/{player_id}", response_class=HTMLResponse)
async def kick_player(request: Request, room_id: str, player_id: str) -> HTMLResponse:
    """踢出玩家（房主操作）。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        return HTMLResponse(content='<div class="text-red-400">房间不存在</div>')

    requester = await _get_authed_player(request, room)
    if not requester:
        return HTMLResponse(content='<div class="text-red-400">未登录</div>')

    result = await game_room_service.kick_player(room_id, player_id, str(requester.id))

    if result["success"]:
        # 通知其他玩家
        await sse_manager.publish(room_id, "player_kicked", {
            "player_id": player_id,
        })
        return HTMLResponse(content='<div class="text-green-400">已踢出玩家</div>')

    return HTMLResponse(content=f'<div class="text-red-400">{result.get("error", "踢出失败")}</div>')


@router.post("/{room_id}/question", response_class=HTMLResponse)
async def submit_question(request: Request, room_id: str) -> HTMLResponse:
    """提交问题。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        return HTMLResponse(content='<div class="text-red-400">房间不存在</div>')

    player = await _get_authed_player(request, room)
    if not player:
        return HTMLResponse(content='<div class="text-red-400">未登录</div>')

    form_data = await request.form()
    question = str(form_data.get("question", "")).strip()
    round_id = str(form_data.get("round_id", "")).strip()

    if not question:
        return HTMLResponse(content='<div class="text-red-400">问题不能为空</div>')

    result = await game_manager.submit_question(room_id, round_id, str(player.id), question)

    if result["success"]:
        return HTMLResponse(content='<div class="text-green-400">问题已提交</div>')
    return HTMLResponse(content=f'<div class="text-red-400">{result.get("error", "提交失败")}</div>')


@router.post("/{room_id}/answer", response_class=HTMLResponse)
async def submit_answer(request: Request, room_id: str) -> HTMLResponse:
    """提交回答。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        return HTMLResponse(content='<div class="text-red-400">房间不存在</div>')

    player = await _get_authed_player(request, room)
    if not player:
        return HTMLResponse(content='<div class="text-red-400">未登录</div>')

    form_data = await request.form()
    answer_type = str(form_data.get("answer_type", "")).strip()
    answer_content = str(form_data.get("answer_content", "")).strip()
    round_id = str(form_data.get("round_id", "")).strip()

    if answer_type not in ("human", "ai"):
        return HTMLResponse(content='<div class="text-red-400">无效的回答类型</div>')

    result = await game_manager.submit_answer(room_id, round_id, str(player.id), answer_type, answer_content)

    if result["success"]:
        return HTMLResponse(content='<div class="text-green-400">回答已提交</div>')
    return HTMLResponse(content=f'<div class="text-red-400">{result.get("error", "提交失败")}</div>')


@router.post("/{room_id}/vote", response_class=HTMLResponse)
async def submit_vote(request: Request, room_id: str) -> HTMLResponse:
    """提交投票。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        return HTMLResponse(content='<div class="text-red-400">房间不存在</div>')

    player = await _get_authed_player(request, room)
    if not player:
        return HTMLResponse(content='<div class="text-red-400">未登录</div>')

    form_data = await request.form()
    vote = str(form_data.get("vote", "")).strip()
    round_id = str(form_data.get("round_id", "")).strip()

    if vote not in ("human", "ai", "skip"):
        return HTMLResponse(content='<div class="text-red-400">无效的投票选项</div>')

    result = await game_manager.submit_vote(room_id, round_id, str(player.id), vote)

    if result["success"]:
        return HTMLResponse(content='<div class="text-green-400">投票已提交</div>')
    return HTMLResponse(content=f'<div class="text-red-400">{result.get("error", "提交失败")}</div>')


@router.post("/{room_id}/leave", response_class=HTMLResponse)
async def leave_room(request: Request, room_id: str) -> HTMLResponse:
    """离开房间。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        return HTMLResponse(content='<script>window.location.href="/game";</script>')

    player = await _get_authed_player(request, room)
    if not player:
        return HTMLResponse(content='<script>window.location.href="/game";</script>')

    result = await game_room_service.leave_room(room_id, str(player.id))

    if result["success"]:
        # 清除玩家 Cookie
        response = HTMLResponse(content='<script>window.location.href="/game";</script>')
        response.delete_cookie("player_id", path="/")
        response.delete_cookie("player_token", path="/")
        return response

    return HTMLResponse(content=f'<div class="text-red-400">{result.get("error", "离开失败")}</div>')


@router.get("/{room_id}/events")
async def sse_events(request: Request, room_id: str):
    """SSE 事件流。"""

    async def event_generator():
        queue = sse_manager.subscribe(room_id)
        try:
            while True:
                message = await queue.get()
                # message 是 JSON 字符串，格式：{"event": "player_ready_changed", "data": {...}}
                # 解析为标准 SSE 格式
                parsed = json.loads(message)
                event_name = parsed.get("event", "")
                event_data = json.dumps(parsed.get("data", {}))
                yield f"event: {event_name}\ndata: {event_data}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse_manager.unsubscribe(room_id, queue)

    import asyncio
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/api/chat")
async def test_chat(request: Request) -> dict[str, Any]:
    """测试聊天 API（用于灵魂注入阶段的调试）。"""
    data = await request.json()
    system_prompt = data.get("system_prompt", "")
    message = data.get("message", "")
    model_id = data.get("model_id")

    if not system_prompt or not message:
        return {"success": False, "error": "缺少必要参数"}

    result = await ai_chat_service.call_ai(
        system_prompt=system_prompt,
        user_message=message,
        model_id=model_id,
    )

    if result["success"]:
        return {"success": True, "content": result["content"]}
    return {"success": False, "error": result.get("error", "调用失败")}


@router.get("/{room_id}/players", response_class=HTMLResponse)
async def get_room_players(request: Request, room_id: str) -> HTMLResponse:
    """获取房间玩家列表（HTML partial）。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        return HTMLResponse(content="<p>房间不存在</p>")

    players = await game_room_service.get_players_in_room(room.room_id)

    return templates.TemplateResponse(
        "partials/room_player_list.html",
        {
            "request": request,
            "players": players,
        },
    )


@router.get("/api/{room_id}/state")
async def get_room_state(room_id: str) -> dict[str, Any]:
    """获取房间状态（API）。"""
    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        return {"success": False, "error": "房间不存在"}

    # 使用房间的 room_id（6位码）来查询玩家
    players = await game_room_service.get_players_in_room(room.room_id)

    return {
        "success": True,
        "room": {
            "id": str(room.id),
            "room_code": room.room_id,
            "phase": room.phase,
            "current_round": room.current_round,
            "total_rounds": room.total_rounds,
            "config": room.config.model_dump(),
            "started_at": room.started_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if room.started_at else None,
        },
        "players": [
            {
                "id": str(p.id),
                "nickname": p.nickname,
                "is_owner": p.is_owner,
                "is_ready": p.is_ready,
                "score": p.total_score or 0,
            }
            for p in players
        ],
    }


@router.get("/api/{room_id}/round")
async def get_current_round(room_id: str) -> dict[str, Any]:
    """获取当前回合状态（API）。"""
    from app.models.game_round import GameRound

    room = await game_room_service.get_room_by_id(room_id)
    if not room:
        return {"success": False, "error": "房间不存在"}

    if room.phase != "playing":
        return {"success": False, "phase": room.phase, "error": "游戏未开始"}

    # 获取当前回合（使用 6 位房间码查询）
    current_round = await GameRound.find_one({
        "room_id": room.room_id,
        "round_number": room.current_round,
    })

    if not current_round:
        return {"success": False, "error": "回合不存在"}

    # 获取玩家信息
    players = await game_room_service.get_players_in_room(room.room_id)
    interrogator = next((p for p in players if str(p.id) == current_round.interrogator_id), None)
    subject = next((p for p in players if str(p.id) == current_round.subject_id), None)

    return {
        "success": True,
        "round": {
            "id": str(current_round.id),
            "round_number": current_round.round_number,
            "total_rounds": room.total_rounds,
            "status": current_round.status,
            "question": current_round.question,
            "answer": current_round.answer,
            "answer_type": current_round.answer_type,
            "interrogator_id": current_round.interrogator_id,
            "interrogator_nickname": interrogator.nickname if interrogator else "未知",
            "subject_id": current_round.subject_id,
            "subject_nickname": subject.nickname if subject else "未知",
        },
    }
