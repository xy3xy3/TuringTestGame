"""后台模块脚手架命令。"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

MODULE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

ROOT = Path(__file__).resolve().parents[1]
CONTROLLERS_DIR = ROOT / "app/apps/admin/controllers"
SERVICES_DIR = ROOT / "app/services"
MODELS_DIR = ROOT / "app/models"
PAGES_DIR = ROOT / "app/apps/admin/templates/pages"
PARTIALS_DIR = ROOT / "app/apps/admin/templates/partials"
TESTS_DIR = ROOT / "tests/unit"
REGISTRY_DIR = ROOT / "app/apps/admin/registry_generated"
MODELS_INIT_FILE = ROOT / "app/models/__init__.py"
DB_FILE = ROOT / "app/db.py"


def parse_args() -> argparse.Namespace:
    """解析命令参数。"""

    parser = argparse.ArgumentParser(description="生成后台 CRUD 模块骨架")
    parser.add_argument("module", help="模块标识（小写字母/数字/下划线）")
    parser.add_argument("--name", default="", help="模块中文名，默认使用 module")
    parser.add_argument("--group", default="system", help="注册分组 key，默认 system")
    parser.add_argument("--url", default="", help="资源 URL，默认 /admin/<module>")
    parser.add_argument("--force", action="store_true", help="覆盖已有文件")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将要生成的文件")
    return parser.parse_args()


def ensure_module_name(module: str) -> str:
    """校验模块名，避免生成非法路由与文件名。"""

    value = module.strip().lower()
    if not MODULE_PATTERN.fullmatch(value):
        raise ValueError("module 必须匹配 ^[a-z][a-z0-9_]{1,31}$")
    return value


def to_pascal_case(value: str) -> str:
    """下划线命名转换为驼峰命名。"""

    return "".join(part[:1].upper() + part[1:] for part in value.split("_") if part)


def write_file(path: Path, content: str, *, force: bool, dry_run: bool) -> None:
    """写入文件，支持覆盖与 dry-run。"""

    if path.exists() and not force:
        raise FileExistsError(f"文件已存在：{path}")

    if dry_run:
        print(f"[dry-run] {path}")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"[ok] {path}")


def _insert_model_import(models_init_text: str, module: str, class_name: str) -> str:
    """向 app/models/__init__.py 注入模块导入。"""

    import_line = f"from .{module} import {class_name}"
    if import_line in models_init_text:
        return models_init_text

    if "\n__all__" in models_init_text:
        return models_init_text.replace("\n__all__", f"\n{import_line}\n\n__all__", 1)

    if not models_init_text.endswith("\n"):
        models_init_text += "\n"
    return models_init_text + import_line + "\n"


def _update_model_exports(models_init_text: str, class_name: str) -> str:
    """更新 app/models/__init__.py 的 __all__ 导出列表。"""

    match = re.search(r"__all__\s*=\s*(\[[^\]]*\])", models_init_text, flags=re.DOTALL)
    if not match:
        if not models_init_text.endswith("\n"):
            models_init_text += "\n"
        return models_init_text + f"__all__ = [\"{class_name}\"]\n"

    exports = list(ast.literal_eval(match.group(1)))
    if class_name not in exports:
        exports.append(class_name)

    replacement = "__all__ = [" + ", ".join(f'\"{item}\"' for item in exports) + "]"
    return models_init_text[: match.start()] + replacement + models_init_text[match.end() :]


def wire_models_init(module: str, class_name: str, *, dry_run: bool) -> None:
    """自动接入 app/models/__init__.py。"""

    if dry_run:
        print(f"[dry-run] update {MODELS_INIT_FILE}")
        return

    if not MODELS_INIT_FILE.exists():
        raise FileNotFoundError(f"缺少文件：{MODELS_INIT_FILE}")

    text = MODELS_INIT_FILE.read_text(encoding="utf-8")
    text = _insert_model_import(text, module, class_name)
    text = _update_model_exports(text, class_name)
    MODELS_INIT_FILE.write_text(text, encoding="utf-8")
    print(f"[ok] update {MODELS_INIT_FILE}")


def wire_db_models(class_name: str, *, dry_run: bool) -> None:
    """自动接入 app/db.py 的模型导入和 document_models 列表。"""

    if dry_run:
        print(f"[dry-run] update {DB_FILE}")
        return

    if not DB_FILE.exists():
        raise FileNotFoundError(f"缺少文件：{DB_FILE}")

    text = DB_FILE.read_text(encoding="utf-8")

    import_match = re.search(r"from \.models import ([^\n]+)", text)
    if not import_match:
        raise RuntimeError("app/db.py 未找到 models 导入行")

    imported = [item.strip() for item in import_match.group(1).split(",") if item.strip()]
    if class_name not in imported:
        imported.append(class_name)
        new_import_line = "from .models import " + ", ".join(imported)
        text = text[: import_match.start()] + new_import_line + text[import_match.end() :]

    models_match = re.search(r"document_models=\[(.*?)\]", text, flags=re.DOTALL)
    if not models_match:
        raise RuntimeError("app/db.py 未找到 document_models 列表")

    model_names = [item.strip() for item in models_match.group(1).split(",") if item.strip()]
    if class_name not in model_names:
        model_names.append(class_name)
        replacement = "document_models=[" + ", ".join(model_names) + "]"
        text = text[: models_match.start()] + replacement + text[models_match.end() :]

    DB_FILE.write_text(text, encoding="utf-8")
    print(f"[ok] update {DB_FILE}")


def render_controller(module: str, title: str) -> str:
    """渲染控制器模板。"""

    return f'''"""{title} 控制器。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services import {module}_service, log_service, permission_decorator

BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(prefix="/admin")


def base_context(request: Request) -> dict[str, Any]:
    """构建模板基础上下文。"""

    return {{
        "request": request,
        "current_admin": request.session.get("admin_name"),
    }}


def _is_htmx_request(request: Request) -> bool:
    """判断是否为 HTMX 请求，用于区分表单错误返回策略。"""

    return request.headers.get("hx-request", "").strip().lower() == "true"


@router.get("/{module}", response_class=HTMLResponse)
async def {module}_page(request: Request) -> HTMLResponse:
    """模块列表页。"""

    items = await {module}_service.list_items()
    await log_service.record_request(
        request,
        action="read",
        module="{module}",
        target="{title}",
        detail="访问模块列表页面",
    )
    return templates.TemplateResponse("pages/{module}.html", {{**base_context(request), "items": items}})


@router.get("/{module}/table", response_class=HTMLResponse)
async def {module}_table(request: Request) -> HTMLResponse:
    """模块表格 partial。"""

    items = await {module}_service.list_items()
    return templates.TemplateResponse("partials/{module}_table.html", {{**base_context(request), "items": items}})


@router.get("/{module}/new", response_class=HTMLResponse)
async def {module}_new(request: Request) -> HTMLResponse:
    """新建弹窗。"""

    return templates.TemplateResponse(
        "partials/{module}_form.html",
        {{**base_context(request), "mode": "create", "action": "/admin/{module}", "errors": [], "form": {{}}}},
    )


@router.post("/{module}", response_class=HTMLResponse)
@permission_decorator.permission_meta("{module}", "create")
async def {module}_create(request: Request) -> HTMLResponse:
    """创建数据（脚手架模板，需按业务补充校验）。"""

    form_data = await request.form()
    payload = dict(form_data)

    errors: list[str] = []
    if not str(payload.get("name", "")).strip():
        errors.append("名称不能为空")
    if errors:
        context = {{
            **base_context(request),
            "mode": "create",
            "action": "/admin/{module}",
            "errors": errors,
            "form": payload,
        }}
        error_status = 200 if _is_htmx_request(request) else 422
        return templates.TemplateResponse("partials/{module}_form.html", context, status_code=error_status)

    created = await {module}_service.create_item(payload)
    await log_service.record_request(
        request,
        action="create",
        module="{module}",
        target="{title}",
        target_id=str(getattr(created, "id", "")),
        detail="创建记录",
    )

    items = await {module}_service.list_items()
    response = templates.TemplateResponse("partials/{module}_table.html", {{**base_context(request), "items": items}})
    response.headers["HX-Retarget"] = "#{module}-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {{"rbac-toast": {{"title": "已创建", "message": "记录创建成功", "variant": "success"}}, "rbac-close": True}},
        ensure_ascii=True,
    )
    return response


@router.get("/{module}/{{item_id}}/edit", response_class=HTMLResponse)
async def {module}_edit(request: Request, item_id: str) -> HTMLResponse:
    """编辑弹窗。"""

    item = await {module}_service.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")

    return templates.TemplateResponse(
        "partials/{module}_form.html",
        {{
            **base_context(request),
            "mode": "edit",
            "action": f"/admin/{module}/{{item_id}}",
            "errors": [],
            "form": item,
        }},
    )


@router.post("/{module}/bulk-delete", response_class=HTMLResponse)
@permission_decorator.permission_meta("{module}", "delete")
async def {module}_bulk_delete(request: Request) -> HTMLResponse:
    """批量删除数据。"""

    form_data = await request.form()
    selected_ids = [str(item).strip() for item in form_data.getlist("selected_ids") if str(item).strip()]
    selected_ids = list(dict.fromkeys(selected_ids))

    deleted_count = 0
    skipped_count = 0
    for item_id in selected_ids:
        item = await {module}_service.get_item(item_id)
        if not item:
            skipped_count += 1
            continue

        await {module}_service.delete_item(item)
        deleted_count += 1
        await log_service.record_request(
            request,
            action="delete",
            module="{module}",
            target="{title}",
            target_id=item_id,
            detail="批量删除记录",
        )

    items = await {module}_service.list_items()
    response = templates.TemplateResponse("partials/{module}_table.html", {{**base_context(request), "items": items}})
    response.headers["HX-Retarget"] = "#{module}-table"
    response.headers["HX-Reswap"] = "outerHTML"

    if deleted_count == 0:
        message = "未删除任何记录，请先勾选数据"
    elif skipped_count > 0:
        message = f"已删除 {{deleted_count}} 条，跳过 {{skipped_count}} 条"
    else:
        message = f"已批量删除 {{deleted_count}} 条记录"

    response.headers["HX-Trigger"] = json.dumps(
        {{"rbac-toast": {{"title": "批量删除完成", "message": message, "variant": "warning"}}}},
        ensure_ascii=True,
    )
    return response


@router.post("/{module}/{{item_id}}", response_class=HTMLResponse)
@permission_decorator.permission_meta("{module}", "update")
async def {module}_update(request: Request, item_id: str) -> HTMLResponse:
    """更新数据（脚手架模板，需按业务补充校验）。"""

    item = await {module}_service.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")

    form_data = await request.form()
    payload = dict(form_data)

    errors: list[str] = []
    if not str(payload.get("name", "")).strip():
        errors.append("名称不能为空")
    if errors:
        context = {{
            **base_context(request),
            "mode": "edit",
            "action": f"/admin/{module}/{{item_id}}",
            "errors": errors,
            "form": payload,
        }}
        error_status = 200 if _is_htmx_request(request) else 422
        return templates.TemplateResponse("partials/{module}_form.html", context, status_code=error_status)

    await {module}_service.update_item(item, payload)
    await log_service.record_request(
        request,
        action="update",
        module="{module}",
        target="{title}",
        target_id=item_id,
        detail="更新记录",
    )

    items = await {module}_service.list_items()
    response = templates.TemplateResponse("partials/{module}_table.html", {{**base_context(request), "items": items}})
    response.headers["HX-Retarget"] = "#{module}-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {{"rbac-toast": {{"title": "已更新", "message": "记录更新成功", "variant": "success"}}, "rbac-close": True}},
        ensure_ascii=True,
    )
    return response


@router.delete("/{module}/{{item_id}}", response_class=HTMLResponse)
@permission_decorator.permission_meta("{module}", "delete")
async def {module}_delete(request: Request, item_id: str) -> HTMLResponse:
    """删除数据。"""

    item = await {module}_service.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")

    await {module}_service.delete_item(item)
    await log_service.record_request(
        request,
        action="delete",
        module="{module}",
        target="{title}",
        target_id=item_id,
        detail="删除记录",
    )

    items = await {module}_service.list_items()
    response = templates.TemplateResponse("partials/{module}_table.html", {{**base_context(request), "items": items}})
    response.headers["HX-Retarget"] = "#{module}-table"
    response.headers["HX-Reswap"] = "outerHTML"
    response.headers["HX-Trigger"] = json.dumps(
        {{"rbac-toast": {{"title": "已删除", "message": "记录已删除", "variant": "warning"}}}},
        ensure_ascii=True,
    )
    return response
'''

def render_model(module: str, class_name: str) -> str:
    """渲染模型模板。"""

    return f'''"""{module} 模型（脚手架生成）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from beanie import Document
from pymongo import IndexModel
from pydantic import Field


def utc_now() -> datetime:
    """返回 UTC 当前时间。"""

    return datetime.now(timezone.utc)


class {class_name}(Document):
    """{module} 数据模型。"""

    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(default="", max_length=200)
    status: Literal["enabled", "disabled"] = "enabled"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "{module}_items"
        indexes = [
            IndexModel([("name", 1)], name="idx_{module}_name"),
            IndexModel([("updated_at", -1)], name="idx_{module}_updated_at"),
        ]
'''


def render_service(module: str, class_name: str) -> str:
    """渲染服务模板。"""

    return f'''"""{module} 服务层（脚手架模板）。"""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId

from app.models.{module} import {class_name}, utc_now


async def list_items() -> list[{class_name}]:
    """查询列表。"""

    return await {class_name}.find_all().sort("-updated_at").to_list()


async def get_item(item_id: str) -> {class_name} | None:
    """按 ID 查询单条记录。"""

    try:
        object_id = PydanticObjectId(item_id)
    except Exception:
        return None
    return await {class_name}.get(object_id)


async def create_item(payload: dict[str, Any]) -> {class_name}:
    """创建记录（默认字段，业务可按需扩展）。"""

    status = str(payload.get("status") or "enabled").strip().lower()
    if status not in {{"enabled", "disabled"}}:
        status = "enabled"

    item = {class_name}(
        name=str(payload.get("name") or "").strip()[:64],
        description=str(payload.get("description") or "").strip()[:200],
        status=status,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    await item.insert()
    return item


async def update_item(item: {class_name}, payload: dict[str, Any]) -> {class_name}:
    """更新记录（默认字段，业务可按需扩展）。"""

    item.name = str(payload.get("name") or item.name).strip()[:64]
    item.description = str(payload.get("description") or item.description).strip()[:200]

    status = str(payload.get("status") or item.status).strip().lower()
    if status not in {{"enabled", "disabled"}}:
        status = item.status
    item.status = status

    item.updated_at = utc_now()
    await item.save()
    return item


async def delete_item(item: {class_name}) -> None:
    """删除记录。"""

    await item.delete()
'''


def render_page(module: str, title: str) -> str:
    """渲染页面模板。"""

    return f'''{{% extends "base.html" %}}

{{% block content %}}
<div class="space-y-4">
  <section class="card p-5">
    <div class="flex items-center justify-between gap-3">
      <h1 class="text-lg font-semibold text-slate-900">{title}</h1>
      <p class="text-sm text-slate-500">脚手架已生成，请按业务补充筛选与统计。</p>
    </div>
  </section>

  <section>
    {{% include "partials/{module}_table.html" %}}
  </section>
</div>
{{% endblock %}}
'''


def render_table(module: str, title: str) -> str:
    """渲染表格 partial 模板。"""

    return f'''<div id="{module}-table" class="card p-5" {{% if request.state.permission_flags.resources.get("{module}", {{"delete": False}})['delete'] %}}data-bulk-scope{{% endif %}}>
  {{% set perm = request.state.permission_flags.resources.get("{module}", {{"create": False, "read": False, "update": False, "delete": False}}) %}}
  {{% set show_action_col = perm['update'] or perm['delete'] %}}
  <div class="flex flex-wrap items-center justify-between gap-3">
    <div>
      <h2 class="text-lg font-semibold text-slate-900">{title}列表</h2>
      <p class="mt-1 text-sm text-slate-500">共 {{{{ items | length }}}} 条记录</p>
    </div>

    <div class="flex items-center gap-2">
      <button
        class="btn-ghost px-3"
        hx-get="/admin/{module}/table"
        hx-target="#{module}-table"
        hx-swap="outerHTML"
        hx-indicator="#global-indicator"
        title="刷新"
        aria-label="刷新"
      >
        <i class="fa-solid fa-rotate-right" aria-hidden="true"></i>
      </button>
      {{% if perm['create'] %}}
        <button
          class="btn-primary"
          hx-get="/admin/{module}/new"
          hx-target="#modal-body"
          hx-swap="innerHTML"
          hx-indicator="#global-indicator"
          x-on:click="modalOpen = true"
        >
          新建
        </button>
      {{% endif %}}
    </div>
  </div>

  {{% if perm['delete'] %}}
    <form
      class="mt-4 space-y-4 pb-20"
    >
      <input type="hidden" name="csrf_token" value="{{{{ request.state.csrf_token or '' }}}}" />

      <div class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50/60 p-3">
        <p class="text-xs text-slate-500">已选 <span data-bulk-count>0</span> 项</p>
        <button type="button" class="btn-ghost px-3" data-bulk-action="invert">反选</button>
      </div>

      <div class="overflow-x-auto rounded-lg border border-slate-200">
        <table class="w-full min-w-[780px] text-left text-sm">
          <thead class="bg-slate-50 text-slate-600">
            <tr>
              <th class="w-12 px-4 py-3 text-center font-medium">
                <input type="checkbox" class="h-4 w-4" data-bulk-master aria-label="全选本页" />
              </th>
              <th class="px-4 py-3 font-medium">ID</th>
              <th class="px-4 py-3 font-medium">名称</th>
              {{% if show_action_col %}}<th class="px-4 py-3 text-right font-medium">操作</th>{{% endif %}}
            </tr>
          </thead>
          <tbody>
            {{% for item in items %}}
              <tr class="border-t border-slate-100">
                <td class="px-4 py-3 text-center">
                  <input type="checkbox" class="h-4 w-4" name="selected_ids" value="{{{{ item.id }}}}" data-bulk-item />
                </td>
                <td class="px-4 py-3 text-slate-500">{{{{ item.id if item.id is defined else '-' }}}}</td>
                <td class="px-4 py-3 text-slate-900">{{{{ item.name if item.name is defined else '-' }}}}</td>
                {{% if show_action_col %}}
                  <td class="px-4 py-3 text-right">
                    {{% if perm['update'] %}}
                      <button
                        type="button"
                        class="btn-link"
                        hx-get="/admin/{module}/{{{{ item.id }}}}/edit"
                        hx-target="#modal-body"
                        hx-swap="innerHTML"
                        hx-indicator="#global-indicator"
                        x-on:click="modalOpen = true"
                      >
                        编辑
                      </button>
                    {{% endif %}}
                    {{% if perm['delete'] %}}
                      <button
                        type="button"
                        class="btn-link {{% if perm['update'] %}}ml-3 {{% endif %}}text-red-500 hover:text-red-600"
                        hx-delete="/admin/{module}/{{{{ item.id }}}}"
                        hx-target="#{module}-table"
                        hx-swap="outerHTML"
                        hx-confirm="确认删除该记录吗？"
                        hx-indicator="#global-indicator"
                      >
                        删除
                      </button>
                    {{% endif %}}
                  </td>
                {{% endif %}}
              </tr>
            {{% else %}}
              <tr>
                <td class="px-4 py-8 text-center text-sm text-slate-500" colspan="4">暂无数据，请先创建记录。</td>
              </tr>
            {{% endfor %}}
          </tbody>
        </table>
      </div>

      <div
        class="bulk-fixed-layer fixed bottom-0 z-20 hidden h-16 bg-slate-900/10 backdrop-blur-[2px]"
        data-bulk-overlay
        aria-hidden="true"
      ></div>
      <div
        class="bulk-fixed-layer fixed bottom-0 z-30 hidden border-t border-slate-200/80 bg-white/65 shadow-[0_-8px_20px_rgba(15,23,42,0.12)] backdrop-blur-md"
        data-bulk-bottom
      >
        <div class="mx-auto flex w-full max-w-[1440px] items-center justify-between gap-3 px-4 py-3">
          <p class="text-sm text-slate-700">已选择 <strong data-bulk-count>0</strong> 项</p>
          <button
            type="button"
            class="btn-ghost text-red-500 hover:text-red-600"
            data-bulk-submit
            hx-post="/admin/{module}/bulk-delete"
            hx-include="closest form"
            hx-target="#{module}-table"
            hx-swap="outerHTML"
            hx-indicator="#global-indicator"
            hx-confirm="确认批量删除已勾选的记录吗？"
            disabled
          >
            批量删除
          </button>
        </div>
      </div>
    </form>
  {{% else %}}
    <div class="mt-4 overflow-x-auto rounded-lg border border-slate-200">
      <table class="w-full min-w-[720px] text-left text-sm">
        <thead class="bg-slate-50 text-slate-600">
          <tr>
            <th class="px-4 py-3 font-medium">ID</th>
            <th class="px-4 py-3 font-medium">名称</th>
            {{% if show_action_col %}}<th class="px-4 py-3 text-right font-medium">操作</th>{{% endif %}}
          </tr>
        </thead>
        <tbody>
          {{% for item in items %}}
            <tr class="border-t border-slate-100">
              <td class="px-4 py-3 text-slate-500">{{{{ item.id if item.id is defined else '-' }}}}</td>
              <td class="px-4 py-3 text-slate-900">{{{{ item.name if item.name is defined else '-' }}}}</td>
              {{% if show_action_col %}}
                <td class="px-4 py-3 text-right">
                  {{% if perm['update'] %}}
                    <button
                      class="btn-link"
                      hx-get="/admin/{module}/{{{{ item.id }}}}/edit"
                      hx-target="#modal-body"
                      hx-swap="innerHTML"
                      hx-indicator="#global-indicator"
                      x-on:click="modalOpen = true"
                    >
                      编辑
                    </button>
                  {{% endif %}}
                </td>
              {{% endif %}}
            </tr>
          {{% else %}}
            <tr>
              <td class="px-4 py-8 text-center text-sm text-slate-500" colspan="3">暂无数据，请先创建记录。</td>
            </tr>
          {{% endfor %}}
        </tbody>
      </table>
    </div>
  {{% endif %}}
</div>
'''

def render_form_partial(module: str, title: str) -> str:
    """渲染表单 partial 模板。"""

    return f'''<div class="flex flex-col" style="max-height: calc(100vh - 9rem);">
  <div class="flex items-start justify-between gap-4 border-b border-slate-100 pb-3">
    <div>
      <h2 class="font-display text-2xl text-ink">{{% if mode == "edit" %}}编辑{title}{{% else %}}新建{title}{{% endif %}}</h2>
      <p class="text-sm text-muted">脚手架模板：请补充表单字段与业务校验。</p>
    </div>
    <button class="btn-ghost px-3" x-on:click="modalOpen = false">关闭</button>
  </div>

  <form
    class="mt-4 flex flex-col"
    style="min-height: 0; flex: 1;"
    hx-post="{{{{ action }}}}"
    hx-target="#modal-body"
    hx-swap="innerHTML"
    hx-indicator="#modal-indicator"
  >
    <input type="hidden" name="csrf_token" value="{{{{ request.state.csrf_token or '' }}}}" />

    <div class="space-y-4 overflow-y-auto pr-1" style="min-height: 0; flex: 1;">
      {{% if errors %}}
        <div class="rounded-2xl border border-black/10 bg-white/70 p-3 text-sm text-red-600">
          <p class="font-semibold">请修正以下问题：</p>
          <ul class="mt-2 list-disc pl-5">
            {{% for err in errors %}}
              <li>{{{{ err }}}}</li>
            {{% endfor %}}
          </ul>
        </div>
      {{% endif %}}

      <div>
        <label class="label">名称</label>
        <input name="name" class="input" value="{{{{ form.name if form.name is defined else '' }}}}" />
      </div>

      <div>
        <label class="label">描述</label>
        <input name="description" class="input" value="{{{{ form.description if form.description is defined else '' }}}}" />
      </div>

      <div>
        <label class="label">状态</label>
        <select name="status" class="input">
          <option value="enabled" {{% if form.status is not defined or form.status == "enabled" %}}selected{{% endif %}}>启用</option>
          <option value="disabled" {{% if form.status is defined and form.status == "disabled" %}}selected{{% endif %}}>禁用</option>
        </select>
      </div>
    </div>

    <div class="mt-4 border-t border-slate-100 pt-3">
      <div class="flex flex-wrap items-center justify-end gap-3">
        <button type="button" class="btn-ghost" x-on:click="modalOpen = false">取消</button>
        <button type="submit" class="btn-primary">保存</button>
      </div>
    </div>
  </form>
</div>
'''


def render_test(module: str) -> str:
    """渲染脚手架测试模板。"""

    return f'''from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.unit
def test_{module}_scaffold_files_exist() -> None:
    assert Path("app/models/{module}.py").exists()
    assert Path("app/services/{module}_service.py").exists()
    assert Path("app/apps/admin/controllers/{module}.py").exists()


@pytest.mark.unit
def test_{module}_registry_generated_contains_crud_actions() -> None:
    payload = json.loads(Path("app/apps/admin/registry_generated/{module}.json").read_text(encoding="utf-8"))

    assert payload["node"]["key"] == "{module}"
    assert payload["node"]["mode"] == "table"
    assert payload["node"]["actions"] == ["create", "read", "update", "delete"]
'''


def render_registry(module: str, title: str, group: str, url: str) -> str:
    """渲染注册节点 JSON。"""

    payload = {
        "group_key": group,
        "node": {
            "key": module,
            "name": title,
            "url": url,
            "mode": "table",
            "actions": ["create", "read", "update", "delete"],
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    """脚手架主流程。"""

    args = parse_args()
    module = ensure_module_name(args.module)
    class_name = f"{to_pascal_case(module)}Item"

    title = (args.name or module).strip()
    group = (args.group or "system").strip() or "system"
    url = (args.url or f"/admin/{module}").strip() or f"/admin/{module}"

    files = {
        CONTROLLERS_DIR / f"{module}.py": render_controller(module, title),
        SERVICES_DIR / f"{module}_service.py": render_service(module, class_name),
        MODELS_DIR / f"{module}.py": render_model(module, class_name),
        PAGES_DIR / f"{module}.html": render_page(module, title),
        PARTIALS_DIR / f"{module}_table.html": render_table(module, title),
        PARTIALS_DIR / f"{module}_form.html": render_form_partial(module, title),
        TESTS_DIR / f"test_{module}_scaffold.py": render_test(module),
        REGISTRY_DIR / f"{module}.json": render_registry(module, title, group, url),
    }

    for path, content in files.items():
        write_file(path, content, force=args.force, dry_run=args.dry_run)

    wire_models_init(module, class_name, dry_run=args.dry_run)
    wire_db_models(class_name, dry_run=args.dry_run)

    print("完成：模型已接入 app/models/__init__.py 与 app/db.py。")
    print("完成：请手动在 app/main.py 引入并 include_router 新控制器。")


if __name__ == "__main__":
    main()
