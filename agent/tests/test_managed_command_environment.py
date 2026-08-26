from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from agent.approvals import ApprovalBroker
from agent.command_runtime import (
    CommandApprovalDecision,
    CommandRuntime,
    ExecCommandTool,
    HostProcessStartTool,
    PendingCommandApproval,
)
from agent.command_runtime.tools import (
    COMMAND_RUN_CONTEXT_ARG,
    COMMAND_RUN_ID_ARG,
    COMMAND_SESSION_ID_ARG,
    COMMAND_SKILL_SNAPSHOT_ARG,
    COMMAND_SURFACE_ARG,
    COMMAND_TOOL_CALL_ID_ARG,
)
from agent.execution_environment import (
    EnvironmentStatus,
    ExecutionEnvironmentManager,
    PreparedProviderEnvironment,
    ProviderHealth,
    ProviderIdentity,
    ResolvedManagedCommand,
)
from agent.skills.models import (
    ActivatedSkill,
    RenderedSkillCatalog,
    SkillCatalogSnapshot,
    SkillRecord,
    SkillTurnSnapshot,
)
from agent.tool_runtime import ToolRunContext

FIXTURE = Path(__file__).parent / "fixtures" / "fake_exec_server.py"


class _ManagedProvider:
    name = "test-python"
    identity = ProviderIdentity(
        provider="python",
        runtime_version="3.12.test",
        identity="b" * 64,
        platform=sys.platform,
        architecture="test",
    )

    def __init__(self, root: Path) -> None:
        self.base_root = root / "base"
        self.session_root = root / "session"
        self.cache_root = root / "cache"
        for path in (self.base_root, self.session_root, self.cache_root):
            path.mkdir(parents=True)

    def prepare_global(self) -> ProviderHealth:
        return ProviderHealth(self.identity, EnvironmentStatus.READY)

    def prepare_session(
        self, session_key: str, health: ProviderHealth
    ) -> PreparedProviderEnvironment:
        return PreparedProviderEnvironment(
            identity=self.identity,
            status=health.status,
            session_key=session_key,
            revision=2,
            executable_aliases={"python": Path(sys.executable)},
            environment={
                "PYTHONNOUSERSITE": "1",
                "PYTHONSAFEPATH": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PATH": str(Path(sys.executable).parent),
            },
            read_only_roots=(self.base_root,),
            writable_roots=(self.session_root,),
            shared_cache_root=self.cache_root,
        )

    def matches_command(self, argv: tuple[str, ...]) -> bool:
        return bool(argv) and Path(argv[0]).name.casefold() in {"python", "pip"}

    def resolve_command(
        self,
        environment: PreparedProviderEnvironment,
        argv: tuple[str, ...],
    ) -> ResolvedManagedCommand | None:
        if not self.matches_command(argv):
            return None
        package = Path(argv[0]).name.casefold() == "pip"
        return ResolvedManagedCommand(
            provider=environment,
            argv=(str(Path(sys.executable).resolve()), *argv[1:]),
            display_argv=argv,
            environment=dict(environment.environment),
            read_only_roots=(self.base_root, self.session_root)
            if not package
            else (self.base_root,),
            writable_roots=(self.session_root, self.cache_root) if package else (),
            package_operation=package,
            requires_network=package,
            requires_write=package,
        )

    def record_command_success(
        self, command: ResolvedManagedCommand
    ) -> PreparedProviderEnvironment:
        return dataclasses.replace(command.provider, revision=command.provider.revision + 1)

    def release_session(self, session_key: str) -> None:
        return None

    def collect_health(self) -> ProviderHealth:
        return ProviderHealth(self.identity, EnvironmentStatus.READY)

    def collect_diagnostics(self, *, active_session_keys: frozenset[str]) -> dict[str, object]:
        return {"provider": "python", "status": "ready"}

    def classify_failure(
        self, command: ResolvedManagedCommand, output: str
    ) -> tuple[str, str] | None:
        if command.package_operation:
            return "managed_package_failed", "fixture package failure"
        return None

    def gc(self, *, active_session_keys: frozenset[str]) -> dict[str, int]:
        return {"active": len(active_session_keys)}


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[CommandRuntime]:
    value = CommandRuntime(
        None,
        codex_home=tmp_path / "codex-home",
        execution_root=tmp_path / "execution",
        exec_server_command=[sys.executable, str(FIXTURE)],
    )
    yield value
    value.close()


@pytest.fixture
def environment_manager(tmp_path: Path) -> Iterator[ExecutionEnvironmentManager]:
    manager = ExecutionEnvironmentManager((_ManagedProvider(tmp_path / "managed"),))
    manager.start()
    manager.prepare_before_turn("session-a")
    yield manager
    manager.close()


def _args(argv: list[str], *, access: dict[str, str] | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "argv": argv,
        "reason": "verify managed environment",
        COMMAND_SESSION_ID_ARG: "session-a",
        COMMAND_RUN_ID_ARG: "run-a",
        COMMAND_TOOL_CALL_ID_ARG: "call-a",
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
    }
    if access is not None:
        value["access"] = access
    return value


def _wait_for_request(broker: ApprovalBroker) -> PendingCommandApproval:
    for _ in range(100):
        requests = broker.pending(kind="command")
        if requests:
            return cast(PendingCommandApproval, requests[0])
        threading.Event().wait(0.01)
    raise AssertionError("approval request was not published")


def _skill_turn(package: Path) -> SkillTurnSnapshot:
    entry = package / "SKILL.md"
    entry.write_text("---\nname: test\ndescription: test\n---\n", encoding="utf-8")
    record = SkillRecord(
        id="skill_test",
        source="personal",
        name="test",
        description="test",
        entry_path=entry,
        canonical_entry_path=entry,
        package_root=package,
        version="a" * 64,
        frontmatter={},
    )
    activation = ActivatedSkill(
        record=record,
        contents=entry.read_text(encoding="utf-8"),
        version=record.version,
    )
    catalog = SkillCatalogSnapshot(
        generation=1,
        fingerprint="b" * 64,
        entries=(record,),
    )
    return SkillTurnSnapshot(
        catalog=catalog,
        rendered_catalog=RenderedSkillCatalog(
            content="",
            fingerprint=catalog.fingerprint,
            budget_profile="test",
        ),
        activations=(activation,),
    )


def test_parse_uses_managed_alias_without_host_path_and_hides_internal_roots(
    runtime: CommandRuntime,
    environment_manager: ExecutionEnvironmentManager,
    tmp_path: Path,
) -> None:
    tool = ExecCommandTool(
        runtime,
        ApprovalBroker(),
        base_env={"PATH": str(tmp_path / "host-path-without-python")},
        execution_environment_manager=environment_manager,
        session_workspaces_root=tmp_path / "workspaces",
    )
    args = _args(["python", "-c", "fake:buffered"])
    context = args[COMMAND_RUN_CONTEXT_ARG]
    assert isinstance(context, ToolRunContext)
    details, _workdir, roots, _temp, _identities = tool._parse(
        args,
        context,
        session_id="session-a",
        run_id="run-a",
    )
    assert details.argv == ("python", "-c", "fake:buffered")
    assert details.launch_argv == (str(Path(sys.executable).resolve()), "-c", "fake:buffered")
    assert details.execution_environment is not None
    assert details.execution_environment["provider"] == "python"
    assert details.execution_environment["environment_revision"] == 2
    assert details.environment_overrides["PYTHONNOUSERSITE"] == "1"
    assert not details.additional_roots
    assert any(path.name == "base" and not writable for path, writable in roots)
    assert any(path.name == "session" and not writable for path, writable in roots)
    context.close_commands()


def test_skill_bound_managed_python_exposes_only_snapshot_entry_directory(
    runtime: CommandRuntime,
    environment_manager: ExecutionEnvironmentManager,
    tmp_path: Path,
) -> None:
    package = tmp_path / "skill"
    scripts = package / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "run.py").write_text("from helper import value\n", encoding="utf-8")
    (scripts / "helper.py").write_text("value = 1\n", encoding="utf-8")
    context = ToolRunContext.create("")
    args: dict[str, object] = {
        "cmd": "python scripts/run.py",
        "skill": {"entry": "scripts/run.py"},
        "reason": "verify Skill sibling import path",
        COMMAND_SESSION_ID_ARG: "session-a",
        COMMAND_RUN_ID_ARG: "run-a",
        COMMAND_TOOL_CALL_ID_ARG: "call-a",
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_RUN_CONTEXT_ARG: context,
        COMMAND_SKILL_SNAPSHOT_ARG: _skill_turn(package),
    }
    tool = ExecCommandTool(
        runtime,
        ApprovalBroker(),
        execution_environment_manager=environment_manager,
        session_workspaces_root=tmp_path / "workspaces",
    )

    details, _workdir, _roots, run_temp, _identities = tool._parse(
        args,
        context,
        session_id="session-a",
        run_id="run-a",
    )

    assert details.skill is not None
    assert details.launch_argv is not None
    assert details.launch_workdir is not None
    expected_entry = Path(details.launch_workdir) / details.launch_argv[1]
    assert details.environment_overrides["PYTHONPATH"] == str(expected_entry.parent)
    assert details.environment_overrides["PYTHONNOUSERSITE"] == "1"
    completed = subprocess.run(
        details.launch_argv,
        cwd=details.launch_workdir,
        env=tool._child_env(
            run_temp,
            overrides=details.environment_overrides,
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    context.close_commands()


@pytest.mark.parametrize(
    ("access", "expected_error"),
    [
        (
            {"filesystem": "read_only", "network": "enabled"},
            "managed_package_requires_write_access",
        ),
        (
            {"filesystem": "read_write", "network": "disabled"},
            "managed_package_requires_network_access",
        ),
    ],
)
def test_package_operation_requires_declared_write_and_network(
    runtime: CommandRuntime,
    environment_manager: ExecutionEnvironmentManager,
    access: dict[str, str],
    expected_error: str,
) -> None:
    tool = ExecCommandTool(
        runtime,
        ApprovalBroker(),
        execution_environment_manager=environment_manager,
    )
    result = tool.invoke(_args(["pip", "install", "demo"], access=access))
    assert json.loads(result.text)["error"] == expected_error


def test_managed_alias_rejects_host_user_but_absolute_host_python_remains_host_capability(
    runtime: CommandRuntime,
    environment_manager: ExecutionEnvironmentManager,
) -> None:
    tool = HostProcessStartTool(
        runtime,
        ApprovalBroker(),
        execution_environment_manager=environment_manager,
    )
    managed = _args(["python", "-V"])
    result = tool.invoke(managed)
    assert json.loads(result.text)["error"] == "managed_environment_requires_sandbox"

    absolute = _args([str(Path(sys.executable).resolve()), "-V"])
    context = absolute[COMMAND_RUN_CONTEXT_ARG]
    assert isinstance(context, ToolRunContext)
    details, *_rest = tool._parse(
        absolute,
        context,
        execution="host_user",
        session_id="session-a",
        run_id="run-a",
    )
    assert details.execution_environment is None
    assert details.argv[0] == str(Path(sys.executable).resolve())
    context.close_commands()


def test_compound_managed_package_shell_is_rejected_before_approval(
    runtime: CommandRuntime,
    environment_manager: ExecutionEnvironmentManager,
) -> None:
    broker = ApprovalBroker()
    tool = ExecCommandTool(
        runtime,
        broker,
        execution_environment_manager=environment_manager,
    )
    args = _args(["python", "-V"])
    args.pop("argv")
    args["cmd"] = "pip install demo && echo done"
    result = tool.invoke(args)
    assert json.loads(result.text)["error"] == "managed_package_command_requires_direct_invocation"
    assert broker.pending(kind="command") == ()


def test_approval_shows_semantic_command_and_result_reports_managed_identity(
    runtime: CommandRuntime,
    environment_manager: ExecutionEnvironmentManager,
    tmp_path: Path,
) -> None:
    broker = ApprovalBroker()
    tool = ExecCommandTool(
        runtime,
        broker,
        execution_environment_manager=environment_manager,
        session_workspaces_root=tmp_path / "workspaces",
    )
    result_box = []
    thread = threading.Thread(
        target=lambda: result_box.append(tool.invoke(_args(["python", "-c", "fake:buffered"])))
    )
    thread.start()
    request = _wait_for_request(broker)
    assert request.details.argv == ("python", "-c", "fake:buffered")
    assert request.details.launch_argv is not None
    assert request.details.launch_argv[0] == str(Path(sys.executable).resolve())
    serialized = request.to_dict()
    serialized_details = cast(dict[str, object], serialized["details"])
    assert str(Path(sys.executable).resolve()) not in str(serialized_details["cmd"])
    assert broker.decide(request.request_id, CommandApprovalDecision(allow=True))
    thread.join(timeout=3)
    assert not thread.is_alive()
    payload = json.loads(result_box[0].text)
    assert payload["status"] == "exited"
    assert payload["execution_environment"]["provider"] == "python"
    assert payload["execution_environment"]["runtime_version"] == "3.12.test"


def test_successful_package_operation_commits_new_environment_revision(
    runtime: CommandRuntime,
    environment_manager: ExecutionEnvironmentManager,
) -> None:
    broker = ApprovalBroker()
    tool = ExecCommandTool(
        runtime,
        broker,
        execution_environment_manager=environment_manager,
    )
    result_box = []
    args = _args(
        ["pip", "install", "fake:buffered"],
        access={"filesystem": "read_write", "network": "enabled"},
    )
    thread = threading.Thread(target=lambda: result_box.append(tool.invoke(args)))
    thread.start()
    request = _wait_for_request(broker)
    assert request.details.execution_environment is not None
    assert request.details.execution_environment["environment_revision"] == 2
    assert broker.decide(request.request_id, CommandApprovalDecision(allow=True))
    thread.join(timeout=3)
    assert not thread.is_alive()
    payload = json.loads(result_box[0].text)
    assert payload["execution_environment"]["environment_revision"] == 3
