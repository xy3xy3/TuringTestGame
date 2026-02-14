"""游戏房间只读控制器。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.models import GamePlayer, GameRoom
from app.services import log_service, permission_decorator

BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(prefix="/admin")

ROOM_PAGE_SIZE = 15
ROOM_PHASE_OPTIONS: dict[str, str] = {
    "waiting": "等待中",
    "setup": "灵魂注入",
    "playing": "游戏进行中",
    "finished": "已结束",
}
ROOM_SORT_OPTIONS: dict[str, str] = {
    "created_desc": "创建时间（新到旧）",
    "created_asc": "创建时间（旧到新）",
    "updated_round_desc": "当前回合（高到低）",
}


def base_context(request: Request) -> dict[str, Any]:
    """构建模板公共上下文。"""
    return {
        "request": request,
        "current_admin": request.session.get("admin_name"),
    }


def fmt_dt(value: datetime | None) -> str:
    """格式化时间字段，统一页面展示。"""
    if not value:
        return "-"
    if value.tzinfo is None:
        return value.strftime("%Y-%m-%d %H:%M")
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


templates.env.filters["fmt_dt"] = fmt_dt


def parse_positive_int(value: Any, default: int = 1) -> int:
    """解析正整数参数，非法值回退默认值。"""
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def parse_room_filters(values: Mapping[str, Any]) -> tuple[dict[str, str], int]:
    """解析房间列表筛选条件。"""
    search_q = str(values.get("search_q") or values.get("q") or "").strip()
    search_phase = str(values.get("search_phase") or "").strip().lower()
    if search_phase not in ROOM_PHASE_OPTIONS:
        search_phase = ""

    search_sort = str(values.get("search_sort") or "created_desc").strip()
    if search_sort not in ROOM_SORT_OPTIONS:
        search_sort = "created_desc"

    page = parse_positive_int(values.get("page"), default=1)
    return (
        {
            "search_q": search_q,
            "search_phase": search_phase,
            "search_sort": search_sort,
        },
        page,
    )


def build_pagination(total: int, page: int, page_size: int) -> dict[str, Any]:
    """构建通用分页信息。"""
    total_pages = max((total + page_size - 1) // page_size, 1)
    current = min(max(page, 1), total_pages)
    start_page = max(current - 2, 1)
    end_page = min(start_page + 4, total_pages)
    start_page = max(end_page - 4, 1)

    if total == 0:
        start_item = 0
        end_item = 0
    else:
        start_item = (current - 1) * page_size + 1
        end_item = min(current * page_size, total)

    return {
        "page": current,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_prev": current > 1,
        "has_next": current < total_pages,
        "prev_page": current - 1,
        "next_page": current + 1,
        "pages": list(range(start_page, end_page + 1)),
        "start_item": start_item,
        "end_item": end_item,
    }


async def list_room_rows(filters: dict[str, str], page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
    """查询房间数据并补充每个房间的玩家数。"""
    query: dict[str, Any] = {}
    keyword = filters.get("search_q", "").strip()
    if keyword:
        regex = {"$regex": keyword, "$options": "i"}
        query["$or"] = [
            {"room_id": regex},
            {"owner_id": regex},
        ]

    phase = filters.get("search_phase", "").strip()
    if phase:
        query["phase"] = phase

    sort_value = filters.get("search_sort", "created_desc")
    if sort_value == "created_asc":
        sort_field = "created_at"
    elif sort_value == "updated_round_desc":
        sort_field = "-current_round"
    else:
        sort_field = "-created_at"

    total = await GameRoom.find(query).count()
    safe_page = page if page > 0 else 1
    skip = max((safe_page - 1) * page_size, 0)
    rooms = await GameRoom.find(query).sort(sort_field).skip(skip).limit(page_size).to_list()

    room_codes = [room.room_id for room in rooms]
    player_count_map: dict[str, int] = {}
    if room_codes:
        players = await GamePlayer.find({"room_id": {"$in": room_codes}}).to_list()
        for player in players:
            player_count_map[player.room_id] = player_count_map.get(player.room_id, 0) + 1

    rows = [
        {
            "room": room,
            "player_count": player_count_map.get(room.room_id, 0),
        }
        for room in rooms
    ]
    return rows, total


async def build_room_table_context(
    request: Request,
    filters: dict[str, str],
    page: int,
) -> dict[str, Any]:
    """构建房间表格上下文。"""
    rows, total = await list_room_rows(filters, page, ROOM_PAGE_SIZE)
    pagination = build_pagination(total, page, ROOM_PAGE_SIZE)
    return {
        **base_context(request),
        "rows": rows,
        "filters": filters,
        "pagination": pagination,
        "phase_options": ROOM_PHASE_OPTIONS,
    }


@router.get("/game_rooms", response_class=HTMLResponse)
@permission_decorator.permission_meta("game_rooms", "read")
async def game_rooms_page(request: Request) -> HTMLResponse:
    """游戏房间只读页面。"""
    filters, page = parse_room_filters(request.query_params)
    context = await build_room_table_context(request, filters, page)
    context["room_sort_options"] = ROOM_SORT_OPTIONS
    await log_service.record_request(
        request,
        action="read",
        module="game_rooms",
        target="游戏房间",
        detail="访问房间管理页面（只读）",
    )
    return templates.TemplateResponse("pages/game_rooms.html", context)


@router.get("/game_rooms/table", response_class=HTMLResponse)
@permission_decorator.permission_meta("game_rooms", "read")
async def game_rooms_table(request: Request) -> HTMLResponse:
    """游戏房间表格 partial。"""
    filters, page = parse_room_filters(request.query_params)
    context = await build_room_table_context(request, filters, page)
    return templates.TemplateResponse("partials/game_rooms_table.html", context)
