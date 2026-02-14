from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import prompt_templates_service


@pytest.mark.unit
def test_normalize_template_payload_trims_and_clamps() -> None:
    """模板载荷应去空白并裁剪长度。"""
    payload = prompt_templates_service.normalize_template_payload(
        {
            "name": "  模板A  ",
            "description": " 描述 ",
            "prompt_text": "  内容  ",
            "status": "ENABLED",
        }
    )

    assert payload["name"] == "模板A"
    assert payload["description"] == "描述"
    assert payload["prompt_text"] == "内容"
    assert payload["status"] == "enabled"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_seed_builtin_templates_skips_existing(monkeypatch) -> None:
    """一键补齐内置模板时，同名模板应跳过。"""
    created_names: list[str] = []
    exists_map = {prompt_templates_service.BUILTIN_PROMPT_TEMPLATES[0]["name"]}

    async def fake_get_item_by_name(name: str):
        if name in exists_map:
            return SimpleNamespace(name=name)
        return None

    async def fake_create_item(payload):
        created_names.append(payload["name"])
        return SimpleNamespace(id="x", name=payload["name"])

    monkeypatch.setattr(prompt_templates_service, "get_item_by_name", fake_get_item_by_name)
    monkeypatch.setattr(prompt_templates_service, "create_item", fake_create_item)

    stats = await prompt_templates_service.seed_builtin_templates()

    assert stats["total"] == len(prompt_templates_service.BUILTIN_PROMPT_TEMPLATES)
    assert stats["skipped"] == 1
    assert stats["created"] == len(prompt_templates_service.BUILTIN_PROMPT_TEMPLATES) - 1
    assert len(created_names) == len(prompt_templates_service.BUILTIN_PROMPT_TEMPLATES) - 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_enabled_template_options_returns_display_fields(monkeypatch) -> None:
    """灵魂注入下拉选项应返回 id、名称、描述与提示词内容。"""
    items = [
        SimpleNamespace(id="a1", name="模板1", description="描述1", prompt_text="内容1"),
        SimpleNamespace(id="a2", name="模板2", description="", prompt_text="内容2"),
    ]

    class FakeFindResult:
        def sort(self, _value):
            return self

        async def to_list(self):
            return items

    monkeypatch.setattr(prompt_templates_service.PromptTemplatesItem, "find", lambda _query: FakeFindResult())

    options = await prompt_templates_service.list_enabled_template_options()

    assert options == [
        {"id": "a1", "name": "模板1", "description": "描述1", "prompt_text": "内容1"},
        {"id": "a2", "name": "模板2", "description": "", "prompt_text": "内容2"},
    ]
