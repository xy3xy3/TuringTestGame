from __future__ import annotations

import pytest

from app.models import AdminUser, BackupRecord, Role


@pytest.mark.unit
def test_role_slug_unique_index_defined() -> None:
    indexes = Role.Settings.indexes
    assert indexes, "Role 模型未定义索引"
    assert any(index.document.get("unique") and index.document.get("name") == "uniq_role_slug" for index in indexes)


@pytest.mark.unit
def test_admin_username_unique_index_defined() -> None:
    indexes = AdminUser.Settings.indexes
    assert indexes, "AdminUser 模型未定义索引"
    assert any(
        index.document.get("unique") and index.document.get("name") == "uniq_admin_username"
        for index in indexes
    )


@pytest.mark.unit
def test_backup_record_sort_index_defined() -> None:
    indexes = BackupRecord.Settings.indexes
    assert indexes, "BackupRecord 模型未定义索引"
    assert any(index.document.get("name") == "idx_backup_created_at" for index in indexes)
