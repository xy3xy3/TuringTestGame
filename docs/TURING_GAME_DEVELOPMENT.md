# 图灵测试游戏开发计划

## 一、 技术栈与架构概览

### 1.1 技术选型

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| 后端 | FastAPI | 复用现有 PyFastAdmin 框架 |
| 数据库 | MongoDB + Beanie | 复用现有 ODM |
| 前端 | HTMX + Alpine.js + Tailwind CSS | 复用现有前端技术栈 |
| AI 调用 | OpenAI SDK (兼容 OpenAI API 协议) | 支持自定义 Base URL，可接入任意兼容 API |
| 实时通信 | Server-Sent Events (SSE) | 用于游戏状态实时推送 |

### 1.2 项目结构（新增模块）

```
app/
├── models/
│   ├── game_room.py         # 游戏房间模型
│   ├── game_player.py       # 玩家模型
│   ├── ai_model.py         # AI 模型配置模型
│   ├── game_round.py       # 游戏回合模型
│   ├── vote_record.py      # 投票记录模型
│   └── __init__.py
├── services/
│   ├── game_service.py     # 游戏核心逻辑服务
│   ├── ai_service.py      # AI 调用服务
│   ├── game_room_service.py # 房间管理服务
│   └── __init__.py
├── apps/
│   ├── game/               # 游戏前台模块（免登录）
│   │   ├── __init__.py
│   │   ├── routes.py       # 游戏路由
│   │   └── templates/      # 游戏前端模板
│   │       ├── lobby.html       # 大厅页
│   │       ├── setup.html       # 灵魂注入页
│   │       ├── game.html        # 游戏进行页
│   │       └── result.html      # 结果页
│   └── admin/
│       └── controllers/
│           ├── ai_models.py     # AI 模型管理控制器
│           └── game_rooms.py   # 游戏房间管理控制器（可选）
```

---

## 二、 数据库模型设计

### 2.1 核心模型概览

| 模型名 | Collection | 说明 |
|--------|------------|------|
| `AIModel` | `ai_models` | AI 模型配置（Base URL、API Key、模型名、启用状态） |
| `GameRoom` | `game_rooms` | 游戏房间（房间号、密码、状态、玩家列表、配置） |
| `GamePlayer` | `game_players` | 玩家（昵称、系统提示词、选择的模型、得分） |
| `GameRound` | `game_rounds` | 游戏回合（轮次、提问者、被测者、问题、回答、延迟） |
| `VoteRecord` | `vote_records` | 投票记录（玩家ID、回合ID、投票选项、是否正确） |

---

### 2.2 AI 模型配置表 (`AIModel`)

```python
class AIModel(Document):
    """AI 模型配置。"""

    name: str = Field(..., min_length=2, max_length=64)           # 显示名称，如 "OpenAI GPT-4o"
    base_url: str = Field(..., min_length=8, max_length=256)      # API Base URL，如 "https://api.openai.com/v1"
    api_key: str = Field(..., min_length=8, max_length=256)        # API Key
    model_name: str = Field(..., min_length=1, max_length=64)      # 模型名称，如 "gpt-4o-mini"
    temperature: float = Field(default=0.8, ge=0.0, le=2.0)       # 随机性参数
    max_tokens: int = Field(default=500, ge=100, le=2000)        # 最大token数
    is_enabled: bool = Field(default=True)                         # 是否启用
    is_default: bool = Field(default=False)                        # 是否为默认模型
    description: str = Field(default="", max_length=256)         # 描述/备注
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "ai_models"
        indexes = [
            IndexModel([("name", 1)], unique=True, name="uniq_ai_model_name"),
            IndexModel([("is_enabled", 1)], name="idx_ai_model_enabled"),
        ]
```

**字段说明**：

- `name`: 管理员可读的显示名称
- `base_url`: 支持任意 OpenAI 兼容 API（如 OpenAI、Azure OpenAI、硅基流动、DeepSeek 等）
- `api_key`: 调用 API 所需的密钥
- `model_name`: 具体模型标识符
- `temperature`: 控制输出随机性，游戏场景建议 0.7-1.0
- `is_enabled`: 控制是否在玩家选择列表中显示
- `is_default`: 玩家加入时默认选中的模型

---

### 2.3 游戏房间表 (`GameRoom`)

```python
class GameRoom(Document):
    """游戏房间。"""

    room_id: str = Field(..., min_length=6, max_length=16, pattern=ROOM_ID_PATTERN)  # 房间号，如 "ABC123"
    password: str = Field(default="", max_length=32)                                  # 房间密码（可为空）
    owner_id: str = Field(..., max_length=32)                                           # 房主Player ID
    status: Literal["waiting", "setup", "playing", "finished"] = "waiting"            # 房间状态
    config: GameConfig = Field(default_factory=GameConfig)                            # 游戏配置
    
    player_ids: list[str] = Field(default_factory=list)                                # 玩家ID列表
    current_round: int = Field(default=0)                                             # 当前回合（0=未开始）
    total_rounds: int = Field(default=4)                                              # 总回合数
    
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None                                                 # 游戏开始时间
    finished_at: datetime | None = None                                                # 游戏结束时间

    class Settings:
        name = "game_rooms"
        indexes = [
            IndexModel([("room_id", 1)], unique=True, name="uniq_room_id"),
            IndexModel([("status", 1)], name="idx_room_status"),
        ]


class GameConfig(BaseModel):
    """游戏配置。"""

    setup_duration: int = Field(default=60, ge=30, le=300)     # 灵魂注入阶段时长（秒）
    question_duration: int = Field(default=30, ge=15, le=60)   # 提问阶段时长（秒）
    answer_duration: int = Field(default=45, ge=20, le=90)     # 回答阶段时长（秒）
    vote_duration: int = Field(default=15, ge=10, le=30)      # 投票阶段时长（秒）
    reveal_delay: int = Field(default=3, ge=1, le=10)         # 揭示答案的延迟（秒）
    
    min_players: int = Field(default=2, ge=2, le=16)          # 最少玩家数
    max_players: int = Field(default=8, ge=2, le=16)          # 最多玩家数
    rounds_per_game: int = Field(default=0, ge=0)              # 0表示等于玩家数
```

---

### 2.4 玩家表 (`GamePlayer`)

```python
class GamePlayer(Document):
    """游戏玩家。"""

    player_id: str = Field(..., max_length=32)                    # 玩家唯一ID（UUID或简短ID）
    room_id: str = Field(..., max_length=16)                      # 所属房间ID
    nickname: str = Field(..., min_length=2, max_length=32)      # 玩家昵称
    
    # 灵魂注入阶段设置
    system_prompt: str = Field(default="", max_length=2000)      # 系统提示词
    ai_model_id: str | None = Field(default=None, max_length=32) # 选择的AI模型ID
    
    # 游戏状态
    is_ready: bool = Field(default=False)                         # 是否准备好
    is_online: bool = Field(default=True)                         # 是否在线
    
    # 得分统计
    total_score: int = Field(default=0)                          # 总得分
    deception_count: int = Field(default=0)                      # 成功欺骗次数
    correct_vote_count: int = Field(default=0)                   # 正确投票次数
    consecutive_correct: int = Field(default=0)                 # 连续猜对次数
    
    # 趣味数据
    times_as_interrogator: int = Field(default=0)                # 作为提问者次数
    times_as_subject: int = Field(default=0)                      # 作为被测者次数
    ai_used_count: int = Field(default=0)                        # 使用AI代答次数
    
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "game_players"
        indexes = [
            IndexModel([("player_id", 1)], unique=True, name="uniq_player_id"),
            IndexModel([("room_id", 1)], name="idx_player_room"),
        ]
```

---

### 2.5 游戏回合表 (`GameRound`)

```python
class GameRound(Document):
    """游戏回合。"""

    room_id: str = Field(..., max_length=16)                      # 房间ID
    round_number: int = Field(..., ge=1)                           # 回合编号
    
    # 本回合角色
    interrogator_id: str = Field(..., max_length=32)              # 提问者ID（Player A）
    subject_id: str = Field(..., max_length=32)                   # 被测者ID（Player B）
    
    # 阶段数据
    question: str = Field(default="", max_length=1000)            # 问题内容
    answer: str = Field(default="", max_length=2000)              # 回答内容
    answer_type: Literal["human", "ai"] = "human"                  # 回答类型（真人对答/AI代答）
    used_ai_model_id: str | None = Field(default=None)            # 如果是AI代答，使用的模型ID
    
    # 时间戳（用于延迟计算）
    question_at: datetime | None = None                           # 提问时间
    answer_submitted_at: datetime | None = None                   # 回答提交时间（玩家提交或AI生成完成）
    answer_displayed_at: datetime | None = None                   # 回答显示时间（含随机延迟）
    
    # 状态
    status: Literal["questioning", "answering", "voting", "revealed"] = "questioning"
    
    created_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "game_rounds"
        indexes = [
            IndexModel([("room_id", 1), ("round_number", 1)], unique=True, name="uniq_room_round"),
        ]
```

---

### 2.6 投票记录表 (`VoteRecord`)

```python
class VoteRecord(Document):
    """投票记录。"""

    room_id: str = Field(..., max_length=16)                      # 房间ID
    round_number: int = Field(..., ge=1)                          # 回合编号
    voter_id: str = Field(..., max_length=32)                     # 投票玩家ID
    
    # 投票内容
    vote: Literal["human", "ai", "skip"] = "skip"                 # 投票选项
    
    # 结算结果（回合结束后填充）
    is_correct: bool | None = Field(default=None)                  # 是否正确
    
    created_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "vote_records"
        indexes = [
            IndexModel([("room_id", 1), ("round_number", 1), ("voter_id", 1)], unique=True, name="uniq_vote"),
        ]
```

---

## 三、 游戏前台路由设计

### 3.1 免登录访问（游戏前台）

| 路由 | 方法 | 说明 |
|------|------|------|
| `/game` | GET | 游戏首页（可选择创建/加入房间） |
| `/game/create` | POST | 创建房间（参数：nickname, password, config） |
| `/game/join` | POST | 加入房间（参数：room_id, nickname, password） |
| `/game/{room_id}` | GET | 房间大厅（WebSocket/SSE连接） |
| `/game/{room_id}/setup` | GET/POST | 灵魂注入页面（设置系统提示词和AI模型） |
| `/game/{room_id}/ready` | POST | 准备/取消准备 |
| `/game/{room_id}/question` | POST | 提问者提交问题（Game Phase） |
| `/game/{room_id}/answer` | POST | 被测者提交回答（选择AI或手动输入） |
| `/game/{room_id}/vote` | POST | 投票（判断是人还是AI） |
| `/game/{room_id}/leave` | POST | 离开房间 |

### 3.2 SSE 实时事件

| 事件名 | 说明 | 载荷示例 |
|--------|------|----------|
| `player_joined` | 玩家加入 | `{"player_id": "xxx", "nickname": "yyy"}` |
| `player_left` | 玩家离开 | `{"player_id": "xxx"}` |
| `player_ready` | 玩家准备 | `{"player_id": "xxx", "is_ready": true}` |
| `game_starting` | 游戏即将开始 | `{"countdown": 60}` |
| `phase_change` | 阶段切换 | `{"phase": "setup\|questioning\|answering\|voting\|revealed", "data": {...}}` |
| `new_question` | 新问题 | `{"question": "...", "interrogator": "xxx"}` |
| `new_answer` | 新回答（含延迟） | `{"answer": "...", "answer_type": "ai\|human"}` |
| `round_result` | 回合结算 | `{"subject_choice": "ai\|human", "votes": {...}, "scores": {...}}` |
| `game_over` | 游戏结束 | `{"leaderboard": [...], "achievements": {...}}` |

---

## 四、 Admin 后台管理

### 4.1 AI 模型管理 (`/admin/ai-models`)

| 路由 | 方法 | 说明 |
|------|------|------|
| `/admin/ai-models` | GET | AI 模型列表 |
| `/admin/ai-models/new` | GET/POST | 新建 AI 模型 |
| `/admin/ai-models/{id}/edit` | GET/POST | 编辑 AI 模型 |
| `/admin/ai-models/{id}/delete` | POST/DELETE | 删除 AI 模型 |
| `/admin/ai-models/{id}/toggle` | POST | 启用/禁用 |

### 4.2 资源注册（Registry）

```json
// app/apps/admin/registry_generated/ai_models.json
{
  "group_key": "game",
  "node": {
    "key": "ai_models",
    "name": "AI 模型配置",
    "url": "/admin/ai-models",
    "actions": ["create", "read", "update", "delete"],
    "mode": "table"
  }
}
```

### 4.3 权限配置

| 资源 | 动作 | 说明 |
|------|------|------|
| `ai_models` | create | 新建 AI 模型配置 |
| `ai_models` | read | 查看模型列表 |
| `ai_models` | update | 编辑模型配置 |
| `ai_models` | delete | 删除模型配置 |

---

## 五、 核心游戏逻辑

### 5.1 游戏流程状态机

```
┌─────────────┐
│   WAITING   │ ← 玩家加入/离开/准备
└──────┬──────┘
       │ 所有人都准备且达到最少人数
       ▼
┌─────────────┐
│    SETUP    │ ← 灵魂注入（60秒倒计时）
│ (60秒)      │   - 玩家设置 System Prompt
└──────┬──────┘   - 玩家选择 AI 模型
       │ 倒计时结束或所有人锁定
       ▼
┌─────────────┐
│  PLAYING    │ ← 游戏进行中（m 轮）
│   ┌────────┴────────┐
│   │                  │
│   ▼                  ▼
│ QUESTIONING      ANSWERING
│ (提问者输入)      (被测者选择/输入)
│   │                  │
│   └────────┬────────┘
│            ▼
│        VOTING
│    (其他玩家投票)
│            │
│            ▼
│        REVEALED
│    (揭示答案+结算)
│            │
└─────────────┘
       │
       ▼
┌─────────────┐
│  FINISHED   │ ← 游戏结束，显示排行榜
└─────────────┘
```

### 5.2 随机延迟算法

```python
async def calculate_display_delay(answer_type: str, submit_time: datetime) -> datetime:
    """计算回答实际显示时间（防作弊延迟）。
    
    规则：
    1. AI 代答：随机延迟 5-15 秒
    2. 真人回答：最低显示 5 秒延迟（如果提交过快）
    3. 统一再加 0-3 秒网络延迟模拟
    """
    base_delay = 0
    
    if answer_type == "ai":
        # AI 生成可能需要一些时间，模拟"思考"
        base_delay = random.uniform(5, 15)
    else:
        # 真人回答：如果提交过快，强制等待
        time_taken = (submit_time - question_at).total_seconds()
        if time_taken < 5:
            base_delay = 5 - time_taken
    
    # 额外网络延迟模拟
    network_delay = random.uniform(0, 3)
    
    display_time = submit_time + timedelta(seconds=base_delay + network_delay)
    return display_time
```

### 5.3 评分逻辑

```python
def calculate_scores(round_result: dict, votes: list[VoteRecord]) -> dict[str, int]:
    """计算本回合得分。
    
    返回格式：{player_id: score_delta}
    """
    scores = {}
    subject_id = round_result["subject_id"]
    answer_type = round_result["answer_type"]  # "human" 或 "ai"
    
    # 1. 计算陪审团得分
    for vote in votes:
        if vote.vote == "skip":
            continue
            
        is_correct = (vote.vote == answer_type)
        if is_correct:
            scores[vote.voter_id] = scores.get(vote.voter_id, 0) + 50
            # 连续猜对奖励
            # ... (需要根据上下文计算连续次数)
        else:
            scores[vote.voter_id] = scores.get(vote.voter_id, 0) - 30
    
    # 2. 计算被测者得分（只有当使用了 AI 时才计算欺骗分）
    if answer_type == "ai":
        wrong_votes = sum(1 for v in votes if v.vote == "human")
        # 每骗一个人 +100 分
        scores[subject_id] = scores.get(subject_id, 0) + wrong_votes * 100
        
        # 完美伪装奖励：如果所有人都猜错了（不算skip）
        non_skip_votes = [v for v in votes if v.vote != "skip"]
        if non_skip_votes and all(v.vote == "human" for v in non_skip_votes):
            scores[subject_id] += 200  # 完美伪装奖励
    
    return scores
```

---

## 六、 前端页面设计

### 6.1 大厅页面 (`/game/{room_id}`)

- 房间信息卡片（房间号、状态、玩家数）
- 玩家列表（昵称、准备状态、在线状态）
- 房间设置（房主可见）
- 聊天区域（可选，用于玩家交流）
- 倒计时显示（游戏开始前）

### 6.2 灵魂注入页面 (`/game/{room_id}/setup`)

- 左侧：系统提示词输入框（Textarea）
- 右侧：可用 AI 模型下拉选择
- 底部：与 AI 的测试对话区域（每次发送后清空上下文）
- 右上角：倒计时进度条
- 确认按钮：锁定设置，开始游戏

### 6.3 游戏进行页面

- 顶部：回合进度条（Round X / Y）
- 中间：问答展示区
  - 提问者的问题（气泡样式）
  - "对方正在输入..." 加载动画
  - 回答内容（气泡样式）
- 底部：
  - 提问者：问题输入框 + 提交按钮
  - 被测者："亲自回答" / "AI 代答" 两个大按钮
  - 陪审团：三个投票按钮（🔵真人 🔴AI ⚪跳过）

---

## 七、 安全与防作弊

### 7.1 输入过滤

- 系统提示词和回答禁止包含敏感词
- 禁止在回答中直接提示（如 "选我"、"我是AI"）
- AI 生成可设置最小长度（如 > 10 字符）

### 7.2 房主验证

- 房间密码使用 bcrypt 哈希存储
- 房主拥有踢人、开始游戏、解散房间等权限

### 7.3 会话管理

- 玩家使用临时会话（UUID 存储在 Cookie）
- 刷新页面保持会话（通过 player_id + room_id 恢复）

---

## 八、 开发阶段划分

### 阶段一：基础模型与 Admin CRUD

1. 创建 `ai_model.py` 模型
2. 生成 Admin 脚手架：`python scripts/generate_admin_module.py ai_models`
3. 完成 AI 模型的增删改查
4. 创建 `game_room.py`、`game_player.py` 基础模型

### 阶段二：游戏前台核心

1. 实现房间创建/加入逻辑
2. 实现灵魂注入页面
3. 实现 SSE 实时事件推送
4. 实现游戏状态机

### 阶段三：游戏进行逻辑

1. 实现提问/回答/投票流程
2. 实现随机延迟算法
3. 实现 AI 调用服务（接入 OpenAI SDK）
4. 实现得分计算

### 阶段四：结算与展示

1. 实现回合结算动画
2. 实现游戏结束排行榜
3. 实现趣味称号计算

### 阶段五：优化与完善

1. 前端 UI 美化
2. 响应式适配
3. 异常处理与断线重连
4. 压力测试与性能优化

---

## 九、 依赖与配置

### 9.1 Python 依赖

已添加：
- `openai>=2.20.0` - OpenAI SDK（已通过 `uv add openai` 添加）

可选：
- `aiohttp` - 已在项目依赖中，用于 SSE

### 9.2 环境变量

```bash
# 游戏相关配置
GAME_DEFAULT_SETUP_DURATION=60
GAME_DEFAULT_ROUNDS=0  # 0 表示等于玩家数
GAME_MIN_PLAYERS=2
GAME_MAX_PLAYERS=8

# AI 模型默认值（可选，备用）
# 默认 AI 模型可通过 Admin 后台配置
```

---

## 十、 注意事项

1. **AI API 费用**：游戏会产生大量 AI 调用，建议在说明中提示玩家自备 API Key
2. **模型兼容性**：使用 OpenAI 兼容接口，可接入任意 LLM 服务
3. **长连接处理**：SSE 需要保持长连接，确保 Nginx/代理支持长连接
4. **房间清理**：定期清理已结束的游戏房间（可设置 TTL 或定时任务）
5. **敏感信息**：API Key 必须加密存储（可在模型中加密，调用时解密）
