"""游戏状态管理器 - 处理游戏核心逻辑、状态机和实时事件。"""

from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import datetime, timezone
from typing import Any

from beanie import PydanticObjectId

from app.models.game_room import GameRoom
from app.models.game_player import GamePlayer
from app.models.game_round import GameRound
from app.models.vote_record import VoteRecord
from app.services import ai_chat_service, config_service, game_room_service


# SSE 事件管理
class SSEManager:
    """SSE 事件管理器。"""

    def __init__(self):
        self._connections: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, room_id: str) -> asyncio.Queue:
        """订阅房间事件。"""
        if room_id not in self._connections:
            self._connections[room_id] = set()
        queue = asyncio.Queue()
        self._connections[room_id].add(queue)
        return queue

    def unsubscribe(self, room_id: str, queue: asyncio.Queue):
        """取消订阅。"""
        if room_id in self._connections:
            self._connections[room_id].discard(queue)

    async def publish(self, room_id: str, event: str, data: dict[str, Any]):
        """发布事件到房间。"""
        if room_id not in self._connections:
            return
        message = json.dumps({"event": event, "data": data})
        for queue in self._connections[room_id]:
            await queue.put(message)

    def get_connection_count(self, room_id: str) -> int:
        """获取房间连接数。"""
        return len(self._connections.get(room_id, set()))


# 全局 SSE 管理器
sse_manager = SSEManager()


class GameManager:
    """游戏状态管理器。"""

    ROLE_BALANCE_DEFAULTS: dict[str, int] = {
        "pity_gap_threshold": 2,
        "weight_base": 100,
        "weight_deficit_step": 40,
        "weight_zero_bonus": 60,
    }
    ROLE_BALANCE_LIMITS: dict[str, tuple[int, int]] = {
        "pity_gap_threshold": (1, 10),
        "weight_base": (1, 10000),
        "weight_deficit_step": (0, 10000),
        "weight_zero_bonus": (0, 10000),
    }

    def __init__(self):
        self._timers: dict[str, asyncio.Task] = {}

    async def _sync_room_time_config(self, room: GameRoom) -> None:
        """同步房间的游戏阶段时长配置，确保使用系统设置最新值。"""
        latest = await config_service.get_game_time_config()
        room.config.setup_duration = int(latest.get("setup_duration", room.config.setup_duration))
        room.config.question_duration = int(latest.get("question_duration", room.config.question_duration))
        room.config.answer_duration = int(latest.get("answer_duration", room.config.answer_duration))
        room.config.vote_duration = int(latest.get("vote_duration", room.config.vote_duration))
        room.config.reveal_delay = int(latest.get("reveal_delay", room.config.reveal_delay))

    def _resolve_duration(self, default_seconds: int, env_key: str) -> int:
        """解析测试环境的阶段时长覆盖配置。"""
        if os.getenv("APP_ENV", "").strip().lower() != "test":
            return default_seconds

        raw = os.getenv(env_key, "").strip()
        if not raw:
            return default_seconds

        try:
            seconds = int(raw)
        except ValueError:
            return default_seconds
        return seconds if seconds > 0 else default_seconds

    def _cancel_timer(self, room_id: str):
        """取消房间正在运行的定时器。"""
        if room_id in self._timers:
            task = self._timers[room_id]
            if not task.done():
                task.cancel()
            del self._timers[room_id]

    def _start_timer(self, room_id: str, coro):
        """启动新的定时器，先取消旧的。"""
        self._cancel_timer(room_id)
        self._timers[room_id] = asyncio.create_task(coro)

    def _get_role_count(self, player: GamePlayer, role: str) -> int:
        """获取玩家在指定角色的历史担任次数。"""
        if role == "interrogator":
            return max(0, int(player.times_as_interrogator or 0))
        return max(0, int(player.times_as_subject or 0))

    def _resolve_role_balance_settings(self, room_config: Any | None) -> dict[str, int]:
        """解析并裁剪角色伪随机保底参数。"""
        resolved: dict[str, int] = {}
        for key, default in self.ROLE_BALANCE_DEFAULTS.items():
            min_val, max_val = self.ROLE_BALANCE_LIMITS[key]
            raw = getattr(room_config, f"role_{key}", default) if room_config else default
            try:
                parsed = int(raw)
            except Exception:
                parsed = default
            resolved[key] = max(min_val, min(max_val, parsed))
        return resolved

    def _choose_player_with_pity(
        self,
        players: list[GamePlayer],
        role: str,
        settings: dict[str, int],
        exclude_player_id: str | None = None,
    ) -> GamePlayer:
        """按“伪随机 + 保底”机制选择玩家。

        规则：
        1) 若最高次数与最低次数差值 >= 2，触发硬保底，仅在最低次数池中随机。
        2) 否则使用加权随机，历史次数越少，权重越高。
        """
        candidates = [
            player
            for player in players
            if exclude_player_id is None or str(player.id) != exclude_player_id
        ]
        if not candidates:
            raise ValueError("没有可用的候选玩家")

        counts = [self._get_role_count(player, role) for player in candidates]
        min_count = min(counts)
        max_count = max(counts)
        pity_gap_threshold = settings["pity_gap_threshold"]

        # 差值达到阈值时启动硬保底，避免长期抽不到角色。
        if max_count - min_count >= pity_gap_threshold:
            pity_pool = [
                player for player in candidates if self._get_role_count(player, role) == min_count
            ]
            return random.choice(pity_pool)

        weight_base = settings["weight_base"]
        weight_deficit_step = settings["weight_deficit_step"]
        weight_zero_bonus = settings["weight_zero_bonus"]
        weights: list[int] = []
        for player in candidates:
            count = self._get_role_count(player, role)
            deficit = max_count - count
            weight = weight_base + deficit * weight_deficit_step
            if count == 0:
                weight += weight_zero_bonus
            weights.append(weight)
        return random.choices(candidates, weights=weights, k=1)[0]

    def _select_round_roles(
        self,
        players: list[GamePlayer],
        room_config: Any | None = None,
    ) -> tuple[GamePlayer, GamePlayer]:
        """选择本轮提问者和被测者。"""
        if len(players) < 2:
            raise ValueError("玩家数不足")

        settings = self._resolve_role_balance_settings(room_config)
        interrogator = self._choose_player_with_pity(players, role="interrogator", settings=settings)
        subject = self._choose_player_with_pity(
            players,
            role="subject",
            settings=settings,
            exclude_player_id=str(interrogator.id),
        )
        return interrogator, subject

    async def _mark_role_usage(self, interrogator: GamePlayer, subject: GamePlayer):
        """记录本轮角色分配次数，用于后续伪随机保底。"""
        interrogator.times_as_interrogator = int(interrogator.times_as_interrogator or 0) + 1
        subject.times_as_subject = int(subject.times_as_subject or 0) + 1
        await interrogator.save()
        await subject.save()

    async def start_game(self, room_id: str) -> dict[str, Any]:
        """开始游戏。
    
        Returns:
            {"success": True} 或 {"success": False, "error": "..."}
        """
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return {"success": False, "error": "房间不存在"}
    
        if room.phase != "waiting":
            return {"success": False, "error": "游戏已经开始"}
    
        # 检查玩家数（使用 6 位房间码查询）
        players = await game_room_service.get_players_in_room(room.room_id)
        if len(players) < room.config.min_players:
            return {"success": False, "error": f"需要至少 {room.config.min_players} 名玩家"}

        # 开始前按当前玩家数动态锁定总回合数（玩家数 * 2）。
        room.total_rounds = game_room_service.resolve_total_rounds_by_player_count(
            len(players),
            fallback=room.total_rounds,
        )
        room.config.rounds_per_game = room.total_rounds
        # 开始前同步系统配置中的阶段时长，避免房间创建后改配置不生效。
        await self._sync_room_time_config(room)
    
        # 更新房间状态为 SETUP
        room.phase = "setup"
        room.current_round = 0
        room.started_at = datetime.now(timezone.utc)
        await room.save()

        setup_duration = self._resolve_duration(room.config.setup_duration, "TEST_GAME_SETUP_DURATION")

        # 通知所有玩家（使用 MongoDB ObjectId）
        await sse_manager.publish(room_id, "game_starting", {
            "countdown": setup_duration,
            "phase": "setup",
            "started_at": room.started_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        })

        # 启动灵魂注入倒计时
        self._start_timer(room_id, self._start_setup_timer(room_id, setup_duration))
        return {"success": True}

    async def _start_setup_timer(self, room_id: str, setup_time: int | None = None):
        """启动灵魂注入阶段倒计时。"""
        try:
            room = await game_room_service.get_room_by_id(room_id)
            if not room:
                return

            if setup_time is None:
                setup_time = self._resolve_duration(room.config.setup_duration, "TEST_GAME_SETUP_DURATION")
            started_at = room.started_at
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"启动灵魂注入倒计时，房间 {room_id}，时长 {setup_time} 秒")

            # 倒计时通知（每秒更新）
            for remaining in range(setup_time, 0, -1):
                logger.info(f"发送 countdown 事件：{remaining} 秒")
                # 使用 ISO 格式 UTC 时间，确保前端能正确解析
                await sse_manager.publish(room_id, "countdown", {
                    "remaining": remaining,
                    "phase": "setup",
                    "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if started_at else None,
                })
                await asyncio.sleep(1)

            logger.info(f"灵魂注入倒计时结束，房间 {room_id}")

            await asyncio.sleep(1)

            # 时间到，进入游戏
            await self._start_first_round(room_id)
        except asyncio.CancelledError:
            # 定时器被取消，正常退出
            pass

    async def _start_first_round(self, room_id: str):
        """开始第一轮。"""
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return

        players = await game_room_service.get_players_in_room(room.room_id)
        if len(players) < 2:
            await sse_manager.publish(room_id, "game_error", {"error": "玩家数不足"})
            await self._end_game(room_id)
            return

        # 更新房间状态
        room.phase = "playing"
        room.current_round = 1
        await room.save()

        # 使用伪随机保底机制选择提问者和被测者
        interrogator, subject = self._select_round_roles(players, room.config)

        # 创建回合记录（使用 6 位房间码 room_id，而非 MongoDB ObjectId）
        game_round = GameRound(
            room_id=room.room_id,
            round_number=1,
            interrogator_id=str(interrogator.id),
            subject_id=str(subject.id),
            status="questioning",
        )
        await game_round.insert()
        await self._mark_role_usage(interrogator, subject)

        # 通知所有玩家游戏开始（包含回合信息，前端收到后直接跳转并显示）
        question_duration = self._resolve_duration(room.config.question_duration, "TEST_GAME_QUESTION_DURATION")
        await sse_manager.publish(room_id, "game_start", {
            "room_id": room_id,
            "round_id": str(game_round.id),
            "round_number": 1,
            "total_rounds": room.total_rounds,
            "interrogator_id": str(interrogator.id),
            "interrogator_nickname": interrogator.nickname,
            "subject_id": str(subject.id),
            "subject_nickname": subject.nickname,
            "question_time": question_duration,
        })

        # 启动提问倒计时
        self._start_timer(room_id, self._start_question_timer(room_id, str(game_round.id)))

    async def _start_question_timer(self, room_id: str, round_id: str):
        """启动提问阶段倒计时。"""
        try:
            room = await game_room_service.get_room_by_id(room_id)
            if not room:
                return

            question_time = self._resolve_duration(room.config.question_duration, "TEST_GAME_QUESTION_DURATION")

            # 倒计时通知（每秒更新）
            for remaining in range(question_time, 0, -1):
                await sse_manager.publish(room_id, "countdown", {"remaining": remaining, "phase": "questioning"})
                await asyncio.sleep(1)

            # 检查是否已提交问题
            game_round = await GameRound.get(PydanticObjectId(round_id))
            if game_round and not game_round.question:
                # 时间到但没提交问题，随机生成一个默认问题
                default_questions = [
                    "请介绍一下你自己",
                    "你今天做了什么?",
                    "你喜欢什么颜色?",
                    "你多大了?",
                    "你喜欢吃什么?",
                ]
                game_round.question = random.choice(default_questions)
                game_round.question_at = datetime.now(timezone.utc)
                await game_round.save()

            # 进入回答阶段
            await self._start_answer_phase(room_id, round_id)
        except asyncio.CancelledError:
            # 定时器被取消，正常退出
            pass

    async def submit_question(self, room_id: str, round_id: str, player_id: str, question: str) -> dict[str, Any]:
        """提交问题。

        Args:
            room_id: 房间 ID
            round_id: 回合 ID
            player_id: 提问玩家 ID
            question: 问题内容

        Returns:
            {"success": True} 或 {"success": False, "error": "..."}
        """
        game_round = await GameRound.get(PydanticObjectId(round_id))
        if not game_round:
            return {"success": False, "error": "回合不存在"}

        if game_round.interrogator_id != player_id:
            return {"success": False, "error": "只有提问者可以提交问题"}

        if game_round.question:
            return {"success": False, "error": "问题已提交"}

        game_round.question = question
        game_round.question_at = datetime.now(timezone.utc)
        await game_round.save()

        # 通知所有玩家
        await sse_manager.publish(room_id, "new_question", {
            "question": question,
            "interrogator_id": player_id,
        })

        # 进入回答阶段
        await self._start_answer_phase(room_id, round_id)

        return {"success": True}

    async def _start_answer_phase(self, room_id: str, round_id: str):
        """开始回答阶段。"""
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return

        game_round = await GameRound.get(PydanticObjectId(round_id))
        if not game_round:
            return

        game_round.status = "answering"
        await game_round.save()

        # 通知被测者
        answer_duration = self._resolve_duration(room.config.answer_duration, "TEST_GAME_ANSWER_DURATION")
        await sse_manager.publish(room_id, "answer_phase", {
            "round_id": round_id,
            "subject_id": game_round.subject_id,
            "question": game_round.question,
            "answer_time": answer_duration,
        })

        # 启动回答倒计时
        self._start_timer(room_id, self._start_answer_timer(room_id, round_id))

    async def _start_answer_timer(self, room_id: str, round_id: str):
        """启动回答阶段倒计时。"""
        try:
            room = await game_room_service.get_room_by_id(room_id)
            if not room:
                return

            answer_time = self._resolve_duration(room.config.answer_duration, "TEST_GAME_ANSWER_DURATION")

            # 倒计时通知（每秒更新）
            for remaining in range(answer_time, 0, -1):
                await sse_manager.publish(room_id, "countdown", {"remaining": remaining, "phase": "answering"})
                await asyncio.sleep(1)
            # 检查是否已提交回答
            game_round = await GameRound.get(PydanticObjectId(round_id))
            if game_round and not game_round.answer:
                # 时间到但没提交回答，使用默认回答
                game_round.answer = "（未作答）"
                game_round.answer_type = "human"
                game_round.answer_submitted_at = datetime.now(timezone.utc)
                await game_round.save()

            if not game_round:
                return

            # 倒计时结束后统一进入随机“输入中”展示期，避免根据提交快慢推断回答类型。
            display_delay = await ai_chat_service.calculate_display_delay(
                answer_type=str(game_round.answer_type or ""),
                submit_time_seconds=0.0,
            )
            await sse_manager.publish(room_id, "answer_submitted", {
                "display_delay": display_delay,
            })

            # 延迟后显示回答，再进入投票阶段。
            asyncio.create_task(self._delayed_answer_display(room_id, round_id, display_delay))
        except asyncio.CancelledError:
            # 定时器被取消，正常退出
            pass

    async def submit_answer(
            self,
            room_id: str,
            round_id: str,
            player_id: str,
            answer_type: str,
            answer_content: str = "",
        ) -> dict[str, Any]:
        """提交回答。
    
        Args:
            room_id: 房间 ID
            round_id: 回合 ID
            player_id: 被测者 ID
            answer_type: "human" 或 "ai"
            answer_content: 手动回答的内容（当 answer_type 为 "human" 时需要）
    
        Returns:
            {"success": True} 或 {"success": False, "error": "..."}
        """
        game_round = await GameRound.get(PydanticObjectId(round_id))
        if not game_round:
            return {"success": False, "error": "回合不存在"}
    
        if game_round.subject_id != player_id:
            return {"success": False, "error": "只有被测者可以提交回答"}
    
        if game_round.answer:
            return {"success": False, "error": "回答已提交"}
    
        # 获取房间以获取正确的 room_id（6位码）
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return {"success": False, "error": "房间不存在"}
    
        # 根据类型处理回答
        if answer_type == "ai":
            # 调用 AI 生成回答
            # 使用 6 位房间码查询玩家
            players = await game_room_service.get_players_in_room(room.room_id)
            subject = next(p for p in players if str(p.id) == player_id)
    
            result = await ai_chat_service.call_ai(
                system_prompt=subject.system_prompt or "你是一个有趣的人",
                user_message=game_round.question,
                model_id=subject.ai_model_id,
            )
    
            if not result["success"]:
                return {"success": False, "error": result.get("error", "AI 调用失败")}

            game_round.answer = result["content"]
            game_round.answer_type = "ai"
            game_round.used_ai_model_id = subject.ai_model_id
        else:
            # 手动回答
            if not answer_content.strip():
                return {"success": False, "error": "回答内容不能为空"}
            game_round.answer = answer_content
            game_round.answer_type = "human"

        game_round.answer_submitted_at = datetime.now(timezone.utc)
        await game_round.save()
        # 注意：回答阶段倒计时必须走完；随机“输入中”展示由倒计时结束后统一触发。
        return {"success": True}

    async def _delayed_answer_display(self, room_id: str, round_id: str, delay: float):
        """延迟显示回答。"""
        await asyncio.sleep(delay)

        game_round = await GameRound.get(PydanticObjectId(round_id))
        if not game_round:
            return

        game_round.answer_displayed_at = datetime.now(timezone.utc)
        await game_round.save()

        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return

        # 通知显示回答（不发送 answer_type，双方都无法看到是否是 AI 回答）
        await sse_manager.publish(room_id, "new_answer", {
            "answer": game_round.answer,
            "subject_id": game_round.subject_id,
        })

        # 进入投票阶段
        await self._start_voting_phase(room_id, round_id)

    async def _start_voting_phase(self, room_id: str, round_id: str):
        """开始投票阶段。"""
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return

        game_round = await GameRound.get(PydanticObjectId(round_id))
        if not game_round:
            return

        # 避免重复进入投票/结算阶段。
        if game_round.status in {"voting", "revealed"}:
            return

        game_round.status = "voting"
        await game_round.save()

        # 通知所有玩家投票
        vote_duration = self._resolve_duration(room.config.vote_duration, "TEST_GAME_VOTE_DURATION")
        await sse_manager.publish(room_id, "voting_phase", {
            "round_id": round_id,
            "vote_time": vote_duration,
        })

        # 启动投票倒计时
        self._start_timer(room_id, self._start_vote_timer(room_id, round_id))

    async def _start_vote_timer(self, room_id: str, round_id: str):
        """启动投票阶段倒计时。"""
        try:
            room = await game_room_service.get_room_by_id(room_id)
            if not room:
                return

            vote_time = self._resolve_duration(room.config.vote_duration, "TEST_GAME_VOTE_DURATION")

            # 倒计时通知（每秒更新）
            for remaining in range(vote_time, 0, -1):
                await sse_manager.publish(room_id, "countdown", {"remaining": remaining, "phase": "voting"})
                await asyncio.sleep(1)
            # 结算本回合
            await self._settle_round(room_id, round_id)
        except asyncio.CancelledError:
            # 定时器被取消，正常退出
            pass

    async def submit_vote(
        self,
        room_id: str,
        round_id: str,
        player_id: str,
        vote: str,
    ) -> dict[str, Any]:
        """提交投票。

        Args:
            room_id: 房间 ID（ObjectId 字符串）
            round_id: 回合 ID
            player_id: 投票玩家 ID
            vote: "human", "ai" 或 "skip"

        Returns:
            {"success": True} 或 {"success": False, "error": "..."}
        """
        # 获取房间以取得正确的 room_id（6位码）
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return {"success": False, "error": "房间不存在"}

        game_round = await GameRound.get(PydanticObjectId(round_id))
        if not game_round:
            return {"success": False, "error": "回合不存在"}

        if game_round.status != "voting":
            return {"success": False, "error": "当前不在投票阶段"}

        # 检查是否是陪审团成员（除被测者外）
        if player_id == game_round.subject_id:
            return {"success": False, "error": "被测者不能投票"}

        # 检查是否已投票（使用6位房间码）
        existing = await VoteRecord.find_one({
            "room_id": room.room_id,
            "round_number": game_round.round_number,
            "voter_id": player_id,
        })
        if existing:
            return {"success": False, "error": "已投票"}

        # 记录投票（使用6位房间码）
        vote_record = VoteRecord(
            room_id=room.room_id,
            round_number=game_round.round_number,
            voter_id=player_id,
            vote=vote,
        )
        await vote_record.insert()

        # 通知（使用 ObjectId 作为 room_id 发布事件）
        await sse_manager.publish(str(room.id), "vote_submitted", {
            "voter_id": player_id,
        })

        return {"success": True}

    async def _settle_round(self, room_id: str, round_id: str):
        """结算回合。"""
        game_round = await GameRound.get(PydanticObjectId(round_id))
        if not game_round:
            return
    
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return
    
        # 获取所有投票（使用6位房间码查询，因为 VoteRecord 存储的是 room.room_id）
        votes = await VoteRecord.find({
            "room_id": room.room_id,
            "round_number": game_round.round_number,
        }).to_list()
    
        # 计算得分
        scores = self._calculate_scores(game_round, votes)
    
        # 更新玩家得分
        players = await game_room_service.get_players_in_room(room.room_id)
        for player in players:
            player_score = scores.get(str(player.id), 0)
            player.total_score = (player.total_score or 0) + player_score
            await player.save()
        # 更新回合状态
        game_round.status = "revealed"
        await game_round.save()

        # 统计投票结果
        vote_stats = {"human": 0, "ai": 0, "skip": 0}
        vote_details: list[dict[str, Any]] = []
        for vote in votes:
            vote_stats[vote.vote] += 1
            # 判断是否正确
            vote.is_correct = (vote.vote == game_round.answer_type)
            await vote.save()
            vote_details.append(
                {
                    "voter_id": vote.voter_id,
                    "vote": vote.vote,
                    "is_correct": bool(vote.is_correct),
                    "score_delta": scores.get(vote.voter_id, 0),
                }
            )

        # 通知结果
        # 构建玩家得分信息（包含昵称）
        player_scores_list = [
            {
                "id": str(p.id),
                "nickname": p.nickname,
                "score": p.total_score
            }
            for p in players
        ]
        await sse_manager.publish(room_id, "round_result", {
            "round_number": game_round.round_number,
            "interrogator_id": game_round.interrogator_id,
            "subject_id": game_round.subject_id,
            "subject_choice": game_round.answer_type,
            "question": game_round.question,
            "answer": game_round.answer,
            "votes": vote_stats,
            "scores": scores,
            "vote_details": vote_details,
            "player_scores": player_scores_list,
        })

        # 等待配置中的揭晓时长后开始下一轮/结束游戏。
        reveal_delay = self._resolve_duration(room.config.reveal_delay, "TEST_GAME_REVEAL_DELAY")
        await asyncio.sleep(reveal_delay)

        # 判断是否结束游戏
        if game_round.round_number >= room.total_rounds:
            await self._end_game(room_id)
        else:
            await self._next_round(room_id)

    def _calculate_scores(self, game_round: GameRound, votes: list[VoteRecord]) -> dict[str, int]:
        """计算本回合得分。"""
        scores: dict[str, int] = {}

        # 新规则：所有“投票玩家”（提问者 + 陪审团）都按投票结果计分；被测者不计分。
        for vote in votes:
            if vote.voter_id == game_round.subject_id:
                continue
            if vote.vote == "skip":
                continue
            if vote.vote == game_round.answer_type:
                scores[vote.voter_id] = 50
            else:
                scores[vote.voter_id] = -30
        return scores

    async def _next_round(self, room_id: str):
        """开始下一轮。"""
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return
    
        players = await game_room_service.get_players_in_room(room.room_id)
        if len(players) < 2:
            await sse_manager.publish(room_id, "game_error", {"error": "玩家数不足"})
            await self._end_game(room_id)
            return
    
        # 更新房间状态
        room.current_round += 1
        await room.save()
    
        # 使用伪随机保底机制选择提问者和被测者
        interrogator, subject = self._select_round_roles(players, room.config)
    
        # 创建回合记录
        game_round = GameRound(
            room_id=room.room_id,
            round_number=room.current_round,
            interrogator_id=str(interrogator.id),
            subject_id=str(subject.id),
            status="questioning",
        )
        await game_round.insert()
        await self._mark_role_usage(interrogator, subject)
    
        # 通知所有玩家
        question_duration = self._resolve_duration(room.config.question_duration, "TEST_GAME_QUESTION_DURATION")
        await sse_manager.publish(room_id, "new_round", {
            "round_id": str(game_round.id),
            "round_number": room.current_round,
            "total_rounds": room.total_rounds,
            "interrogator_id": str(interrogator.id),
            "interrogator_nickname": interrogator.nickname,
            "subject_id": str(subject.id),
            "subject_nickname": subject.nickname,
            "question_time": question_duration,
        })

        # 启动提问倒计时
        self._start_timer(room_id, self._start_question_timer(room_id, str(game_round.id)))

    async def _end_game(self, room_id: str):
        """结束游戏。"""
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return
    
        # 更新房间状态
        room.phase = "finished"
        await room.save()
    
        # 获取玩家得分
        players = await game_room_service.get_players_in_room(room.room_id)
        leaderboard = sorted(
            [{"id": str(p.id), "nickname": p.nickname, "score": p.total_score} for p in players],
            key=lambda x: x["score"],
            reverse=True,
        )
    
        # 统计成就
        achievements = self._calculate_achievements(players)
        # 通知游戏结束
        await sse_manager.publish(room_id, "game_over", {
            "leaderboard": leaderboard,
            "achievements": achievements,
        })

    def _calculate_achievements(self, players: list[GamePlayer]) -> dict[str, Any]:
        """计算成就。"""
        if not players:
            return {}
    
        max_score = max(p.total_score for p in players)
        winners = [p for p in players if p.total_score == max_score]
    
        return {
            "winners": [{"id": str(p.id), "nickname": p.nickname} for p in winners],
            "max_score": max_score,
        }

# 全局游戏管理器
game_manager = GameManager()
