from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from agent.sessions.catalog import SessionCatalog, normalize_session_title
from agent.sessions.metadata import SessionMetadataStore
from agent.sessions.store import SessionStore, SessionSummary


class _SummaryStore:
    def __init__(self, summaries: list[SessionSummary]) -> None:
        self.summaries = summaries

    def list(self) -> list[SessionSummary]:
        return list(self.summaries)


def _summary(session_id: str, title: str, minutes: int) -> SessionSummary:
    now = datetime(2026, 7, 28, 10, tzinfo=UTC)
    return SessionSummary(
        session_id=session_id,
        title=title,
        created_at=now - timedelta(days=1),
        updated_at=now + timedelta(minutes=minutes),
        persona="default",
        model="test/model",
    )


def test_catalog_resolves_renamed_title_leading_alias_and_archived(tmp_path: Path) -> None:
    store = _SummaryStore([_summary("im-1", "initial", 1)])
    metadata = SessionMetadataStore(tmp_path / "metadata.json")
    metadata.set_display_title("im-1", "诗欣IM（用户在用）")
    metadata.set_archived_at("im-1", datetime(2026, 7, 28, tzinfo=UTC))

    catalog = SessionCatalog(cast(SessionStore, store), metadata)
    resolution = catalog.resolve("诗欣IM")

    assert resolution.status == "matched"
    assert resolution.matched is not None
    assert resolution.matched.display_title == "诗欣IM（用户在用）"
    assert resolution.matched.archived is True


def test_catalog_reports_ambiguous_fragment_without_guessing() -> None:
    catalog = SessionCatalog(
        cast(
            SessionStore,
            _SummaryStore(
                [
                    _summary("a", "项目讨论 A", 1),
                    _summary("b", "项目讨论 B", 2),
                ]
            ),
        ),
    )

    resolution = catalog.resolve("项目讨论")

    assert resolution.status == "ambiguous"
    assert [item.session_id for item in resolution.candidates] == ["b", "a"]


def test_catalog_ignores_metadata_without_raw_session(tmp_path: Path) -> None:
    metadata = SessionMetadataStore(tmp_path / "metadata.json")
    metadata.set_display_title("deleted", "已经删除")
    catalog = SessionCatalog(cast(SessionStore, _SummaryStore([])), metadata)

    assert catalog.resolve("已经删除").status == "not_found"


def test_title_normalization_uses_nfkc_casefold_and_whitespace() -> None:
    assert normalize_session_title("  ＡＢＣ\n IM  ") == "abc im"
