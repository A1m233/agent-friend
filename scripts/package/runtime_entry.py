"""Frozen production runtime entry point for the Windows desktop package."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import runpy
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from importlib.resources import files
from pathlib import Path
from typing import Any, ClassVar, Protocol

from runtime_config import (
    ALL_CREDENTIAL_NAMES,
    OPTIONAL_CREDENTIAL_NAMES,
    REQUIRED_CREDENTIAL_NAMES,
    CredentialSeedError,
    KeyringSource,
    decrypt_credential_seed,
    install_credential_seed,
)

try:
    from agent_friend_build_identity import BUILD_IDENTITY_JSON
except ImportError:
    BUILD_IDENTITY_JSON = ""


class _CredentialStore(Protocol):
    def get(self, name: str) -> Any: ...

    def status(self, name: str) -> Any: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _build_identity() -> dict[str, Any]:
    if not BUILD_IDENTITY_JSON:
        raise RuntimeError("frozen runtime build identity is unavailable")
    value = json.loads(BUILD_IDENTITY_JSON)
    if not isinstance(value, dict):
        raise RuntimeError("frozen runtime build identity is invalid")
    return value


def _print_build_identity() -> int:
    try:
        identity = _build_identity()
    except (RuntimeError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "identity": identity}, ensure_ascii=False))
    return 0


def _smoke_skill_catalog() -> dict[str, Any]:
    """Exercise packaged builtin resources and the native hot-reload watcher."""
    from agent.skills import SkillCatalogService, SkillSelection

    marker = "agent-friend frozen Skill watcher activation smoke"
    with tempfile.TemporaryDirectory(prefix="agent-friend-skill-smoke-") as temp_dir:
        root = Path(temp_dir)
        personal_root = root / "personal"
        service = SkillCatalogService(
            personal_root=personal_root,
            shared_root=root / "shared",
            watcher_enabled=True,
        )
        try:
            initial = service.initialize()
            builtin = next(
                (record for record in initial.entries if record.id == "builtin/skill-creator"),
                None,
            )
            if builtin is None:
                raise RuntimeError("packaged builtin skill-creator was not discovered")
            required_resources = (
                builtin.package_root / "SKILL.md",
                builtin.package_root / "license.txt",
                builtin.package_root / "scripts" / "quick_validate.py",
            )
            missing = [
                str(path.relative_to(builtin.package_root))
                for path in required_resources
                if not path.is_file()
            ]
            if missing:
                raise RuntimeError(
                    f"packaged builtin Skill resources are missing: {','.join(missing)}"
                )

            installer = next(
                (record for record in initial.entries if record.id == "builtin/skill-installer"),
                None,
            )
            if installer is None:
                raise RuntimeError("packaged builtin skill-installer was not discovered")
            installer_entry = installer.package_root / "scripts" / "install-skill-from-github.py"
            if not installer_entry.is_file():
                raise RuntimeError("packaged skill-installer entry is missing")
            installer_scripts = str(installer_entry.parent)
            sys.path.insert(0, installer_scripts)
            try:
                installer_namespace = runpy.run_path(
                    str(installer_entry),
                    run_name="agent_friend_packaged_skill_installer_probe",
                )
            finally:
                sys.path.remove(installer_scripts)
            validator = installer_namespace.get("_validate_relative_path")
            install_error = installer_namespace.get("InstallError")
            if not callable(validator) or not isinstance(install_error, type):
                raise RuntimeError("packaged skill-installer validation API is unavailable")
            try:
                validator("../outside")
            except Exception as exc:
                if not isinstance(exc, install_error) or "relative path inside the repo" not in str(
                    exc
                ):
                    raise RuntimeError(
                        "packaged skill-installer validation raised an unexpected error"
                    ) from exc
            else:
                raise RuntimeError("packaged skill-installer validation path did not fail closed")

            managed_module = sys.modules.get("managed_install")
            if managed_module is None:
                raise RuntimeError("packaged skill-installer managed helper is unavailable")
            managed_root = root / "managed-installs"
            managed_source = root / "managed-source"
            managed_source.mkdir()
            managed_entry = managed_source / "SKILL.md"
            managed_entry.write_text(
                "---\n"
                "name: frozen-managed-smoke\n"
                "description: Validate the packaged managed installer.\n"
                "---\n\n"
                "version one\n",
                encoding="utf-8",
            )
            stage_candidate = installer_namespace.get("stage_candidate")
            commit_candidate = installer_namespace.get("commit_candidate")
            make_record = installer_namespace.get("make_record")
            load_records = installer_namespace.get("load_records")
            package_digest = installer_namespace.get("package_digest")
            remove_candidate = installer_namespace.get("remove_candidate")
            if not all(
                callable(value)
                for value in (
                    stage_candidate,
                    commit_candidate,
                    make_record,
                    load_records,
                    package_digest,
                    remove_candidate,
                )
            ):
                raise RuntimeError("packaged managed installer API is incomplete")
            candidate, digest = stage_candidate(
                managed_source,
                managed_root,
                "frozen-managed-smoke",
            )
            record = make_record(
                repository="fixture/repo",
                path="skills/frozen-managed-smoke",
                ref="main",
                content_sha256=digest,
            )
            commit_candidate(
                root=managed_root,
                skill_name="frozen-managed-smoke",
                candidate=candidate,
                record=record,
                expected_mode="absent",
                expected_record=None,
                expected_current_sha256=None,
            )
            remove_candidate(candidate)
            managed_target = managed_root / "frozen-managed-smoke"
            if (
                load_records(managed_root).get("frozen-managed-smoke") != record
                or package_digest(managed_target) != digest
            ):
                raise RuntimeError("packaged managed installer did not commit its first install")

            managed_entry.write_text(
                "---\n"
                "name: frozen-managed-smoke\n"
                "description: Validate the packaged managed installer.\n"
                "---\n\n"
                "version two\n",
                encoding="utf-8",
            )
            replacement, replacement_digest = stage_candidate(
                managed_source,
                managed_root,
                "frozen-managed-smoke",
            )
            replacement_record = make_record(
                repository="fixture/repo",
                path="skills/frozen-managed-smoke",
                ref="main",
                content_sha256=replacement_digest,
            )
            original_writer = managed_module._write_records

            def fail_lock_write(_root: Path, _records: object) -> None:
                raise managed_module.ManagedInstallError(
                    "lock_write_failed", "injected frozen smoke failure"
                )

            managed_module._write_records = fail_lock_write
            try:
                try:
                    commit_candidate(
                        root=managed_root,
                        skill_name="frozen-managed-smoke",
                        candidate=replacement,
                        record=replacement_record,
                        expected_mode="managed",
                        expected_record=record,
                        expected_current_sha256=digest,
                    )
                except Exception as exc:
                    if getattr(exc, "code", None) != "lock_write_failed":
                        raise RuntimeError(
                            "packaged managed installer returned an unexpected rollback error"
                        ) from exc
                else:
                    raise RuntimeError("packaged managed installer failure injection did not fail")
            finally:
                managed_module._write_records = original_writer
                remove_candidate(replacement)
            if (
                load_records(managed_root).get("frozen-managed-smoke") != record
                or package_digest(managed_target) != digest
            ):
                raise RuntimeError(
                    "packaged managed installer did not restore the previous version"
                )

            service.start()
            package = personal_root / "frozen-watcher-smoke"
            package.mkdir(parents=True)
            (package / "SKILL.md").write_text(
                "---\n"
                "name: frozen-watcher-smoke\n"
                "description: Validate the packaged Skill watcher.\n"
                "---\n\n"
                f"{marker}\n",
                encoding="utf-8",
            )

            deadline = time.monotonic() + 5
            live = None
            while time.monotonic() < deadline:
                live = next(
                    (
                        record
                        for record in service.snapshot.entries
                        if record.name == "frozen-watcher-smoke"
                    ),
                    None,
                )
                if live is not None:
                    break
                time.sleep(0.05)
            if live is None:
                raise RuntimeError("packaged Skill watcher did not refresh the catalog")

            turn = service.begin_turn((SkillSelection(live.id, live.version),))
            if (
                len(turn.activations) != 1
                or turn.activation_issues
                or marker not in turn.activations[0].contents
            ):
                raise RuntimeError("hot-loaded Skill could not be version-pinned and activated")
            return {
                "builtin": builtin.name,
                "builtin_resources": len(required_resources),
                "installer": installer.name,
                "installer_validation": "fail-closed",
                "installer_managed_transaction": "rollback-ok",
                "catalog_generation": turn.catalog.generation,
                "watcher_status": service.watcher_state.status,
                "activated": turn.activations[0].record.name,
            }
        finally:
            service.stop()


def _smoke_tool_schema_validation() -> dict[str, Any]:
    """Exercise frozen jsonschema through the production registry boundary."""

    from agent.tools import ToolRegistry, ToolResult

    class _SchemaSmokeTool:
        name = "schema_smoke"
        description = "Frozen runtime schema validation smoke."
        parameters_schema: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        }

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            return ToolResult(text=json.dumps({"accepted": args["value"]}))

    registry = ToolRegistry([_SchemaSmokeTool()])
    accepted = registry.invoke("schema_smoke", {"value": 7})
    rejected = registry.invoke("schema_smoke", {"value": "wrong", "extra": True})
    rejected_payload = json.loads(rejected.text)
    if accepted.is_error or not rejected.is_error:
        raise RuntimeError("production ToolRegistry did not enforce its input schema")
    if rejected_payload.get("error") != "invalid_arguments":
        raise RuntimeError("schema rejection did not preserve the model-visible error code")
    return {
        "accepted": json.loads(accepted.text).get("accepted") == 7,
        "rejected_rule": rejected_payload.get("rule"),
    }


def _smoke_scheduled_tasks() -> dict[str, Any]:
    """Exercise the frozen Scheduled Task tool, SQLite store, and recovery boundary."""

    from datetime import UTC, datetime, timedelta

    from agent.scheduled_tasks import (
        ScheduledOccurrenceOutcome,
        ScheduledTaskService,
        ScheduledTaskStore,
    )
    from agent.tools.builtin.scheduled_task_manage import ScheduledTaskManageTool

    now = datetime(2026, 1, 1, tzinfo=UTC)
    with tempfile.TemporaryDirectory(prefix="agent-friend-scheduled-task-smoke-") as temp_dir:
        store = ScheduledTaskStore(Path(temp_dir) / "scheduled-tasks.db")
        service = ScheduledTaskService(store, clock=lambda: now)
        tool = ScheduledTaskManageTool(
            service,
            default_persona_id=None,
            default_persona_name="default",
            default_model_id="doctor/model",
        )
        created = tool.invoke(
            {
                "operation": "create",
                "name": "Frozen scheduled task smoke",
                "instruction": "Exercise deterministic frozen scheduling.",
                "schedule": {
                    "kind": "fixed_interval",
                    "interval": 1,
                    "unit": "hour",
                },
                "__agent_friend_runtime_now": now.isoformat(),
            }
        )
        if created.is_error:
            raise RuntimeError(f"frozen Scheduled Task create failed: {created.text}")
        task_id = str(json.loads(created.text)["task"]["id"])
        due_at = now + timedelta(hours=1, seconds=5)
        created_occurrences = store.create_due_occurrences(due_at)
        if len(created_occurrences) != 1:
            raise RuntimeError("frozen Scheduled Task did not create one due occurrence")
        claim = store.claim_pending(due_at)[0]
        store.mark_submitted(
            claim.occurrence.id,
            now=due_at,
            skill_source=None,
            skill_version=None,
        )
        recovered = store.recover_startup(due_at + timedelta(seconds=1))
        occurrence = store.get_occurrence(claim.occurrence.id)
        history = tool.invoke({"operation": "history", "task_id": task_id})
        if history.is_error or occurrence.outcome is not ScheduledOccurrenceOutcome.INTERRUPTED:
            raise RuntimeError("frozen Scheduled Task recovery did not fail closed")
        history_payload = json.loads(history.text)
        if history_payload["occurrences"][0]["outcome"] != "interrupted":
            raise RuntimeError("frozen Scheduled Task history lost the recovery outcome")
        return {
            "task_id": bool(task_id),
            "scheduled_occurrence": len(created_occurrences),
            "interrupted_runs": recovered["interrupted_runs"],
            "history_outcome": "interrupted",
        }


def _smoke_filesystem_tools() -> dict[str, Any]:
    """Exercise frozen filesystem imports plus deterministic read/write/copy paths."""
    from agent.filesystem import (
        ApprovalAction,
        FilesystemAuthorizationLedger,
        FilesystemService,
        ProtectedPathPolicy,
    )

    with tempfile.TemporaryDirectory(prefix="agent-friend-filesystem-smoke-") as temp_dir:
        root = Path(temp_dir).resolve()
        service = FilesystemService(ProtectedPathPolicy(product_roots=(), personal_skill_roots=()))
        ledger = FilesystemAuthorizationLedger()
        scope = ledger.grant_scope(
            root=root,
            root_kind="directory",
            permissions=frozenset({"search_metadata", "read_text", "create", "edit"}),
        )
        created = service.write_text(
            ledger,
            scope_id=scope.scope_id,
            relative_path="source.txt",
            content="alpha\nbeta\n",
        )
        read = service.read_text(
            ledger,
            scope_id=scope.scope_id,
            relative_path="source.txt",
        )
        patched = service.apply_patch(
            ledger,
            scope_id=scope.scope_id,
            relative_path="source.txt",
            expected_sha256=str(read["sha256"]),
            patch=(
                "*** Begin Patch\n*** Update File: source.txt\n@@\n alpha\n-beta\n+gamma\n"
                "*** End Patch\n"
            ),
        )
        binary = root / "source.bin"
        binary.write_bytes(bytes(range(256)))
        copied = service.copy(
            ledger,
            source_scope_id=scope.scope_id,
            source_path="source.bin",
            destination_scope_id=scope.scope_id,
            destination_path="copy.bin",
        )
        delete_action = ApprovalAction(
            action_id="doctor-delete",
            action="delete",
            target=(root / "copy.bin").resolve(),
        )
        ledger.grant_action(delete_action)
        service.delete(
            ledger,
            scope_id=scope.scope_id,
            relative_path="copy.bin",
            action_id=delete_action.action_id,
        )
        return {
            "created_sha256": created["sha256"],
            "patched_sha256": patched["sha256"],
            "binary_sha256": copied["sha256"],
            "deleted": not (root / "copy.bin").exists(),
        }


def _smoke_command_runtime() -> dict[str, Any]:
    """Exercise the packaged official exec-server without network or an LLM."""

    from agent.approvals import ApprovalBroker
    from agent.command_runtime import (
        CommandApprovalDecision,
        CommandRuntime,
        ExecCommandTool,
        TerminateCommandTool,
        verify_codex_executable,
    )
    from agent.command_runtime.runtime import sandbox_context
    from agent.command_runtime.tools import (
        COMMAND_RUN_CONTEXT_ARG,
        COMMAND_RUN_ID_ARG,
        COMMAND_SESSION_ID_ARG,
        COMMAND_SKILL_SNAPSHOT_ARG,
        COMMAND_SURFACE_ARG,
        COMMAND_TOOL_CALL_ID_ARG,
    )
    from agent.skills.models import (
        ActivatedSkill,
        RenderedSkillCatalog,
        SkillCatalogSnapshot,
        SkillRecord,
        SkillTurnSnapshot,
    )
    from agent.tool_runtime import ToolRunContext

    executable_value = os.environ.get("AGENT_FRIEND_CODEX_EXECUTABLE", "").strip()
    if not executable_value:
        return {"status": "not-injected", "runtime_exercised": False}
    codex_home_value = os.environ.get("AGENT_FRIEND_COMMAND_RUNTIME_CODEX_HOME", "").strip()
    if not codex_home_value:
        raise RuntimeError("packaged command runtime CODEX_HOME was not injected")
    execution_root_value = os.environ.get("AGENT_FRIEND_COMMAND_RUNTIME_EXECUTION_ROOT", "").strip()
    if not execution_root_value:
        raise RuntimeError("packaged command runtime execution root was not injected")
    executable = verify_codex_executable(Path(executable_value))
    with tempfile.TemporaryDirectory(prefix="agent-friend-command-smoke-") as temp_dir:
        root = Path(temp_dir).resolve()
        runtime = CommandRuntime(
            executable,
            codex_home=Path(codex_home_value),
            execution_root=Path(execution_root_value),
        )
        command_env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "PATH",
                "PATHEXT",
                "SYSTEMROOT",
                "SYSTEMDRIVE",
                "WINDIR",
                "COMSPEC",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "HOMEDRIVE",
                "HOMEPATH",
                "APPDATA",
                "LOCALAPPDATA",
            }
        }
        sandbox = sandbox_context(
            workdir=root,
            writable=True,
            additional_roots=(),
            network_enabled=False,
        )
        try:
            process = runtime.start_process(
                argv=(
                    os.environ["COMSPEC"],
                    "/D",
                    "/S",
                    "/C",
                    "if defined SystemDrive (echo frozen-command-ok) else exit /b 17",
                ),
                workdir=root,
                env=command_env,
                mode="buffered",
                session_id="doctor",
                run_id="doctor-buffered",
                tool_call_id="doctor-buffered",
                sandbox=sandbox,
            )
            finished = runtime.wait(
                process.process_id,
                session_id="doctor",
                run_id="doctor-buffered",
                timeout_seconds=30,
            )
            if not finished.terminal():
                runtime.terminate(
                    process.process_id,
                    session_id="doctor",
                    run_id="doctor-buffered",
                    reason="timeout",
                )
                raise RuntimeError("packaged buffered command did not finish")
            if finished.exit_code != 0 or b"frozen-command-ok" not in finished.stdout_tail:
                raise RuntimeError(
                    "packaged buffered command failed: "
                    + finished.stderr_tail.decode("utf-8", errors="replace")
                )

            host_user = runtime.start_process(
                argv=(
                    os.environ["COMSPEC"],
                    "/D",
                    "/S",
                    "/C",
                    "echo frozen-host-user-ok",
                ),
                workdir=root,
                env=dict(os.environ),
                mode="buffered",
                session_id="doctor",
                run_id="doctor-host-user",
                tool_call_id="doctor-host-user",
                sandbox=None,
            )
            host_user = runtime.wait(
                host_user.process_id,
                session_id="doctor",
                run_id="doctor-host-user",
                timeout_seconds=30,
            )
            if (
                host_user.exit_code != 0
                or host_user.sandbox_type != "none"
                or b"frozen-host-user-ok" not in host_user.stdout_tail
            ):
                raise RuntimeError(
                    "packaged host-user command failed: "
                    + json.dumps(host_user.to_dict(), ensure_ascii=False)
                )

            streaming = runtime.start_process(
                argv=(os.environ["COMSPEC"], "/D", "/Q"),
                workdir=root,
                env=command_env,
                mode="streaming",
                session_id="doctor",
                run_id="doctor-streaming",
                tool_call_id="doctor-streaming",
                sandbox=sandbox,
            )
            runtime.write(
                streaming.process_id,
                session_id="doctor",
                run_id="doctor-streaming",
                chunk=b"echo frozen-streaming-ok\r\nexit\r\n",
            )
            streaming_finished = runtime.wait(
                streaming.process_id,
                session_id="doctor",
                run_id="doctor-streaming",
                timeout_seconds=30,
            )
            if (
                not streaming_finished.terminal()
                or streaming_finished.exit_code != 0
                or b"frozen-streaming-ok" not in streaming_finished.pty_tail
            ):
                raise RuntimeError("packaged streaming command or stdin failed")

            denied_path = root / "denied.txt"
            denied = runtime.start_process(
                argv=(
                    os.environ["COMSPEC"],
                    "/D",
                    "/S",
                    "/C",
                    f"echo forbidden>{denied_path.name}",
                ),
                workdir=root,
                env=command_env,
                mode="buffered",
                session_id="doctor",
                run_id="doctor-read-only",
                tool_call_id="doctor-read-only",
                sandbox=sandbox_context(
                    workdir=root,
                    writable=False,
                    additional_roots=(),
                    network_enabled=False,
                ),
            )
            denied_finished = runtime.wait(
                denied.process_id,
                session_id="doctor",
                run_id="doctor-read-only",
                timeout_seconds=30,
            )
            if denied_finished.exit_code == 0 or denied_path.exists():
                raise RuntimeError("packaged read-only sandbox allowed a write")

            default_context = ToolRunContext.create("")
            default_broker = ApprovalBroker()
            default_tool = ExecCommandTool(
                runtime,
                default_broker,
                session_workspaces_root=root / "session-workspaces",
            )
            default_results: list[Any] = []
            default_thread = threading.Thread(
                target=lambda: default_results.append(
                    default_tool.invoke(
                        {
                            "cmd": "echo frozen-session-cwd-ok>session-cwd.txt",
                            "access": {"filesystem": "read_write"},
                            "reason": "verify the frozen session-managed command directory",
                            COMMAND_SESSION_ID_ARG: "doctor-default-cwd",
                            COMMAND_RUN_ID_ARG: "doctor-default-cwd",
                            COMMAND_TOOL_CALL_ID_ARG: "doctor-default-cwd",
                            COMMAND_SURFACE_ARG: "desktop_chat",
                            COMMAND_RUN_CONTEXT_ARG: default_context,
                        }
                    )
                ),
                daemon=True,
            )
            default_thread.start()
            try:
                deadline = time.monotonic() + 10
                default_pending = ()
                while time.monotonic() < deadline:
                    default_pending = default_broker.pending(kind="command")
                    if default_pending:
                        break
                    time.sleep(0.01)
                if not default_pending:
                    raise RuntimeError("packaged default-cwd command did not request approval")
                default_request = default_pending[0]
                if default_request.details.cwd_source != "session_managed":
                    raise RuntimeError("packaged command did not select the session-managed cwd")
                if not default_broker.decide(
                    default_request.request_id,
                    CommandApprovalDecision(allow=True),
                    session_id="doctor-default-cwd",
                    run_id="doctor-default-cwd",
                    expected_kind="command",
                ):
                    raise RuntimeError("packaged default-cwd command approval was rejected")
                default_thread.join(timeout=40)
                if default_thread.is_alive() or not default_results:
                    raise RuntimeError("packaged default-cwd command did not finish")
                default_payload = json.loads(default_results[0].text)
                default_marker = Path(default_request.details.workdir) / "session-cwd.txt"
                if (
                    default_payload.get("status") != "exited"
                    or default_payload.get("exit_code") != 0
                    or not default_marker.is_file()
                ):
                    raise RuntimeError(
                        "packaged session-managed cwd command failed: "
                        + json.dumps(default_payload, ensure_ascii=False)
                    )
            finally:
                default_context.close_commands()

            read_only_context = ToolRunContext.create("")
            read_only_broker = ApprovalBroker()
            read_only_tool = ExecCommandTool(
                runtime,
                read_only_broker,
                session_workspaces_root=root / "session-workspaces",
            )
            read_only_results: list[Any] = []
            read_only_thread = threading.Thread(
                target=lambda: read_only_results.append(
                    read_only_tool.invoke(
                        {
                            "cmd": "echo forbidden>session-cwd-denied.txt",
                            "reason": "verify a fresh session-managed cwd remains read-only",
                            COMMAND_SESSION_ID_ARG: "doctor-default-cwd-read-only",
                            COMMAND_RUN_ID_ARG: "doctor-default-cwd-read-only",
                            COMMAND_TOOL_CALL_ID_ARG: "doctor-default-cwd-read-only",
                            COMMAND_SURFACE_ARG: "desktop_chat",
                            COMMAND_RUN_CONTEXT_ARG: read_only_context,
                        }
                    )
                ),
                daemon=True,
            )
            read_only_thread.start()
            try:
                deadline = time.monotonic() + 10
                read_only_pending = ()
                while time.monotonic() < deadline:
                    read_only_pending = read_only_broker.pending(kind="command")
                    if read_only_pending:
                        break
                    time.sleep(0.01)
                if not read_only_pending:
                    raise RuntimeError(
                        "packaged read-only default-cwd command did not request approval"
                    )
                read_only_request = read_only_pending[0]
                if read_only_request.details.cwd_source != "session_managed":
                    raise RuntimeError(
                        "packaged read-only command did not select the session-managed cwd"
                    )
                if not read_only_broker.decide(
                    read_only_request.request_id,
                    CommandApprovalDecision(allow=True),
                    session_id="doctor-default-cwd-read-only",
                    run_id="doctor-default-cwd-read-only",
                    expected_kind="command",
                ):
                    raise RuntimeError(
                        "packaged read-only default-cwd command approval was rejected"
                    )
                read_only_thread.join(timeout=40)
                if read_only_thread.is_alive() or not read_only_results:
                    raise RuntimeError("packaged read-only default-cwd command did not finish")
                read_only_payload = json.loads(read_only_results[0].text)
                read_only_marker = (
                    Path(read_only_request.details.workdir) / "session-cwd-denied.txt"
                )
                if read_only_payload.get("exit_code") == 0 or read_only_marker.exists():
                    raise RuntimeError(
                        "packaged fresh session-managed read-only sandbox allowed a write: "
                        + json.dumps(read_only_payload, ensure_ascii=False)
                    )
            finally:
                read_only_context.close_commands()

            package = root / "skill-package"
            package.mkdir()
            skill_md = package / "SKILL.md"
            skill_md.write_text(
                "---\nname: frozen-smoke\ndescription: frozen smoke\n---\n",
                encoding="utf-8",
            )
            skill_script = package / "run.cmd"
            skill_script.write_text("@echo skill-original\n", encoding="utf-8")
            record = SkillRecord(
                id="skill_frozen_smoke",
                source="personal",
                name="frozen-smoke",
                description="frozen smoke",
                entry_path=skill_md,
                canonical_entry_path=skill_md,
                package_root=package,
                version="f" * 64,
                frontmatter={},
            )
            activation = ActivatedSkill(
                record=record,
                contents=skill_md.read_text(encoding="utf-8"),
                version=record.version,
            )
            catalog = SkillCatalogSnapshot(
                generation=1,
                fingerprint="c" * 64,
                entries=(record,),
            )
            turn_snapshot = SkillTurnSnapshot(
                catalog=catalog,
                rendered_catalog=RenderedSkillCatalog(
                    content="",
                    fingerprint=catalog.fingerprint,
                    budget_profile="frozen-smoke",
                ),
                activations=(activation,),
            )
            context = ToolRunContext.create("")
            broker = ApprovalBroker()
            tool = ExecCommandTool(runtime, broker)
            result_box: list[Any] = []
            invoke_args = {
                "cmd": "run.cmd",
                "skill": {"skill_id": record.id, "entry": "run.cmd", "workdir": "."},
                "reason": "verify the frozen governed Skill command path",
                COMMAND_SESSION_ID_ARG: "doctor",
                COMMAND_RUN_ID_ARG: "doctor-skill",
                COMMAND_TOOL_CALL_ID_ARG: "doctor-skill",
                COMMAND_SURFACE_ARG: "desktop_chat",
                COMMAND_RUN_CONTEXT_ARG: context,
                COMMAND_SKILL_SNAPSHOT_ARG: turn_snapshot,
            }
            skill_thread = threading.Thread(
                target=lambda: result_box.append(tool.invoke(invoke_args)),
                daemon=True,
            )
            skill_thread.start()
            try:
                deadline = time.monotonic() + 10
                pending = ()
                while time.monotonic() < deadline:
                    pending = broker.pending(kind="command")
                    if pending:
                        break
                    time.sleep(0.01)
                if not pending:
                    raise RuntimeError("packaged Skill command did not request approval")
                request = pending[0]
                if request.details.skill is None:
                    raise RuntimeError("packaged Skill command lost its structured binding")
                skill_script.write_text("@echo skill-replaced\n", encoding="utf-8")
                if not broker.decide(
                    request.request_id,
                    CommandApprovalDecision(allow=True),
                    session_id="doctor",
                    run_id="doctor-skill",
                    expected_kind="command",
                ):
                    raise RuntimeError("packaged Skill command approval was rejected")
                skill_thread.join(timeout=40)
                if skill_thread.is_alive() or not result_box:
                    raise RuntimeError("packaged Skill command did not finish")
                skill_payload = json.loads(result_box[0].text)
                skill_output = str(skill_payload.get("stdout", ""))
                if (
                    skill_payload.get("status") != "exited"
                    or skill_payload.get("exit_code") != 0
                    or "skill-original" not in skill_output
                    or "skill-replaced" in skill_output
                ):
                    raise RuntimeError(
                        "packaged Skill Tool path did not preserve snapshot identity: "
                        + json.dumps(skill_payload, ensure_ascii=False)
                    )
                skill_fingerprint = request.details.skill.fingerprint
            finally:
                context.close_commands()

            child_script = root / "child.cmd"
            child_marker = root / "escaped-child.txt"
            child_script.write_text(
                "@echo off\nping -n 6 127.0.0.1 >nul\necho escaped>escaped-child.txt\n",
                encoding="utf-8",
            )
            long_process = runtime.start_process(
                argv=(
                    os.environ["COMSPEC"],
                    "/D",
                    "/S",
                    "/C",
                    'start "" /B cmd.exe /D /S /C child.cmd & ping -n 30 127.0.0.1 >nul',
                ),
                workdir=root,
                env=command_env,
                mode="streaming",
                session_id="doctor",
                run_id="doctor-cancel",
                tool_call_id="doctor-cancel",
                sandbox=sandbox,
            )
            time.sleep(0.5)
            cancel_context = ToolRunContext.create("")
            cancel_context.register_command_handle(
                long_process.process_id,
                lambda reason: runtime.terminate(
                    long_process.process_id,
                    session_id="doctor",
                    run_id="doctor-cancel",
                    reason=reason,
                    wait_for_terminal_seconds=0,
                ),
            )
            termination = TerminateCommandTool(runtime).invoke(
                {
                    "process_id": long_process.process_id,
                    COMMAND_SESSION_ID_ARG: "doctor",
                    COMMAND_RUN_ID_ARG: "doctor-cancel",
                    COMMAND_TOOL_CALL_ID_ARG: "doctor-terminate",
                    COMMAND_RUN_CONTEXT_ARG: cancel_context,
                }
            )
            termination_payload = json.loads(termination.text)
            cancel_context.close_commands()
            if (
                termination.is_error
                or termination_payload.get("status") != "cancelled"
                or termination_payload.get("terminal_reason") != "agent_requested"
            ):
                raise RuntimeError("packaged Agent termination Tool failed: " + termination.text)
            time.sleep(6)
            if child_marker.exists():
                raise RuntimeError("packaged command cancellation left a child process running")
            return {
                "versioned_payload": executable.name,
                "sandbox_type": finished.sandbox_type,
                "buffered_output": "frozen-command-ok",
                "host_user": True,
                "streaming_input": True,
                "read_only_write_denied": True,
                "session_managed_cwd": True,
                "session_managed_read_only": True,
                "skill_snapshot_fingerprint": skill_fingerprint,
                "skill_tool_path": True,
                "cancelled": True,
                "agent_termination_tool": True,
                "child_tree_cleaned": True,
            }
        finally:
            runtime.close()


def _doctor() -> int:
    checks: dict[str, dict[str, Any]] = {}
    modules = (
        "agent",
        "agent_bridge",
        "llm_providers",
        "memory",
        "runtime_config",
        "voice_bridge",
        "keyring",
        "jsonschema",
        "onnxruntime",
        "qqbot_agent_sdk",
        "sqlite_vec",
        "tokenizers",
    )
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:
            checks[f"module:{name}"] = {"ok": False, "error": str(exc)}
        else:
            checks[f"module:{name}"] = {"ok": True}

    resource_loaders = {
        "persona": lambda: files("agent.personas").joinpath("default.md"),
        "prompt": lambda: files("agent.prompt_sections").joinpath("project_identity.md"),
        "memory_prompt": lambda: (
            Path(importlib.import_module("memory").__file__).resolve().parent
            / "extraction"
            / "prompts"
            / "extract.md"
        ),
    }
    for name, load_resource in resource_loaders.items():
        try:
            resource = load_resource()
            content = resource.read_text(encoding="utf-8")
        except Exception as exc:
            checks[f"resource:{name}"] = {"ok": False, "error": str(exc)}
        else:
            checks[f"resource:{name}"] = {"ok": bool(content.strip())}

    try:
        from agent.tools.builtin.web_fetch.html import extract_static_html

        page = extract_static_html(
            """
            <html><body>
              <main><p>agent-friend frozen HTML extraction smoke</p></main>
              <a href="/evidence">Evidence link</a>
            </body></html>
            """,
            requested_url="https://example.com/start",
            final_url="https://example.com/start",
        )
        html_ok = (
            "frozen HTML extraction smoke" in page.text
            and len(page.links) == 1
            and page.links[0].url == "https://example.com/evidence"
        )
    except Exception as exc:
        checks["capability:web_fetch_static_html"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    else:
        checks["capability:web_fetch_static_html"] = {
            "ok": html_ok,
            "content_chars": len(page.text),
            "link_count": len(page.links),
        }

    try:
        schema_details = _smoke_tool_schema_validation()
    except Exception as exc:
        checks["capability:tool_schema_validation"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    else:
        checks["capability:tool_schema_validation"] = {"ok": True, **schema_details}

    try:
        scheduled_task_details = _smoke_scheduled_tasks()
    except Exception as exc:
        checks["capability:scheduled_tasks"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    else:
        checks["capability:scheduled_tasks"] = {"ok": True, **scheduled_task_details}

    try:
        skill_details = _smoke_skill_catalog()
    except Exception as exc:
        checks["capability:skill_catalog_hot_reload"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    else:
        checks["capability:skill_catalog_hot_reload"] = {"ok": True, **skill_details}

    try:
        filesystem_details = _smoke_filesystem_tools()
    except Exception as exc:
        checks["capability:governed_filesystem"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    else:
        checks["capability:governed_filesystem"] = {
            "ok": bool(filesystem_details.get("deleted")),
            **filesystem_details,
        }

    try:
        command_details = _smoke_command_runtime()
    except Exception as exc:
        checks["capability:governed_command_runtime"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    else:
        checks["capability:governed_command_runtime"] = {"ok": True, **command_details}

    try:
        managed_environment_details = _smoke_managed_execution_environment()
    except Exception as exc:
        checks["capability:managed_execution_python"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    else:
        checks["capability:managed_execution_python"] = {
            "ok": True,
            **managed_environment_details,
        }

    model_dir = os.environ.get("AGENT_FRIEND_MEMORY_MODEL_DIR", "").strip()
    if model_dir:
        model_path = Path(model_dir)
        checks["memory_model"] = {
            "ok": model_path.is_dir() and any(model_path.iterdir()),
            "path": str(model_path),
        }
        try:
            from memory.search.vector import (
                OnnxEmbeddingProvider,
                SqliteVecIndex,
                VectorFilters,
                VectorRecord,
            )

            provider = OnnxEmbeddingProvider(
                model_path / "onnx" / "model_quantized.onnx",
                model_path / "tokenizer.json",
            )
            vector = provider.embed(["打包运行时记忆检索自检"])[0]
            with tempfile.TemporaryDirectory(prefix="agent-friend-memory-smoke-") as temp_dir:
                index = SqliteVecIndex(Path(temp_dir) / "index.db", dimension=len(vector))
                try:
                    index.upsert(
                        "doctor",
                        [
                            VectorRecord(
                                evidence_id="packaged-smoke",
                                vector=vector,
                                owner_user_id="doctor",
                                persona_id="default",
                                kind="semantic",
                            )
                        ],
                    )
                    hits = index.search(
                        vector,
                        filters=VectorFilters(generation_id="doctor", owner_user_id="doctor"),
                        limit=1,
                    )
                finally:
                    index.close()
            checks["memory_native_query"] = {
                "ok": bool(hits and hits[0].evidence_id == "packaged-smoke"),
                "dimension": len(vector),
            }
        except Exception as exc:
            checks["memory_native_query"] = {"ok": False, "error": str(exc)}
    else:
        checks["memory_model"] = {"ok": True, "status": "not-configured"}

    ok = all(item["ok"] for item in checks.values())
    print(
        json.dumps(
            {"ok": ok, "runtime_root": str(_runtime_root()), "checks": checks},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


def _smoke_managed_execution_environment() -> dict[str, Any]:
    from agent.execution_environment import EnvironmentStatus, PythonEnvironmentProvider

    configured_root = os.environ.get("AGENT_FRIEND_EXECUTION_ENVIRONMENT_ROOT", "").strip()
    if not configured_root:
        return {"status": "not-configured", "source_mode": True}
    with tempfile.TemporaryDirectory(prefix="agent-friend-managed-environment-doctor-") as value:
        temporary = Path(value)
        provider = PythonEnvironmentProvider.from_runtime(
            data_root=temporary / "data",
            environment_root=Path(configured_root),
        )
        health = provider.prepare_global()
        if health.status is not EnvironmentStatus.READY:
            raise RuntimeError(health.error_code or "managed environment global health failed")
        environment = provider.prepare_session("doctor", health)
        if environment.status is not EnvironmentStatus.READY:
            raise RuntimeError(
                environment.error_code or "managed environment session prepare failed"
            )
        script = temporary / "data" / "doctor-script.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "import csv,json,sqlite3,ssl,tarfile,zipfile\n"
            "print(json.dumps({'managed': True}, sort_keys=True))\n",
            encoding="utf-8",
        )
        resolved = provider.resolve_command(environment, ("python", str(script)))
        if resolved is None:
            raise RuntimeError("managed Python alias did not resolve")
        script_result = subprocess.run(
            resolved.argv,
            env=resolved.environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=15,
            text=True,
            encoding="utf-8",
        )
        if json.loads(script_result.stdout) != {"managed": True}:
            raise RuntimeError("managed Python standard library smoke returned unexpected output")

        wheel = temporary / "managed_fixture-1.0-py3-none-any.whl"
        _write_doctor_wheel(wheel)
        package = provider.resolve_command(
            environment,
            ("pip", "install", str(wheel)),
        )
        if package is None or package.requires_network:
            raise RuntimeError("managed local wheel command was not normalized offline")
        subprocess.run(
            package.argv,
            env=package.environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=30,
            text=True,
            encoding="utf-8",
        )
        import_result = subprocess.run(
            [
                str(environment.executable_aliases["python"]),
                "-I",
                "-B",
                "-c",
                "import managed_fixture",
            ],
            env=environment.environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=15,
            text=True,
            encoding="utf-8",
        )
        if import_result.returncode != 0:
            raise RuntimeError("managed local wheel import failed")
        return {
            "status": environment.status.value,
            "runtime_version": environment.identity.runtime_version,
            "identity": environment.identity.identity[:16],
            "environment_revision": environment.revision,
            "local_wheel": True,
        }


def _write_doctor_wheel(path: Path) -> None:
    distribution = "managed_fixture-1.0.dist-info"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("managed_fixture/__init__.py", "VALUE = 'doctor'\n")
        bundle.writestr(
            f"{distribution}/METADATA",
            "Metadata-Version: 2.1\nName: managed-fixture\nVersion: 1.0\n",
        )
        bundle.writestr(
            f"{distribution}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: agent-friend-doctor\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n",
        )
        bundle.writestr(
            f"{distribution}/RECORD",
            "managed_fixture/__init__.py,,\n"
            f"{distribution}/METADATA,,\n"
            f"{distribution}/WHEEL,,\n"
            f"{distribution}/RECORD,,\n",
        )


def _seed_credentials(
    payload_path: Path,
    key_path: Path,
    *,
    store: _CredentialStore | None = None,
) -> int:
    credential_store = store or KeyringSource()
    try:
        credentials = decrypt_credential_seed(payload_path.read_bytes(), key_path.read_bytes())
        count = install_credential_seed(credentials, credential_store)
    except (CredentialSeedError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    cleared = sum(value is None for value in credentials.values())
    print(json.dumps({"ok": True, "installed": count, "cleared": cleared}, ensure_ascii=False))
    return 0


def _credential_status(
    *,
    require_operational: bool,
    store: _CredentialStore | None = None,
) -> int:
    credential_store = store or KeyringSource()
    missing: list[str] = []
    unreadable: list[str] = []
    for name in ALL_CREDENTIAL_NAMES:
        try:
            status = credential_store.status(name)
        except Exception:
            unreadable.append(name)
            continue
        if status.error and not status.configured:
            unreadable.append(name)
        elif not status.configured:
            missing.append(name)
    missing_required = [name for name in missing if name in REQUIRED_CREDENTIAL_NAMES]
    missing_optional = [name for name in missing if name in OPTIONAL_CREDENTIAL_NAMES]
    ok = not unreadable and (not require_operational or not missing_required)
    print(
        json.dumps(
            {
                "ok": ok,
                "configured": len(ALL_CREDENTIAL_NAMES) - len(missing) - len(unreadable),
                "total": len(ALL_CREDENTIAL_NAMES),
                "missing_required": missing_required,
                "missing_optional": missing_optional,
                "unreadable": unreadable,
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 2


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent-friend-runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("bridge", "voice"):
        subparsers.add_parser(command)
    subparsers.add_parser("doctor")
    subparsers.add_parser("build-identity")
    seed_parser = subparsers.add_parser("seed-credentials")
    seed_parser.add_argument("--payload", type=Path, required=True)
    seed_parser.add_argument("--key-file", type=Path, required=True)
    status_parser = subparsers.add_parser("credential-status")
    status_parser.add_argument("--require-operational", action="store_true")
    args, remaining = parser.parse_known_args(argv)
    if args.command not in {"bridge", "voice"} and remaining:
        parser.error(f"unrecognized arguments: {' '.join(remaining)}")
    args.args = remaining
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command == "doctor":
        return _doctor()
    if args.command == "build-identity":
        return _print_build_identity()
    if args.command == "seed-credentials":
        return _seed_credentials(args.payload, args.key_file)
    if args.command == "credential-status":
        return _credential_status(require_operational=args.require_operational)
    if BUILD_IDENTITY_JSON:
        os.environ["AGENT_FRIEND_BUILD_IDENTITY_JSON"] = BUILD_IDENTITY_JSON
    if args.command == "bridge":
        from agent_bridge.__main__ import main as bridge_main

        return bridge_main(args.args)
    if args.command == "voice":
        from voice_bridge.__main__ import main as voice_main

        return voice_main(args.args)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
