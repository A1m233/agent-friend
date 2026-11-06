from datetime import UTC, datetime
from pathlib import Path

from agent.message_exposure import MessageExposureStore, content_version


def test_display_version_upgrade_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "display.db"
    store = MessageExposureStore(path)
    now = datetime.now(UTC)
    old, new = content_version("第一版"), content_version("第二版")
    assert store.get("s", "m", old)["extent"] == "unknown"
    assert store.record("s", "m", old, "partial", observed_at=now)
    cursor = store.changes(0)["cursor"]
    assert not store.record("s", "m", old, "partial", observed_at=now)
    assert store.changes(cursor)["items"] == []
    assert store.record("s", "m", old, "full", observed_at=now)
    assert not store.record("s", "m", old, "partial", observed_at=now)
    assert MessageExposureStore(path).get("s", "m", old)["extent"] == "full"
    assert store.get("s", "m", new)["extent"] == "unknown"
    assert store.record("s", "m", new, "partial", observed_at=now)
    assert store.get("s", "m", old)["extent"] == "unknown"
    assert store.get("s", "m", new)["read_status"] == "unknown"
    store.delete_session("s")
    assert store.get("s", "m", new)["extent"] == "unknown"


def test_display_cursor_does_not_drop_overflow(tmp_path: Path) -> None:
    store = MessageExposureStore(tmp_path / "display.db")
    now = datetime.now(UTC)
    for i in range(4):
        store.record("s", str(i), content_version(str(i)), "full", observed_at=now)
    first = store.changes(0, limit=2)
    second = store.changes(first["cursor"], limit=2)
    assert first["has_more"] is True
    assert second["has_more"] is False
    assert [row["message_id"] for row in first["items"] + second["items"]] == ["0", "1", "2", "3"]
