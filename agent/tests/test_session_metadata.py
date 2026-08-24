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


def test_metadata_store_approval_mode_defaults_and_persists_version(tmp_path: Path) -> None:
    path = tmp_path / "metadata.json"
    store = SessionMetadataStore(path)

    assert store.get("sid-1").approval_mode == "per_request"
    assert store.get("sid-1").approval_mode_version == 0

    enabled, changed = store.compare_and_set_approval_mode(
        "sid-1", expected_version=0, mode="session_auto"
    )
    disabled, changed_back = store.compare_and_set_approval_mode(
        "sid-1", expected_version=1, mode="per_request"
    )

    assert changed is True
    assert enabled.mode == "session_auto"
    assert enabled.version == 1
    assert changed_back is True
    assert disabled.mode == "per_request"
    assert disabled.version == 2
    reloaded = SessionMetadataStore(path).get("sid-1")
    assert reloaded.approval_mode == "per_request"
    assert reloaded.approval_mode_version == 2


def test_metadata_store_rejects_stale_approval_mode_version(tmp_path: Path) -> None:
    store = SessionMetadataStore(tmp_path / "metadata.json")
    store.compare_and_set_approval_mode("sid-1", expected_version=0, mode="session_auto")

    state, changed = store.compare_and_set_approval_mode(
        "sid-1", expected_version=0, mode="per_request"
    )

    assert changed is False
    assert state.mode == "session_auto"
    assert state.version == 1


def test_metadata_store_preserves_mode_when_title_and_archive_change(tmp_path: Path) -> None:
    store = SessionMetadataStore(tmp_path / "metadata.json")
    store.compare_and_set_approval_mode("sid-1", expected_version=0, mode="session_auto")

    store.set_display_title("sid-1", "展示标题")
    store.set_archived_at("sid-1", datetime(2026, 7, 2, 12, 30, tzinfo=UTC))

    loaded = store.get("sid-1")
    assert loaded.approval_mode == "session_auto"
    assert loaded.approval_mode_version == 1
