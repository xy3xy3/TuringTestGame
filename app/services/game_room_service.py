"""游戏房间服务 - 处理房间的创建、加入、管理等逻辑。"""

from __future__ import annotations

import hashlib
import secrets
import string
from datetime import datetime, timezone, timedelta
from typing import Any

from beanie import PydanticObjectId

from app.models.game_room import GameRoom, GameConfig
from app.models.game_player import GamePlayer


def _hash_password(password: str, salt: str = "") -> str:
    """对密码进行 SHA256 哈希（加盐）。"""
    if not password:
        return ""
    if not salt:
        salt = secrets.token_hex(16)
    return f"{salt}${hashlib.sha256((salt + password).encode()).hexdigest()}"


def _verify_password(password: str, hashed: str) -> bool:
    """验证密码。"""
    if not password or not hashed:
        return False
    try:
        salt, hash_value = hashed.split("$")
        return hash_value == hashlib.sha256((salt + password).encode()).hexdigest()
    except Exception:
        return False


def generate_room_code(length: int = 6) -> str:
    """生成房间邀请码。"""
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(length))


def generate_player_token() -> str:
    """生成玩家临时令牌。"""
    return secrets.token_urlsafe(16)


async def create_room(
    nickname: str,
    password: str = "",
    owner_nickname: str = "房主",
    min_players: int = 2,
    max_players: int = 8,
    total_rounds: int = 4,
    setup_time: int = 60,
    question_time: int = 30,
    answer_time: int = 45,
    vote_time: int = 15,
) -> dict[str, Any]:
    """创建游戏房间。

    Args:
        nickname: 房主昵称
        password: 房间密码（可选）
        owner_nickname: 房主显示名称
        min_players: 最少玩家数
        max_players: 最多玩家数
        total_rounds: 总回合数
        setup_time: 灵魂注入阶段时长（秒）
        question_time: 提问阶段时长（秒）
        answer_time: 回答阶段时长（秒）
        vote_time: 投票阶段时长（秒）

    Returns:
        {"success": True, "room": GameRoom, "player": GamePlayer, "token": "..."}
    """
    room_code = generate_room_code()
    
    # 确保房间码唯一
    while await GameRoom.find_one({"room_id": room_code}):
        room_code = generate_room_code()

    # 创建游戏配置
    game_config = GameConfig(
        min_players=min_players,
        max_players=max_players,
        total_rounds=total_rounds,
        setup_time=setup_time,
        question_time=question_time,
        answer_time=answer_time,
        vote_time=vote_time,
    )

    # 创建房间（先创建，获取 room id 后再创建玩家）
    room = GameRoom(
        room_id=room_code,
        owner_id=nickname,  # 暂时使用昵称作为 owner_id，后续可更新为玩家 ID
        password=password,  # 直接存储密码，不哈希
        config=game_config,
        phase="waiting",
        current_round=0,
    )
    await room.insert()

    # 创建房主玩家
    player_token = generate_player_token()
    player = GamePlayer(
        room_id=room.room_id,  # 使用 6 位房间码，而非 MongoDB ObjectId
        nickname=nickname,
        token=player_token,
        is_owner=True,
        is_ready=False,
        phase="waiting",
    )
    await player.insert()

    # 更新房间的 owner_id 为玩家 ID，并将玩家加入房间列表
    room.owner_id = str(player.id)
    room.player_ids.append(str(player.id))
    await room.save()

    return {
        "success": True,
        "room": room,
        "player": player,
        "token": player_token,
    }


async def join_room(
    room_code: str,
    nickname: str,
    password: str = "",
) -> dict[str, Any]:
    """加入游戏房间。

    Args:
        room_code: 房间邀请码
        nickname: 玩家昵称
        password: 房间密码（如果有）

    Returns:
        {"success": True, "room": GameRoom, "player": GamePlayer, "token": "..."}
        或
        {"success": False, "error": "错误信息"}
    """
    # 查找房间
    room = await GameRoom.find_one({"room_id": room_code.upper()})
    if not room:
        return {"success": False, "error": "房间不存在"}

    # 检查密码（直接比较，因为现在密码是明文存储的）
    if room.password and room.password != password:
        return {"success": False, "error": "房间密码错误"}

    # 检查房间是否已满
    player_count = await GamePlayer.find({"room_id": room.room_id}).count()
    if player_count >= room.config.max_players:
        return {"success": False, "error": "房间已满"}

    # 检查房间状态
    if room.phase != "waiting":
        return {"success": False, "error": "游戏已开始，无法加入"}

    # 检查昵称是否已存在
    existing = await GamePlayer.find_one({"room_id": room.room_id, "nickname": nickname})
    if existing:
        return {"success": False, "error": "昵称已被使用"}

    # 创建玩家
    player_token = generate_player_token()
    player = GamePlayer(
        room_id=room.room_id,
        nickname=nickname,
        token=player_token,
        is_owner=False,
        is_ready=False,
        phase="waiting",
    )
    await player.insert()

    # 将玩家加入房间列表
    room.player_ids.append(str(player.id))
    await room.save()

    # 通知所有玩家有新玩家加入
    from app.services.game_manager import sse_manager
    await sse_manager.publish(str(room.id), "player_joined", {
        "player_id": str(player.id),
        "nickname": player.nickname,
    })

    return {
        "success": True,
        "room": room,
        "player": player,
        "token": player_token,
    }


async def get_room_by_code(room_code: str) -> GameRoom | None:
    """根据房间码获取房间。"""
    return await GameRoom.find_one({"room_id": room_code.upper()})


async def get_room_by_id(room_id: str) -> GameRoom | None:
    """根据 ID 获取房间。"""
    try:
        object_id = PydanticObjectId(room_id)
    except Exception:
        return None
    return await GameRoom.get(object_id)


async def get_player_by_token(token: str) -> GamePlayer | None:
    """根据令牌获取玩家。"""
    return await GamePlayer.find_one({"token": token})


async def get_players_in_room(room_id: str) -> list[GamePlayer]:
    """获取房间内的所有玩家。"""
    return await GamePlayer.find({"room_id": room_id}).to_list()


async def set_player_ready(room_id: str, player_id: str, is_ready: bool) -> dict[str, Any]:
    """设置玩家准备状态。

    Returns:
        {"success": True, "all_ready": bool, "player_count": int}
    """
    # 获取房间以获取正确的 room_id（6位码）
    room = await get_room_by_id(room_id)
    if not room:
        return {"success": False, "error": "房间不存在"}

    player = await GamePlayer.find_one({"_id": PydanticObjectId(player_id), "room_id": room.room_id})
    if not player:
        return {"success": False, "error": "玩家不存在"}

    player.is_ready = is_ready
    await player.save()

    # 检查是否所有人都准备好了
    players = await get_players_in_room(room.room_id)
    all_ready = all(p.is_ready for p in players)
    player_count = len(players)

    # 通知所有玩家准备状态已变更
    from app.services.game_manager import sse_manager
    await sse_manager.publish(str(room.id), "player_ready_changed", {
        "player_id": player_id,
        "is_ready": is_ready,
        "all_ready": all_ready,
    })

    return {
        "success": True,
        "all_ready": all_ready,
        "player_count": player_count,
    }


async def leave_room(room_id: str, player_id: str) -> dict[str, Any]:
    """玩家离开房间。

    Returns:
        {"success": True, "room_deleted": bool}
    """
    # 获取房间以获取正确的 room_id（6位码）
    room = await get_room_by_id(room_id)
    if not room:
        return {"success": False, "error": "房间不存在"}

    player = await GamePlayer.find_one({"_id": PydanticObjectId(player_id), "room_id": room.room_id})
    if not player:
        return {"success": False, "error": "玩家不存在"}

    # 如果是房主离开，解散房间
    if player.is_owner:
        await GamePlayer.find({"room_id": room.room_id}).delete()
        if room:
            await room.delete()
        return {"success": True, "room_deleted": True}

    # 否则只删除玩家
    await player.delete()

    # 通知所有玩家有玩家离开
    from app.services.game_manager import sse_manager
    await sse_manager.publish(str(room.id), "player_left", {
        "player_id": player_id,
    })

    # 检查是否还有其他玩家
    remaining = await GamePlayer.find({"room_id": room.room_id}).count()
    if remaining == 0:
        if room:
            await room.delete()
        return {"success": True, "room_deleted": True}

    return {"success": True, "room_deleted": False}


async def update_player_setup(
    room_id: str,
    player_id: str,
    system_prompt: str,
    ai_model_id: str,
) -> dict[str, Any]:
    """更新玩家的灵魂注入设置。

    Args:
        room_id: 房间 ID
        player_id: 玩家 ID
        system_prompt: 系统提示词
        ai_model_id: AI 模型 ID

    Returns:
        {"success": True} 或 {"success": False, "error": "..."}
    """
    # 获取房间以获取正确的 room_id（6位码）
    room = await get_room_by_id(room_id)
    if not room:
        return {"success": False, "error": "房间不存在"}

    player = await GamePlayer.find_one({"_id": PydanticObjectId(player_id), "room_id": room.room_id})
    if not player:
        return {"success": False, "error": "玩家不存在"}

    player.system_prompt = system_prompt
    player.ai_model_id = ai_model_id
    player.phase = "setup"
    await player.save()

    return {"success": True}


async def check_all_players_ready(room_id: str) -> dict[str, Any]:
    """检查是否所有玩家都已完成灵魂注入。

    Returns:
        {"all_ready": bool, "ready_count": int, "total_count": int}
    """
    # 获取房间以获取正确的 room_id（6位码）
    room = await get_room_by_id(room_id)
    if not room:
        return {"all_ready": False, "ready_count": 0, "total_count": 0}

    players = await get_players_in_room(room.room_id)
    total_count = len(players)
    ready_count = sum(1 for p in players if p.phase == "setup")

    return {
        "all_ready": ready_count == total_count and total_count >= 2,
        "ready_count": ready_count,
        "total_count": total_count,
    }


async def kick_player(room_id: str, player_id: str, requester_id: str) -> dict[str, Any]:
    """踢出玩家（房主操作）。

    Args:
        room_id: 房间 ID
        player_id: 被踢出的玩家 ID
        requester_id: 请求者 ID（房主）

    Returns:
        {"success": True} 或 {"success": False, "error": "..."}
    """
    # 获取房间以获取正确的 room_id（6位码）
    room = await get_room_by_id(room_id)
    if not room:
        return {"success": False, "error": "房间不存在"}

    # 验证请求者是房主
    requester = await GamePlayer.find_one({"_id": PydanticObjectId(requester_id), "room_id": room.room_id})
    if not requester or not requester.is_owner:
        return {"success": False, "error": "只有房主可以踢人"}

    # 不能踢自己
    if player_id == requester_id:
        return {"success": False, "error": "不能踢自己"}

    # 查找被踢出的玩家
    player = await GamePlayer.find_one({"_id": PydanticObjectId(player_id), "room_id": room_id})
    if not player:
        return {"success": False, "error": "玩家不存在"}

    # 不能踢房主
    if player.is_owner:
        return {"success": False, "error": "不能踢房主"}

    # 删除玩家
    await player.delete()

    return {"success": True}
