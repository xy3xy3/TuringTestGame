"""后台页面注册表（用于权限树展示）。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable

RESOURCE_ACTION_TEMPLATES: dict[str, list[str]] = {
    "table": ["create", "read", "update", "delete"],
    "settings": ["read", "update"],
    "self_service": ["read", "update_self"],
    "operation": ["read", "trigger", "restore", "delete"],
}
VALID_MODES = set(RESOURCE_ACTION_TEMPLATES)
VALID_ACTIONS = {
    action
    for actions in RESOURCE_ACTION_TEMPLATES.values()
    for action in actions
}
REGISTRY_GENERATED_DIR = Path(__file__).resolve().parent / "registry_generated"

BASE_ADMIN_TREE = [
    {
        "key": "dashboard",
        "name": "首页",
        "children": [
            {
                "key": "dashboard_home",
                "name": "仪表盘",
                "url": "/admin/dashboard",
                "actions": ["read"],
                "mode": "settings",
            }
        ],
    },
    {
        "key": "security",
        "name": "权限与安全",
        "children": [
            {
                "key": "rbac",
                "name": "角色与权限",
                "url": "/admin/rbac",
                "actions": ["create", "read", "update", "delete"],
                "mode": "table",
            }
        ],
    },
    {
        "key": "accounts",
        "name": "账号管理",
        "children": [
            {
                "key": "admin_users",
                "name": "管理员账号",
                "url": "/admin/users",
                "actions": ["create", "read", "update", "delete"],
                "mode": "table",
            },
            {
                "key": "profile",
                "name": "个人资料",
                "url": "/admin/profile",
                "actions": ["read", "update_self"],
                "mode": "self_service",
                "assignable": False,
                "require_read": False,
            },
            {
                "key": "password",
                "name": "修改密码",
                "url": "/admin/password",
                "actions": ["read", "update_self"],
                "mode": "self_service",
                "assignable": False,
                "require_read": False,
            },
        ],
    },
    {
        "key": "system",
        "name": "系统设置",
        "children": [
            {
                "key": "config",
                "name": "系统配置",
                "url": "/admin/config",
                "actions": ["read", "update"],
                "mode": "settings",
            },
            {
                "key": "operation_logs",
                "name": "操作日志",
                "url": "/admin/logs",
                "actions": ["read", "delete"],
                "mode": "operation",
            },
            {
                "key": "backup_config",
                "name": "备份配置",
                "url": "/admin/backup/config",
                "actions": ["read", "update"],
                "mode": "settings",
            },
            {
                "key": "backup_records",
                "name": "备份任务",
                "url": "/admin/backup",
                "actions": ["read", "trigger", "restore", "delete"],
                "mode": "operation",
            },
        ],
    },
]


def _normalize_mode(raw_mode: Any) -> str:
    """规范化资源模式，非法值回退为 table。"""

    mode = str(raw_mode or "").strip().lower()
    return mode if mode in VALID_MODES else "table"


def _normalize_actions(raw_actions: Any, mode: str) -> list[str]:
    """按模式清洗动作列表，确保动作合法且去重。"""

    actions_source = raw_actions
    if not isinstance(actions_source, list):
        actions_source = RESOURCE_ACTION_TEMPLATES.get(mode, RESOURCE_ACTION_TEMPLATES["table"])

    actions = [
        str(action).strip().lower()
        for action in actions_source
        if str(action).strip().lower() in VALID_ACTIONS
    ]
    return list(dict.fromkeys(actions))


def _normalize_generated_node(payload: dict[str, Any]) -> dict[str, Any] | None:
    """清洗外部注册节点，避免脏数据污染权限树。"""

    group_key = str(payload.get("group_key") or "").strip()
    node = payload.get("node")
    if not group_key or not isinstance(node, dict):
        return None

    key = str(node.get("key") or "").strip()
    name = str(node.get("name") or "").strip()
    url = str(node.get("url") or "").strip()
    mode = _normalize_mode(node.get("mode"))
    actions = _normalize_actions(node.get("actions"), mode)
    assignable = node.get("assignable")
    require_read = node.get("require_read")

    if not key or not name or not url or not actions:
        return None

    if not isinstance(assignable, bool):
        assignable = mode != "self_service"
    if not isinstance(require_read, bool):
        require_read = mode != "self_service"

    normalized = {
        "group_key": group_key,
        "node": {
            "key": key,
            "name": name,
            "url": url,
            "actions": actions,
            "mode": mode,
            "assignable": assignable,
            "require_read": require_read,
        },
    }
    return normalized


def _load_generated_nodes() -> list[dict[str, Any]]:
    """加载脚手架生成的注册节点（JSON）。"""

    if not REGISTRY_GENERATED_DIR.exists():
        return []

    nodes: list[dict[str, Any]] = []
    for path in sorted(REGISTRY_GENERATED_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue

        normalized = _normalize_generated_node(payload)
        if normalized is not None:
            nodes.append(normalized)
    return nodes


def build_admin_tree() -> list[dict[str, Any]]:
    """构建最终权限树：基础树 + 脚手架扩展。"""

    tree = copy.deepcopy(BASE_ADMIN_TREE)

    def normalize_tree_nodes(nodes: list[dict[str, Any]]) -> None:
        """递归清洗树节点的 mode/actions 元数据。"""

        for node in nodes:
            children = node.get("children")
            if isinstance(children, list) and children:
                normalize_tree_nodes(children)
                continue

            mode = _normalize_mode(node.get("mode"))
            node["mode"] = mode
            node["actions"] = _normalize_actions(node.get("actions"), mode)
            if not isinstance(node.get("assignable"), bool):
                node["assignable"] = mode != "self_service"
            if not isinstance(node.get("require_read"), bool):
                node["require_read"] = mode != "self_service"

    normalize_tree_nodes(tree)

    group_map = {
        str(group.get("key") or ""): group
        for group in tree
    }

    for item in _load_generated_nodes():
        group_key = item["group_key"]
        group = group_map.get(group_key)
        if not group:
            group = {"key": group_key, "name": group_key, "children": []}
            tree.append(group)
            group_map[group_key] = group

        children = group.setdefault("children", [])
        existing_index = next(
            (index for index, child in enumerate(children) if child.get("key") == item["node"]["key"]),
            None,
        )
        if existing_index is None:
            children.append(item["node"])
        else:
            children[existing_index] = item["node"]

    return tree


ADMIN_TREE = build_admin_tree()


def iter_leaf_nodes(tree: list[dict]) -> Iterable[dict]:
    """遍历叶子节点。"""

    for node in tree:
        children = node.get("children")
        if children:
            yield from iter_leaf_nodes(children)
        else:
            yield node


def iter_assignable_leaf_nodes(tree: list[dict]) -> Iterable[dict]:
    """遍历可分配给角色的叶子节点。"""

    for node in iter_leaf_nodes(tree):
        if bool(node.get("assignable", True)):
            yield node
