# TuringTestGame

一个不分离的后台管理系统 + 图灵测试小游戏示例项目。

技术栈：FastAPI + Jinja2 + HTMX + Alpine.js + Tailwind + MongoDB(Beanie)。

## 在线体验

- 游戏体验网址：`https://turinggame.skywc.com`

## 主要功能

后台（`/admin`）
- RBAC 角色权限（菜单、页面、按钮与接口强校验）
- AI 模型配置（用于游戏里的 AI 代答）
- 系统配置、操作日志、备份（示例模块）

前台游戏（`/game`）
- 创建/加入房间（邀请码邀请）
- 灵魂注入：为你的 AI 角色设置系统提示词与模型
- SSE 实时同步房间与对局状态（准备状态、回合推进、倒计时等）
- 支持 Redis + IP 限流（可在后台系统配置开启，防止恶意高频请求）

## 游戏规则（图灵测试）

基本概念
- 参与者：每局有多名玩家。
- 角色：每一轮会随机选出
  - 提问者（Interrogator）：提出问题
  - 被测者（Subject）：选择“真人回答”或“AI 代答”
  - 陪审团（Jury）：除被测者外的其他玩家，用于投票判断回答来自真人还是 AI

流程
1. 房间大厅（`waiting`）：玩家加入并点击“准备”
2. 灵魂注入（`setup`）：每位玩家配置自己的 AI 角色（系统提示词 + 模型）
3. 对局进行（`playing`）：每轮包含提问 -> 回答 -> 投票 -> 揭晓
4. 结算（`finished`）：展示排行榜与成就

得分规则（以当前实现为准）
- 提问者（Interrogator，唯一计分）
  - 猜对回答类型：`+50`
  - 猜错回答类型：`-30`
  - 选择跳过：`0`
- 被测者与陪审团
  - 本轮不参与加减分：`0`

说明
- 回合数按“当前玩家数 × 2”动态计算（上限 20）。
- 若未配置任何可用 AI 模型，“AI 代答”会失败（测试环境有固定回复兜底，见下文）。

## 本地运行（开发）

前置要求
- Docker / Docker Compose（用于本地 MongoDB）
- Node + pnpm
- Python 3.13 + uv

1) 启动 MongoDB + Redis（开发环境）

```bash
cd deploy/dev
docker compose --env-file ../../.env up -d
```

2) 安装前端依赖并构建 Tailwind

```bash
pnpm install
pnpm build:css
```

3) 初始化 Python 依赖

```bash
uv venv -p 3.13
source .venv/bin/activate
uv sync
```

4) 启动服务

```bash
uv run uvicorn app.main:app --reload --port ${APP_PORT:-8000}
```

访问入口
- 游戏首页：`http://localhost:8000/game`
- 管理后台：`http://localhost:8000/admin/login`

首次启动会自动创建默认管理员（用户名/密码来自 `.env` 的 `ADMIN_USER`、`ADMIN_PASS`）。

## IP 限流与反向代理 IP

你可以在后台 `系统配置 -> 系统设置 -> IP 限流（防 CC）` 中配置：
- 是否启用限流
- 限流窗口秒数
- 创建房间 / 加入房间 / 测试对话 / 通用写接口的请求上限
- 是否信任反向代理 IP 头（`X-Forwarded-For` / `X-Real-IP`）

建议 Nginx 反代配置（示例）：

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Real-IP $remote_addr;
```

部署时请确保 `.env` 中配置了 Redis：
- `REDIS_URL=redis://localhost:6379/0`（本地）
- 生产 compose 默认注入：`redis://redis:6379/0`

## AI 模型配置（用于“AI 代答”）

项目使用 OpenAI 兼容接口进行调用（见 `app/services/ai_chat_service.py`）。

配置步骤
1. 登录后台：`/admin/login`
2. 进入 AI 模型配置：`/admin/ai_models`
3. 新建模型并填写：
   - `名称`：展示用
   - `Base URL`：OpenAI 兼容地址（例如 OpenAI 官方或自建网关）
   - `API Key`：访问令牌
   - `模型名`：例如 `gpt-4o-mini`（按你的服务实际支持填写）
   - `Temperature / Max Tokens`
4. 确保至少有 1 个模型 `启用`，并建议设置 1 个 `默认`

没有可用模型时
- 生产/开发环境：会返回“没有可用的 AI 模型”
- 测试环境（`APP_ENV=test`）：会使用固定回复 `TEST_FAKE_AI_REPLY` 作为兜底，保证 E2E 可稳定运行

## 测试（单元 / 集成 / E2E）

单元测试（纯逻辑）
```bash
uv run pytest -m unit
```

语法检查
```bash
uv run python -m compileall app tests scripts
```

E2E（Playwright）
```bash
uv run playwright install chromium
uv run pytest -m e2e
```

说明
- E2E 会启动独立服务并使用独立 MongoDB 数据库（见 `tests/e2e/conftest.py`）
- 游戏 E2E：
  - 二人局：`tests/e2e/test_game_two_players_flow.py`
  - 三人局：`tests/e2e/test_game_three_players_flow.py`
- E2E 默认会在测试环境缩短各阶段时长，并固定 AI 回复，确保用例稳定不依赖外部服务。

## 生产部署（Docker Compose）

```bash
# 推荐在项目根目录执行（避免路径切换导致读取错环境文件）
docker compose -f deploy/product/docker-compose.yml up -d --build
```

说明
- `deploy/product/docker-compose.yml` 已固定项目名与卷名（`product_*`），避免不同目录执行命令时创建新卷导致“数据像丢失”。
- `app` 服务已内置 `env_file: ../../.env`，即使不手动传 `--env-file` 也会读取项目根目录 `.env`。

部署要点
- 正确配置 `MONGO_URL/MONGO_DB/SECRET_KEY`
- 正确配置 `REDIS_URL`（用于 IP 限流）
- 配置后台 AI 模型（否则“AI 代答”不可用）
- 邀请链接依赖站点 `Base URL`（后台配置项，用于生成可分享链接）

## License

本项目使用 GNU GPLv3 许可证，见 `LICENSE`。
