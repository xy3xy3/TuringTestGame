# PyFastAdmin

HTMX + Alpine.js + Tailwind + FastAPI + Jinja2 + Beanie 的不分离管理后台示例。

## 功能
- RBAC 权限管理（增删改查）
- 权限树配置（按页面 URL/名称聚合）
- 管理员管理（创建/编辑/禁用）
- 登录、个人资料、修改密码场景页面
- 系统配置（示例：SMTP）
- 移动端/桌面端响应式布局
- 所有静态资源本地化（HTMX/Alpine/Tailwind 编译产物均为本地）

## 目录结构
- `app/`：FastAPI 应用与模板
- `app/static/`：本地静态资源
- `deploy/dev/`：仅数据库的开发环境
- `deploy/product/`：生产环境 Dockerfile（uv 安装依赖）
- `refs/`：外部仓库参考（已 gitignore）

## 本地运行
1. 准备 MongoDB（开发环境）

```bash
cd deploy/dev
docker compose --env-file ../../.env up -d
```

2. 安装前端依赖并构建 Tailwind

```bash
pnpm install
pnpm build:css
```

3. 使用 uv 创建虚拟环境并同步依赖（Python 3.13）

```bash
uv venv -p 3.13
source .venv/bin/activate
uv sync
```

4. 启动服务

```bash
uv run uvicorn app.main:app --reload --port ${APP_PORT:-8000}
```

访问：http://localhost:8000/admin/rbac
- 权限树配置：http://localhost:8000/admin/rbac/permissions
- 管理员管理：http://localhost:8000/admin/users
- 个人资料：http://localhost:8000/admin/profile
- 修改密码：http://localhost:8000/admin/password
- 系统配置：http://localhost:8000/admin/config

首次启动会自动创建默认管理员（用户名/密码来自 `.env` 的 `ADMIN_USER`、`ADMIN_PASS`）。


## 测试（单元 / 集成 / E2E）

> 测试会使用 **独立 MongoDB 数据库**，不污染主库。

1. 默认会自动加载项目 `.env`（无需手动 `export`）

测试优先读取：`TEST_MONGO_URL / TEST_MONGO_DB / TEST_E2E_MONGO_DB`，未设置时回退到 `.env` 的 `MONGO_URL`。
如需临时覆盖，可在命令前追加环境变量：

```bash
TEST_MONGO_URL=mongodb://localhost:27117 TEST_MONGO_DB=pyfastadmin_test uv run pytest -m integration
```

2. 安装测试依赖（已通过 `uv add --dev` 管理）

```bash
uv sync
```

3. 运行测试

```bash
# 单元测试（纯逻辑）
uv run pytest -m unit

# 集成测试（需要 MongoDB）
uv run pytest -m integration

# 端到端（Playwright）
uv run playwright install chromium
uv run pytest -m e2e
```

说明：
- `tests/unit/`：不依赖数据库
- `tests/integration/`：自动清理 `TEST_MONGO_DB`
- `tests/e2e/`：启动独立服务并使用 `TEST_E2E_MONGO_DB`

## 生产部署（含 uv Dockerfile）

```bash
cd deploy/product
docker compose --env-file ../../.env up -d --build
```

## 环境变量
参考 `.env.example`，重点变量：
- `APP_PORT`：应用端口
- `MONGO_URL`：MongoDB 连接串
- `MONGO_DB`：数据库名称
- `MONGO_PORT`：MongoDB 容器映射端口
- `SECRET_KEY`：Session 加密密钥
- `ADMIN_USER`：默认管理员账号
- `ADMIN_PASS`：默认管理员密码

备份云存储测试变量（用于 E2E/测试直连云端）：
- `TEST_BACKUP_USE_ENV`：开发环境是否强制启用 `TEST_BACKUP_*` 覆盖（`1/true` 生效）
- `TEST_BACKUP_CLOUD_ENABLED`：是否启用云端备份
- `TEST_BACKUP_CLOUD_PROVIDERS`：云厂商列表，逗号分隔（如 `aliyun_oss,tencent_cos`）
- `TEST_BACKUP_CLOUD_PATH`：云端备份前缀
- `TEST_BACKUP_OSS_*` / `TEST_BACKUP_COS_*`：OSS/COS 凭证与桶配置

## 依赖管理（uv）
- 新增依赖：`uv add 包名`
- 同步依赖：`uv sync`

## 二开与迁移

本章节面向业务开发者，重点讲“怎么从示例项目快速落地自己的模块”。

### 1) 一键生成模块骨架（推荐起手）

```bash
# 先预览将生成哪些文件
uv run python scripts/generate_admin_module.py inventory --name "库存管理" --group system --dry-run

# 确认后生成
uv run python scripts/generate_admin_module.py inventory --name "库存管理" --group system
```

可选参数：
- `module`：模块标识，必须匹配 `^[a-z][a-z0-9_]{1,31}$`
- `--name`：模块中文名（默认同 module）
- `--group`：菜单分组 key（默认 `system`）
- `--url`：资源 URL（默认 `/admin/<module>`）
- `--force`：覆盖已有文件
- `--dry-run`：仅打印不落盘

### 2) 脚手架会生成什么
- `app/apps/admin/controllers/<module>.py`
- `app/models/<module>.py`
- `app/services/<module>_service.py`
- `app/apps/admin/templates/pages/<module>.html`
- `app/apps/admin/templates/partials/<module>_table.html`
- `app/apps/admin/templates/partials/<module>_form.html`
- `tests/unit/test_<module>_scaffold.py`
- `app/apps/admin/registry_generated/<module>.json`
- 自动更新 `app/models/__init__.py` 与 `app/db.py`，完成模型注册

### 3) 生成后你还需要手动做什么（必须）
1. 在 `app/main.py` 引入并注册新控制器路由（`app.include_router(...)`）。
2. 按业务调整模型字段/索引和服务层读写逻辑（脚手架提供的是通用 CRUD 起点）。
3. 按业务补齐表单字段、筛选、分页和错误提示。
4. 保持按钮权限与后端权限一致（只隐藏按钮不算完成）。
5. 补充操作日志（至少 create/read/update/delete）。
6. 做移动端和桌面端检查（尤其表格横向滚动）。

### 4) RBAC 显式权限声明（强烈建议）
- 推荐在路由上使用：
  - `@permission_decorator.permission_meta("resource", "action")`
- 自动推断仍可用，但只作为兜底。
- 批量操作、导入导出、非标准路径务必显式声明，降低误判风险。

### 5) 角色权限导入导出（JSON）
- 页面入口：`/admin/rbac` 顶部按钮（导出 JSON / 导入 JSON）
- 导出接口：`GET /admin/rbac/roles/export?include_system=1`
- 导入接口：`POST /admin/rbac/roles/import`

典型迁移流程：
1. 在示例环境导出角色权限 JSON。
2. 在业务环境进入 RBAC 页面导入 JSON。
3. 检查导入 summary（新增/更新/跳过）与操作日志。
4. 用导入后的角色账号实际登录验证菜单和按钮权限。

### 6) 推荐验收命令

```bash
# 基础回归
uv run pytest -m unit
uv run python -m compileall app tests scripts

# 若改了导入导出/权限映射，建议追加
uv run pytest tests/integration/test_rbac_role_transfer.py -m integration
```

### 7) 更多二开细则
- AI 协作规范：`AGENTS.md`
- 8 步落地清单：`docs/SECONDARY_DEVELOPMENT_GUIDE.md`
