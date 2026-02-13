"""游戏状态管理器 - 处理游戏核心逻辑、状态机和实时事件。"""

from __future__ import annotations

import asyncio
import json
import random
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncIterator

from beanie import PydanticObjectId

from app.models.game_room import GameRoom
from app.models.game_player import GamePlayer
from app.models.game_round import GameRound
from app.models.vote_record import VoteRecord
from app.services import ai_chat_service, game_room_service


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

    def __init__(self):
        self._timers: dict[str, asyncio.Task] = {}

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
    
        # 更新房间状态为 SETUP
        room.phase = "setup"
        room.current_round = 0
        room.started_at = datetime.now(timezone.utc)
        await room.save()

        # 通知所有玩家（使用 MongoDB ObjectId）
        await sse_manager.publish(room_id, "game_starting", {
            "countdown": room.config.setup_duration,
            "phase": "setup",
            "started_at": room.started_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        })

        # 启动灵魂注入倒计时
        await self._start_setup_timer(room_id)
        return {"success": True}

    async def _start_setup_timer(self, room_id: str):
        """启动灵魂注入阶段倒计时。"""
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return

        setup_time = room.config.setup_duration
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

        # 随机选择提问者和被测者
        random.shuffle(players)
        interrogator = players[0]
        subject = players[1]

        # 创建回合记录（使用 6 位房间码 room_id，而非 MongoDB ObjectId）
        game_round = GameRound(
            room_id=room.room_id,
            round_number=1,
            interrogator_id=str(interrogator.id),
            subject_id=str(subject.id),
            status="questioning",
        )
        await game_round.insert()

        # 通知所有玩家游戏开始（包含回合信息，前端收到后直接跳转并显示）
        await sse_manager.publish(room_id, "game_start", {
            "room_id": room_id,
            "round_id": str(game_round.id),
            "round_number": 1,
            "interrogator_id": str(interrogator.id),
            "interrogator_nickname": interrogator.nickname,
            "subject_id": str(subject.id),
            "subject_nickname": subject.nickname,
            "question_time": room.config.question_duration,
        })

        # 启动提问倒计时
        await self._start_question_timer(room_id, str(game_round.id))

    async def _start_question_timer(self, room_id: str, round_id: str):
        """启动提问阶段倒计时。"""
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return

        question_time = room.config.question_duration

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
        await sse_manager.publish(room_id, "answer_phase", {
            "round_id": round_id,
            "subject_id": game_round.subject_id,
            "question": game_round.question,
            "answer_time": room.config.answer_duration,
        })

        # 启动回答倒计时
        await self._start_answer_timer(room_id, round_id)

    async def _start_answer_timer(self, room_id: str, round_id: str):
        """启动回答阶段倒计时。"""
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return

        answer_time = room.config.answer_duration

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

        # 进入投票阶段
        await self._start_voting_phase(room_id, round_id)

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
            {"success": True, "display_delay": float} 或 {"success": False, "error": "..."}
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
    
        # 计算提交时间
        submit_time = 0
        if game_round.question_at:
            question_at = game_round.question_at
            # 如果 question_at 没有时区信息，假设为 UTC
            if question_at.tzinfo is None:
                question_at = question_at.replace(tzinfo=timezone.utc)
            submit_time = (datetime.now(timezone.utc) - question_at).total_seconds()
    
        # 获取延迟
        display_delay = await ai_chat_service.calculate_display_delay(answer_type, submit_time)
    
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

        # 通知所有玩家回答已提交（显示"正在输入..."）
        await sse_manager.publish(room_id, "answer_submitted", {
            "display_delay": display_delay,
        })

        # 延迟后显示回答
        asyncio.create_task(self._delayed_answer_display(room_id, round_id, display_delay))

        return {"success": True, "display_delay": display_delay}

    async def _delayed_answer_display(self, room_id: str, round_id: str, delay: float):
        """延迟显示回答。"""
        await asyncio.sleep(delay)

        game_round = await GameRound.get(PydanticObjectId(round_id))
        if not game_round:
            return

        game_round.answer_displayed_at = datetime.now(timezone.utc)
        game_round.status = "voting"
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

        game_round.status = "voting"
        await game_round.save()

        # 通知所有玩家投票
        await sse_manager.publish(room_id, "voting_phase", {
            "round_id": round_id,
            "vote_time": room.config.vote_duration,
        })

        # 启动投票倒计时
        await self._start_vote_timer(room_id, round_id)

    async def _start_vote_timer(self, room_id: str, round_id: str):
        """启动投票阶段倒计时。"""
        room = await game_room_service.get_room_by_id(room_id)
        if not room:
            return

        vote_time = room.config.vote_duration

        # 倒计时通知（每秒更新）
        for remaining in range(vote_time, 0, -1):
            await sse_manager.publish(room_id, "countdown", {"remaining": remaining, "phase": "voting"})
            await asyncio.sleep(1)
        # 结算本回合
        await self._settle_round(room_id, round_id)

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
        await sse_manager.publish(room_id, "vote_submitted", {
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
            player.total_score = player.total_score + player_score
            await player.save()
        # 更新回合状态
        game_round.status = "revealed"
        await game_round.save()

        # 统计投票结果
        vote_stats = {"human": 0, "ai": 0, "skip": 0}
        for vote in votes:
            vote_stats[vote.vote] += 1
            # 判断是否正确
            vote.is_correct = (vote.vote == game_round.answer_type)
            await vote.save()

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
            "subject_choice": game_round.answer_type,
            "question": game_round.question,
            "answer": game_round.answer,
            "votes": vote_stats,
            "scores": scores,
            "player_scores": player_scores_list,
        })

        # 等待几秒后开始下一轮
        await asyncio.sleep(5)

        # 判断是否结束游戏
        if game_round.round_number >= room.total_rounds:
            await self._end_game(room_id)
        else:
            await self._next_round(room_id)

    def _calculate_scores(self, game_round: GameRound, votes: list[VoteRecord]) -> dict[str, int]:
        """计算本回合得分。"""
        scores = {}
        subject_id = game_round.subject_id

        # 计算陪审团得分
        for vote in votes:
            if vote.vote == "skip":
                continue

            is_correct = (vote.vote == game_round.answer_type)
            if is_correct:
                scores[vote.voter_id] = scores.get(vote.voter_id, 0) + 50
            else:
                scores[vote.voter_id] = scores.get(vote.voter_id, 0) - 30

        # 计算被测者得分（只有使用了 AI 才计算欺骗分）
        if game_round.answer_type == "ai":
            wrong_votes = sum(1 for v in votes if v.vote == "human")
            # 每骗一个人 +100 分
            scores[subject_id] = scores.get(subject_id, 0) + wrong_votes * 100

            # 完美伪装奖励：如果所有人都猜错了（不算skip）
            non_skip_votes = [v for v in votes if v.vote != "skip"]
            if non_skip_votes and all(v.vote == "human" for v in non_skip_votes):
                scores[subject_id] = scores.get(subject_id, 0) + 200

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
    
        # 随机选择提问者和被测者（轮换）
        random.shuffle(players)
        interrogator = players[0]
        subject = players[1]
    
        # 创建回合记录
        game_round = GameRound(
            room_id=room.room_id,
            round_number=room.current_round,
            interrogator_id=str(interrogator.id),
            subject_id=str(subject.id),
            status="questioning",
        )
        await game_round.insert()
    
        # 通知所有玩家
        await sse_manager.publish(room_id, "new_round", {
            "round_id": str(game_round.id),
            "round_number": room.current_round,
            "interrogator_id": str(interrogator.id),
            "interrogator_nickname": interrogator.nickname,
            "subject_id": str(subject.id),
            "subject_nickname": subject.nickname,
            "question_time": room.config.question_duration,
        })

        # 启动提问倒计时
        await self._start_question_timer(room_id, str(game_round.id))

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
