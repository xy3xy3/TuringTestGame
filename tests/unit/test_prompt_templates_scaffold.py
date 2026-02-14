from __future__ import annotations

import json
from pathlib import Path

import pytest


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
