from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.apps.admin.registry import build_admin_tree


@pytest.mark.unit
def test_prompt_templates_scaffold_files_exist() -> None:
    assert Path("app/models/prompt_templates.py").exists()
    assert Path("app/services/prompt_templates_service.py").exists()
    assert Path("app/apps/admin/controllers/prompt_templates.py").exists()


@pytest.mark.unit
def test_prompt_templates_registry_generated_contains_crud_actions() -> None:
    payload = json.loads(Path("app/apps/admin/registry_generated/prompt_templates.json").read_text(encoding="utf-8"))

    assert payload["node"]["key"] == "prompt_templates"
    assert payload["node"]["mode"] == "table"
    assert payload["node"]["actions"] == ["create", "read", "update", "delete"]


@pytest.mark.unit
def test_prompt_templates_exists_in_admin_tree() -> None:
    """提示词模板应出现在后台权限树，避免导航项丢失。"""
    tree = build_admin_tree()
    system_group = next(group for group in tree if group.get("key") == "system")
    children = system_group.get("children", [])
    prompt_templates_node = next(node for node in children if node.get("key") == "prompt_templates")

    assert prompt_templates_node["url"] == "/admin/prompt_templates"
    assert prompt_templates_node["actions"] == ["create", "read", "update", "delete"]
