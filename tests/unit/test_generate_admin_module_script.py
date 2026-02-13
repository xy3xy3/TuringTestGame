from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def scaffold_module():
    """加载脚手架脚本模块，便于直接验证模板渲染函数。"""

    script_path = Path("scripts/generate_admin_module.py")
    spec = importlib.util.spec_from_file_location("generate_admin_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载脚手架脚本")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_render_service_uses_set_literal(scaffold_module) -> None:
    """状态白名单应渲染为集合字面量，避免 f-string 误替换为元组字符串。"""

    rendered = scaffold_module.render_service("demo_inventory", "DemoInventoryItem")

    assert 'if status not in {"enabled", "disabled"}:' in rendered
    assert "('enabled', 'disabled')" not in rendered


@pytest.mark.unit
def test_main_files_include_model(scaffold_module) -> None:
    """脚手架产物应包含模型文件路径。"""

    rendered = scaffold_module.render_test("demo_inventory")

    assert 'Path("app/models/demo_inventory.py").exists()' in rendered


@pytest.mark.unit
def test_render_form_partial_targets_modal_body(scaffold_module) -> None:
    """脚手架表单应在弹窗内提交并回显错误。"""

    rendered = scaffold_module.render_form_partial("demo_inventory", "示例模块")

    assert 'hx-target="#modal-body"' in rendered
    assert 'hx-swap="innerHTML"' in rendered


@pytest.mark.unit
def test_render_form_partial_uses_fixed_header_footer_layout(scaffold_module) -> None:
    """脚手架弹窗应固定头部与底部，仅中间区域滚动。"""

    rendered = scaffold_module.render_form_partial("demo_inventory", "示例模块")

    assert 'max-height: calc(100vh - 9rem);' in rendered
    assert 'overflow-y-auto' in rendered
    assert 'border-b border-slate-100 pb-3' in rendered
    assert 'border-t border-slate-100 pt-3' in rendered


@pytest.mark.unit
def test_render_table_includes_bulk_delete_controls(scaffold_module) -> None:
    """脚手架表格应包含全选/反选和批量删除能力。"""

    rendered = scaffold_module.render_table("demo_inventory", "示例模块")

    assert 'data-bulk-scope' in rendered
    assert 'data-bulk-action="invert"' in rendered
    assert 'data-bulk-submit' in rendered
    assert 'data-bulk-bottom' in rendered
    assert 'data-bulk-overlay' in rendered
    assert 'hx-post="/admin/demo_inventory/bulk-delete"' in rendered
    assert 'hx-include="closest form"' in rendered
    assert 'hx-confirm="确认批量删除已勾选的记录吗？"' in rendered
    assert 'fa-rotate-right' in rendered


@pytest.mark.unit
def test_render_controller_has_htmx_modal_error_strategy(scaffold_module) -> None:
    """脚手架控制器应内置 HTMX 弹窗错误回显策略。"""

    rendered = scaffold_module.render_controller("demo_inventory", "示例模块")

    assert "def _is_htmx_request(request: Request) -> bool:" in rendered
    assert "error_status = 200 if _is_htmx_request(request) else 422" in rendered
    assert "HX-Retarget" in rendered
    assert "HX-Reswap" in rendered
    assert '@router.post("/demo_inventory/bulk-delete", response_class=HTMLResponse)' in rendered
