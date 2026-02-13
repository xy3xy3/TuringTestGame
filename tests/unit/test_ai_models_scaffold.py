from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.unit
def test_ai_models_scaffold_files_exist() -> None:
    """检查 AI 模型相关文件是否存在。"""
    assert Path("app/models/ai_model.py").exists()
    assert Path("app/services/ai_models_service.py").exists()
    assert Path("app/apps/admin/controllers/ai_models.py").exists()


@pytest.mark.unit
def test_ai_models_registry_generated_contains_crud_actions() -> None:
    """检查注册表生成的 CRUD 操作。"""
    payload = json.loads(Path("app/apps/admin/registry_generated/ai_models.json").read_text(encoding="utf-8"))

    assert payload["node"]["key"] == "ai_models"
    assert payload["node"]["mode"] == "table"
    assert payload["node"]["actions"] == ["create", "read", "update", "delete"]


@pytest.mark.unit
def test_game_models_exist() -> None:
    """检查游戏相关模型文件是否存在。"""
    assert Path("app/models/game_room.py").exists()
    assert Path("app/models/game_player.py").exists()
    assert Path("app/models/game_round.py").exists()
    assert Path("app/models/vote_record.py").exists()