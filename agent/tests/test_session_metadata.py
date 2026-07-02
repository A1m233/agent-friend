"""会话管理 metadata store 测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent import SessionMetadataStore, SessionPersistError


def test_metadata_store_persists_title_and_archive_state(tmp_path: Path) -> None:
    path = tmp_path / "metadata.json"
    store = SessionMetadataStore(path)
    archived_at = datetime(2026, 7, 2, 12, 30, tzinfo=UTC)

    store.set_display_title("sid-1", "展示标题")
    store.set_archived_at("sid-1", archived_at)

    loaded = SessionMetadataStore(path).get("sid-1")
    assert loaded.display_title == "展示标题"
    assert loaded.archived_at == archived_at


def test_metadata_store_removes_empty_items(tmp_path: Path) -> None:
    store = SessionMetadataStore(tmp_path / "metadata.json")

    store.set_display_title("sid-1", "展示标题")
    store.set_display_title("sid-1", None)

    assert store.load_all() == {}


def test_metadata_store_delete_many(tmp_path: Path) -> None:
    store = SessionMetadataStore(tmp_path / "metadata.json")
    store.set_display_title("sid-1", "一")
    store.set_display_title("sid-2", "二")

    store.delete_many(["sid-1", "missing"])

    assert set(store.load_all()) == {"sid-2"}


def test_metadata_store_rejects_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "metadata.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(SessionPersistError, match="metadata JSON 损坏"):
        SessionMetadataStore(path).load_all()
