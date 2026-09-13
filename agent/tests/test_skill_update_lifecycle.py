from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent.command_runtime.skill_snapshot import prepare_skill_snapshot
from agent.command_runtime.tools import ExecCommandTool
from agent.skills import SkillCatalogService, SkillSelection
from agent.skills.lifecycle import apply_skill_update
from agent.skills.models import SkillRecord, SkillTurnSnapshot
from agent.skills.package_snapshot import package_digest
from agent.skills.update_receipts import PendingSkillUpdate, SkillUpdateResult
from agent.tool_runtime import ToolRunContext
from agent.tools.builtin.skills import (
    SKILL_CATALOG_SNAPSHOT_ARG,
    SKILL_RUN_CONTEXT_ARG,
    SkillReadTool,
)
from agent.tools.protocol import ToolResult


def _write(root: Path, version: str, *, implicit: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: sample\ndescription: A sample workflow\n---\n\n{version}\n", encoding="utf-8"
    )
    (root / "run.py").write_text(f"print({version!r})\n", encoding="utf-8")
    (root / "agents").mkdir(exist_ok=True)
    (root / "agents" / "openai.yaml").write_text(
        f"policy:\n  allow_implicit_invocation: {str(implicit).lower()}\n", encoding="utf-8"
    )


def _setup(tmp_path: Path) -> tuple[Path, SkillCatalogService, SkillRecord]:
    root = tmp_path / "personal" / "sample"
    _write(root, "V1")
    service = SkillCatalogService(
        personal_root=root.parent, shared_root=tmp_path / "shared", watcher_enabled=False
    )
    catalog = service.initialize()
    skill = next(r for r in catalog.entries if r.name == "sample")
    return root, service, skill


def _apply(
    service: SkillCatalogService,
    snapshot: SkillTurnSnapshot,
    context: ToolRunContext,
    skill_id: str,
    root: Path,
) -> ToolResult:
    result = ToolResult(
        '{"process_status":"exited","exit_code":0}',
        meta={
            "_skill_update": SkillUpdateResult(skill_id, "previous", package_digest(root)),
        },
    )
    return apply_skill_update(
        result, service=service, snapshot=snapshot, context=context, runtime_capabilities="test"
    )


def _read(service: SkillCatalogService, args: dict[str, object]) -> ToolResult:
    result = SkillReadTool(service).invoke(args)
    context = args[SKILL_RUN_CONTEXT_ARG]
    assert isinstance(context, ToolRunContext)
    activation = result.meta.get("_activated_skill")
    if activation is not None:
        context.bind_skill(activation)
    blocked = result.meta.get("_skill_blocked")
    if isinstance(blocked, str):
        context.block_skill(blocked, str(result.meta.get("error_kind")))
    return result


def test_update_rebinds_body_files_and_commands_without_changing_old_process_resources(
    tmp_path: Path,
) -> None:
    root, service, skill = _setup(tmp_path)
    turn = service.begin_turn((SkillSelection(skill.id),))
    context = ToolRunContext.create("update then continue")
    old = context.bind_skill(turn.activations[0])
    old_scope = context.filesystem_authorizations.scopes()[0].scope_id
    old_command = prepare_skill_snapshot(
        context, old, entry="run.py", execution_root=tmp_path / "commands"
    )
    _write(root, "V2")
    result = _apply(service, turn, context, skill.id, root)
    assert not result.is_error
    assert json.loads(result.text)["skill_update"]["activation"] == "reloaded"
    assert "V2" in result.text
    new = context.skill_activations[skill.id]
    assert new.contents != old.contents
    assert "V2" in (new.resource_root / "run.py").read_text()
    assert context.filesystem_authorizations.scope(old_scope) is None
    new_command = prepare_skill_snapshot(
        context, new, entry="run.py", execution_root=tmp_path / "commands"
    )
    assert new_command.fingerprint != old_command.fingerprint
    assert "V1" in old_command.entry_path.read_text()
    assert "V2" in new_command.entry_path.read_text()
    for script_path in (root / "run.py", new.resource_root / "run.py"):
        for path_string in (str(script_path), script_path.as_posix()):
            rewritten = ExecCommandTool._rewrite_skill_entry(f'python "{path_string}"', new_command)
            assert str(new_command.entry_path) in rewritten
    context.close_commands()
    assert not old.resource_root.exists()
    assert not new.resource_root.exists()
    assert not old_command.snapshot_root.exists()
    assert not new_command.snapshot_root.exists()


def test_unloaded_update_is_lazy_and_can_activate_in_the_same_turn(tmp_path: Path) -> None:
    root, service, skill = _setup(tmp_path)
    turn = service.begin_turn()
    context = ToolRunContext.create("update")
    _write(root, "V2")
    result = _apply(service, turn, context, skill.id, root)
    assert not context.skill_activations
    assert "instructions" not in json.loads(result.text)["skill_update"]
    activated = _read(
        service,
        {
            "skill_id": skill.id,
            "reason": "continue task",
            SKILL_CATALOG_SNAPSHOT_ARG: turn.catalog,
            SKILL_RUN_CONTEXT_ARG: context,
        },
    )
    assert not activated.is_error
    assert "V2" in activated.text
    context.close_commands()


def test_script_only_update_changes_binding_and_repeated_activation_is_deduplicated(
    tmp_path: Path,
) -> None:
    root, service, skill = _setup(tmp_path)
    turn = service.begin_turn((SkillSelection(skill.id),))
    context = ToolRunContext.create("continue")
    old = context.bind_skill(turn.activations[0])
    (root / "run.py").write_text("print('V2')\n", encoding="utf-8")
    assert not _apply(service, turn, context, skill.id, root).is_error
    new = context.skill_activations[skill.id]
    assert old.version == new.version
    assert old.package_version != new.package_version
    again = _apply(service, turn, context, skill.id, root)
    assert json.loads(again.text)["skill_update"]["activation"] == "already_loaded"
    assert "instructions" not in json.loads(again.text)["skill_update"]
    assert context.skill_activations[skill.id] is new
    context.close_commands()


def test_failed_reload_blocks_old_binding_and_recovers_in_same_turn(tmp_path: Path) -> None:
    root, service, skill = _setup(tmp_path)
    turn = service.begin_turn((SkillSelection(skill.id),))
    context = ToolRunContext.create("continue")
    context.bind_skill(turn.activations[0])
    (root / "SKILL.md").write_text("invalid", encoding="utf-8")
    failed = _apply(service, turn, context, skill.id, root)
    assert failed.is_error
    assert skill.id in context.blocked_skills
    assert skill.id not in context.skill_activations
    assert not context.filesystem_authorizations.scopes()
    _write(root, "V3", implicit=False)
    recovered = _read(
        service,
        {
            "skill_id": skill.id,
            "reason": "repair",
            SKILL_CATALOG_SNAPSHOT_ARG: turn.catalog,
            SKILL_RUN_CONTEXT_ARG: context,
        },
    )
    assert not recovered.is_error  # host caller origin survives failed reload
    assert "V3" in recovered.text
    assert not context.blocked_skills
    context.close_commands()


def test_implicit_reload_cannot_inherit_caller_permission(tmp_path: Path) -> None:
    root, service, skill = _setup(tmp_path)
    turn = service.begin_turn()
    context = ToolRunContext.create("continue")
    args = {
        "skill_id": skill.id,
        "reason": "use",
        SKILL_CATALOG_SNAPSHOT_ARG: turn.catalog,
        SKILL_RUN_CONTEXT_ARG: context,
    }
    assert not _read(service, args).is_error
    _write(root, "V2", implicit=False)
    result = _apply(service, turn, context, skill.id, root)
    assert result.is_error
    assert skill.id in context.blocked_skills
    assert _read(service, args).is_error
    context.close_commands()


def test_unknown_skill_and_success_text_cannot_activate(tmp_path: Path) -> None:
    root, service, skill = _setup(tmp_path)
    turn = service.begin_turn()
    context = ToolRunContext.create("continue")
    fake = ToolResult("Updated sample to V2", meta={"_skill_update": {"skill_id": skill.id}})
    assert (
        apply_skill_update(
            fake, service=service, snapshot=turn, context=context, runtime_capabilities="test"
        )
        is fake
    )
    new_root = root.parent / "new-skill"
    new_root.mkdir()
    (new_root / "SKILL.md").write_text(
        "---\nname: new-skill\ndescription: new\n---\nnew", encoding="utf-8"
    )
    new = next(r for r in service.refresh().entries if r.name == "new-skill")
    activation, issue = service.activate_from_snapshot(turn.catalog, SkillSelection(new.id))
    assert activation is None and issue is not None
    assert not context.skill_activations


def test_receipt_is_operation_bound_and_cannot_be_replayed(tmp_path: Path) -> None:
    root, _service, skill = _setup(tmp_path)
    receipt = tmp_path / "receipt.json"
    pending = PendingSkillUpdate(skill.id, root, receipt, "nonce", package_digest(root))
    _write(root, "V2")
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "nonce": "nonce",
                "root": str(root.parent),
                "skill_name": "sample",
                "status": "committed",
                "old_sha256": pending.before_digest,
                "new_sha256": package_digest(root),
            }
        ),
        encoding="utf-8",
    )
    result = pending.consume()
    assert result is not None and result.version == package_digest(root)
    assert pending.consume() is None
    other = PendingSkillUpdate(skill.id, root, receipt, "different")
    with pytest.raises(ValueError, match="does not match"):
        other.consume()


def test_missing_receipt_after_disk_change_is_not_silent_success(tmp_path: Path) -> None:
    root, _service, skill = _setup(tmp_path)
    pending = PendingSkillUpdate(
        skill.id, root, tmp_path / "absent.json", "nonce", package_digest(root)
    )
    _write(root, "V2")
    with pytest.raises(ValueError, match="receipt is missing"):
        pending.consume()


def test_current_binding_reappears_after_compaction_and_old_binding_stays_history(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from agent.sessions.model_context import ModelContextProjection, SessionModelContext
    from agent.sessions.rollout import deserialize_model_context, serialize_model_context
    from agent.skills.lifecycle import render_current_bindings

    root, service, skill = _setup(tmp_path)
    turn = service.begin_turn((SkillSelection(skill.id),))
    context = ToolRunContext.create("continue")
    context.bind_skill(turn.activations[0])

    def projection() -> ModelContextProjection:
        return ModelContextProjection(
            SessionModelContext().plan(
                current_time=datetime(2026, 9, 13, tzinfo=UTC),
                timezone="UTC",
                surface="desktop_chat",
                surface_context="desktop",
                channel="text",
                channel_instruction=None,
                approval_mode=None,
                approval_version=None,
                runtime_capabilities="test",
                capability_catalog=None,
                skill_bindings=render_current_bindings(context, "test"),
            )
        )

    old_messages = projection().apply([])
    _write(root, "V2")
    _apply(service, turn, context, skill.id, root)
    current = projection()
    history = current.apply(old_messages)
    assert history[: len(old_messages)] == old_messages
    assert "V2" in history[-1].content
    assert current.apply(history) == history
    rebuilt = current.apply([])
    assert "V2" in rebuilt[-1].content and "V1" not in rebuilt[-1].content
    state = SessionModelContext(current.snapshot.next_snapshot)
    assert deserialize_model_context(serialize_model_context(state)).snapshot == state.snapshot
    context.close_commands()
    assert "No Skill is active" in projection().apply(history)[-1].content
