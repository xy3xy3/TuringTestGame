# AGENTS.md（PyFastAdmin 开发规范）

本文件用于给后续 AI/开发者统一“怎么开发、参考哪里、如何自检”。

## 0. 总原则
- 优先保证：**权限正确 > 接口安全 > UI 一致性 > 代码简洁**。
- 新功能默认要包含：
  1) 页面与按钮权限控制（前端显示层）
  2) 接口鉴权（后端强校验）
  3) 操作日志记录
  4) 移动端 + PC 响应式适配
- 回答用户使用中文。
- 函数必须有中文注释说明用途；复杂逻辑需补充中文注释。

---

## 1. 技术栈与命令约束

### 1.1 Python / 依赖 / 启动
- Python 相关命令必须使用 `uv run` 或 `.venv` 环境。
- 禁止直接使用 `python` / `python3` 裸命令。
- 常用命令：
  - 安装依赖：`uv sync`
  - 启动服务：`uv run uvicorn app.main:app --reload --port ${APP_PORT:-8000}`

### 1.2 Node / Tailwind
- Node 包管理只用 `pnpm`，禁止 `npm`。
- Tailwind 源文件：`app/static/css/tailwind.css`
- 编译产物：`app/static/css/app.css`
- **禁止手改 `app/static/css/app.css`**，只能由构建生成。
- 常用命令：
  - 安装前端依赖：`pnpm install`
  - 构建 CSS：`pnpm build:css`
  - 开发监听：`pnpm dev:css`

### 1.3 MongoDB（开发）
- 开发环境 MongoDB 在 `deploy/dev`。
- 启动：`cd deploy/dev && docker compose --env-file ../../.env up -d`

---

## 2. 目录速查（改动前先定位）
- 后端路由控制器：`app/apps/admin/controllers/`
- 权限资源注册：`app/apps/admin/registry.py`
- 脚手架扩展资源目录：`app/apps/admin/registry_generated/`
- 权限解析与鉴权：`app/services/permission_service.py`
- 显式权限装饰器：`app/services/permission_decorator.py`
- 统一字段校验：`app/services/validators.py`
- 鉴权中间件：`app/middleware/auth.py`
- 模板：`app/apps/admin/templates/`
  - 页面：`pages/`
  - 表格/弹窗等局部：`partials/`
- 全局前端交互：`app/static/js/app.js`
- 日志服务：`app/services/log_service.py`
- 模块脚手架命令：`scripts/generate_admin_module.py`
- 测试：`tests/unit`、`tests/integration`、`tests/e2e`

---

## 3. RBAC 权限开发硬性规则

### 3.1 资源类型与动作模板
- 资源按 `mode` 建模，常用模板：
  - `table`：`create/read/update/delete`
  - `settings`：`read/update`
  - `operation`：`read/trigger/restore/delete`
  - `self_service`：`read/update_self`（默认登录即允许，不参与角色分配）
- 当前系统动作白名单：`create/read/update/delete/trigger/restore/update_self`。
- `viewer` 角色默认只读：仅保留各资源 `read` 动作。
- 新资源必须在 `registry.py` 或 `registry_generated/*.json` 明确 `mode` + `actions`。
- 新增动作时必须同步：
  - `app/apps/admin/registry.py`（`RESOURCE_ACTION_TEMPLATES` / `VALID_ACTIONS`）
  - `app/apps/admin/controllers/rbac.py`（`ACTION_LABELS` 展示文案）
  - `app/services/permission_service.py`（自动推导与路由鉴权）

### 3.2 内置资源映射（改动前先对照）
- `dashboard_home`：`settings`，`read`
- `rbac`：`table`，`create/read/update/delete`
- `admin_users`：`table`，`create/read/update/delete`
- `profile`：`self_service`，`read/update_self`（`assignable=false`，`require_read=false`）
- `password`：`self_service`，`read/update_self`（`assignable=false`，`require_read=false`）
- `config`：`settings`，`read/update`
- `operation_logs`：`operation`，`read/delete`
- `backup_config`：`settings`，`read/update`
- `backup_records`：`operation`，`read/trigger/restore/delete`

### 3.3 前端按钮/列显示规则（必须）
- 列表型（`table`）页面遵循：
  - 无 `create` 权限：隐藏“新建”按钮。
  - 无 `update` 权限：隐藏“编辑”按钮。
  - 无 `delete` 权限：隐藏“删除”按钮。
  - 若同时无 `update` 与 `delete`：**整列“操作”不显示**。
- 非列表型页面按语义动作控制按钮（如 `trigger/restore/update_self`），禁止生搬 CRUD 文案。

### 3.4 Jinja 权限判断写法（重要）
- 权限对象是字典时，必须使用下标写法：
  - `perm['update']`、`perm['delete']`、`perm['create']`。
- 禁止使用 `perm.update` 这类点写法，避免命中字典方法导致误判（会造成按钮错误显示）。

### 3.5 后端接口鉴权（必须）
- 仅做前端隐藏不够，所有对应接口必须鉴权。
- 新增/修改路由后，务必同步检查：
  - `permission_service.required_permission(...)` 是否已映射到正确 resource/action。
  - `auth.py` 中间件是否能拦截并返回 403。
- `permission_meta(...)` 显式声明优先级高于自动推导，语义动作（如 `trigger/restore/update_self`）建议显式标注。
- 含静态后缀的路由（如 `/bulk-delete`、`/restore`）要写在动态路由（如 `/{id}`）之前，避免被错误匹配。

### 3.6 角色权限保存约束
- `assignable=false` 资源禁止进入角色权限树（例如 `self_service`）。
- `require_read=true` 且资源存在 `read` 动作时，保存与解析阶段都要兜底：缺少 `read` 时清理其它动作。
- 权限解析时必须做动作白名单清洗（防脏数据绕过）。
- `self_service` 资源通过内置授权自动授予登录用户（不依赖角色 permissions 存储）。

---

## 4. 模块脚手架约定（AI 优先遵循）

### 4.1 先脚手架，后业务
- 新增后台模块优先执行脚手架：
  - `uv run python scripts/generate_admin_module.py <module> --name "中文名" --group system`
- 禁止跳过脚手架直接散改多文件，除非用户明确要求。

### 4.2 模块命名规则
- `module` 必须匹配：`^[a-z][a-z0-9_]{1,31}$`。
- 路由默认前缀：`/admin/<module>`。
- 资源 key 与 module 同名，避免权限映射分裂。

### 4.3 脚手架产物（必须检查）
- `app/apps/admin/controllers/<module>.py`
- `app/models/<module>.py`
- `app/services/<module>_service.py`
- `app/apps/admin/templates/pages/<module>.html`
- `app/apps/admin/templates/partials/<module>_table.html`
- `app/apps/admin/templates/partials/<module>_form.html`
- `tests/unit/test_<module>_scaffold.py`
- `app/apps/admin/registry_generated/<module>.json`
  - 默认包含：`mode=table` 与 CRUD `actions`
- 自动更新：`app/models/__init__.py` 与 `app/db.py`

### 4.4 脚手架后必做事项
- 在 `app/main.py` 手动 `import router` 并 `app.include_router(...)`。
- 按业务调整模型字段、索引与服务层查询条件（默认 CRUD 仅作为起点）。
- 补齐表单校验（优先复用 `validators.py`）。
- 补齐筛选/分页与 HTMX `hx-vals` 透传。
- 检查按钮权限显示与后端 403 一致。
- 检查日志字段是否满足审计要求。

---

## 5. HTMX + Alpine + Jinja 开发约定
- 列表页采用“页面 + 表格 partial”模式（如 `.../table`）。
- 弹窗表单通过 HTMX 加载到 `#modal-body`。
- **所有弹窗统一结构**：顶部（标题+关闭）固定、底部（取消/保存）固定，仅中间内容区滚动（`overflow-y-auto`）。
- 校验错误块应放在中间滚动区顶部，确保滚动时也能第一时间看到。
- **弹窗内提交也必须回写 `#modal-body`**：`hx-target="#modal-body" + hx-swap="innerHTML"`，保证 422/校验失败时错误信息能在弹窗内直接显示。
- 弹窗提交成功后，如需刷新列表，后端返回表格 partial 时通过 `HX-Retarget: #xxx-table` + `HX-Reswap: outerHTML` 重定向更新目标，并用 `HX-Trigger` 触发关闭弹窗。
- 后端对 HTMX 弹窗校验失败建议返回表单 partial + `status=200`（非 HTMX 场景保留 422），避免前端只弹通用“请求失败”toast。
- 列表筛选/分页刷新时，保持 `hx-vals` 透传当前筛选条件和页码。
- 返回 403 的 HTMX 请求，要由前端统一 toast 反馈（参考 `app/static/js/app.js`）。
- 新增前端交互逻辑优先复用 `app/static/js/app.js`，避免散落内联脚本。
- 批量操作推荐模式：`hx-post`/`hx-confirm` 写在批量按钮，配合 `hx-include="closest form"`；避免把 `hx-confirm` 挂在 `<form>` 上导致继承误伤其它按钮。

---

## 6. 响应式规范（移动端 + PC）
- 所有新页面必须同时适配移动端和桌面端。
- 可参考：`app/apps/admin/templates/pages/profile.html` 的布局策略。
- 表格页面保持现有表格风格，不强制改卡片风格。
- 宽表必须使用横向滚动容器（如 `overflow-x-auto`），避免移动端撑爆页面。
- 新增 `min-w-*` 时要控制合理宽度，避免小屏过宽。

---

## 7. 操作日志规范
- 关键页面访问与增删改操作需要记录日志。
- 使用 `log_service.record_request(...)`，至少包含：
  - `action`（`create/read/update/delete/trigger/restore/update_self` 按实际语义填写）
  - `module`（如 rbac/admin_users）
  - `target`（操作对象）
  - `detail`（操作描述）
- 登录/登出/个人资料/改密/导入导出这类账号安全场景也要记录。

---

## 8. 开发流程（建议 AI 按此执行）
1. 先读本文件和 `README.md`，明确约束与运行方式。
2. 若是新模块，先执行脚手架（建议先 `--dry-run`）。
3. 定位涉及模块（controller/service/template/js）。
4. 先补后端鉴权，再做前端按钮显示控制。
5. 完成响应式检查（至少手机宽度与桌面宽度各看一轮）。
6. 补充/更新测试（尤其权限逻辑、导入导出、校验逻辑）。
7. 本地自检通过后再交付。

---

## 9. 交付前自检清单（最少）
- 单测：`uv run pytest -m unit`
- 语法检查：`uv run python -m compileall app tests scripts`
- 若改了 Tailwind：`pnpm build:css`
- 若改了权限：人工确认以下场景
  - 无编辑权限时编辑按钮不显示
  - 无删除权限时删除按钮不显示
  - 无编辑+删除权限时操作列不显示
  - 强行请求接口会被后端 403 拦截
- 若改了导入导出：至少跑 `uv run pytest tests/integration/test_rbac_role_transfer.py -m integration`

---

## 10. 参考优先级
1. 本文件（AGENTS.md）
2. `README.md`（运行/部署/测试）
3. 现有同类页面实现（优先复用已有模式）
4. 最后才做新的模式设计（避免破坏整体风格）
