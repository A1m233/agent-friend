from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agent.attachments import AttachmentStore
from agent.session_files import SessionFileService
from agent.sessions import JsonlSessionStore
from agent.sessions.metadata import SessionMetadataStore
from agent.skills import SkillCatalogService, SkillSelection
from agent.system_prompt import DESKTOP_CHAT_SURFACE
from agent.tools import ToolRegistry
from agent.tools.builtin.skills import SkillReadTool

from .test_dynamic_context_cache_continuity import _MutableTimeProvider
from .test_session_files import chunks
from .test_session_rollout import _create, _manager, _open_cached, _RecordingLLM, _run
from .test_skill_conversation import _write_skill


def test_cache_hit_refreshes_current_permission_and_calendar_before_provider(
    tmp_path: Path,
) -> None:
    metadata = SessionMetadataStore(tmp_path / "metadata.json")
    clock = _MutableTimeProvider(datetime(2026, 8, 29, 10, tzinfo=ZoneInfo("Asia/Shanghai")))
    first = _manager(tmp_path, _RecordingLLM(), metadata_store=metadata, time_provider=clock)
    sid = _create(first)
    before, changed = metadata.compare_and_set_approval_mode(
        sid, expected_version=0, mode="session_auto"
    )
    assert changed
    _run(first, sid, "first")
    first.close()

    now, changed = metadata.compare_and_set_approval_mode(
        sid, expected_version=before.version, mode="per_request"
    )
    assert changed
    clock.value = datetime(2026, 8, 30, 11, tzinfo=ZoneInfo("Asia/Shanghai"))
    llm = _RecordingLLM()
    restarted = _manager(tmp_path, llm, metadata_store=metadata, time_provider=clock)
    try:
        conversation = restarted.start_conversation(_open_cached(restarted, sid))
        assert conversation._session_model_context.snapshot.approval_mode == "session_auto"
        list(conversation.stream("after restart", surface=DESKTOP_CHAT_SURFACE))
        permissions = [
            item["content"]
            for item in llm.received[0]
            if "<agent_friend_permission_context" in str(item.get("content"))
        ]
        calendars = [
            item["content"]
            for item in llm.received[0]
            if 'contributor="calendar"' in str(item.get("content"))
        ]
        assert 'approval_mode="session_auto"' in permissions[0]
        assert 'approval_mode="per_request"' in permissions[-1]
        assert f'version="{now.version}"' in permissions[-1]
        assert 'kind="delta"' in permissions[-1]
        assert 'date="2026-08-30"' in calendars[-1]
        assert 'kind="delta"' in calendars[-1]
    finally:
        restarted.close()


def test_cache_hit_reads_current_skill_version_and_expires_old_activation(tmp_path: Path) -> None:
    personal = tmp_path / "skills"
    _write_skill(personal, "test-skill", "FIRST VERSION")
    service = SkillCatalogService(
        personal_root=personal, shared_root=tmp_path / "shared", watcher_enabled=False
    )
    skill = next(item for item in service.initialize().entries if item.name == "test-skill")
    registry = ToolRegistry([SkillReadTool(service)])
    first = _manager(tmp_path, _RecordingLLM(), skill_service=service, registry=registry)
    sid = _create(first)
    conversation = first.start_conversation(first.open_for_execution(sid))
    list(conversation.stream("first", skill_selections=(SkillSelection(skill.id, skill.version),)))
    first.close()

    _write_skill(personal, "test-skill", "SECOND VERSION")
    service.refresh()
    llm = _RecordingLLM()
    restarted = _manager(tmp_path, llm, skill_service=service, registry=registry)
    try:
        conversation = restarted.start_conversation(_open_cached(restarted, sid))
        list(conversation.stream("again", skill_selections=(SkillSelection(skill.id),)))
        bindings = [
            item["content"]
            for item in llm.received[-1]
            if "<skill_bindings" in str(item.get("content"))
        ]
        assert "SECOND VERSION" in bindings[-1] and "FIRST VERSION" not in bindings[-1]
        list(conversation.stream("unrelated question"))
        bindings = [
            item["content"]
            for item in llm.received[-1]
            if "<skill_bindings" in str(item.get("content"))
        ]
        assert "No Skill is active" in bindings[-1]
    finally:
        restarted.close()
        service.stop()


def test_cache_hit_revalidates_attachment_and_never_sends_a_missing_original(
    tmp_path: Path,
) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    assets = AttachmentStore(tmp_path / "assets")
    _, ref = asyncio.run(assets.import_stream(chunks(b"fixture bytes"), display_name="input.dat"))
    first = _manager(
        tmp_path,
        _RecordingLLM(),
        store=store,
        session_file_service=SessionFileService(store, assets, tmp_path / "workspace"),
    )
    sid = _create(first)
    conversation = first.start_conversation(first.open_for_execution(sid))
    list(conversation.stream("first", attachments=[ref]))
    first.close()
    assets.verify(ref.asset_id).unlink()

    cold_store = JsonlSessionStore(store.base_dir)
    llm = _RecordingLLM()
    restarted = _manager(
        tmp_path,
        llm,
        store=cold_store,
        session_file_service=SessionFileService(cold_store, assets, tmp_path / "workspace"),
    )
    try:
        conversation = restarted.start_conversation(_open_cached(restarted, sid))
        with pytest.raises(ValueError, match="原件缺失或校验失败"):
            list(conversation.stream("read again", attachments=[ref], user_event_id="next-input"))
        assert not llm.received
        accepted = [
            event for event in cold_store.load(sid).active_events if event.uuid == "next-input"
        ]
        assert len(accepted) == 1 and accepted[0].payload["attachments"]
    finally:
        restarted.close()
