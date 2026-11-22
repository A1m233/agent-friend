from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from impact import STAGES, stage_plan
from platform_plan import plan_for_checkout, verification_plan


@pytest.mark.parametrize(
    ("path", "stages"),
    [
        ("frontend/src/pages/chat/components/ChatSenderBox.tsx", ["frontend"]),
        ("frontend/src/pages/chat/components/ChatSenderBox.integration.test.ts", ["frontend"]),
        ("frontend/src/styles/theme/light.css", ["frontend"]),
        ("frontend/pnpm-lock.yaml", ["frontend"]),
        ("scripts/frontend/test.ps1", ["frontend"]),
        ("frontend/src-tauri/src/window_placement.rs", ["rust"]),
        ("frontend/src-tauri/Cargo.lock", ["rust"]),
        ("scripts/rust-check/run.py", ["rust"]),
        ("agent/src/agent/context/summary_prompt.md", ["backend"]),
        ("agent/tests/test_session_store.py", ["backend"]),
        ("uv.lock", ["backend"]),
        ("pyproject.toml", ["backend"]),
        ("frontend/src/services/api/runs.ts", ["backend", "frontend"]),
        ("agent_bridge/src/agent_bridge/protocols/ag_ui/encoders.py", ["backend", "frontend"]),
        ("frontend/src/lib/settings/index.ts", ["frontend", "rust"]),
        ("frontend/src-tauri/src/lib.rs", ["frontend", "rust"]),
        ("runtime_config/src/runtime_config/model.py", list(STAGES)),
        ("scripts/check/run.py", list(STAGES)),
        ("scripts/package/run.py", list(STAGES)),
        (".github/workflows/check.yml", list(STAGES)),
        ("new_module/service.py", list(STAGES)),
        ("docs/issues/001-fixture/replay.py", list(STAGES)),
        (".cursor/skills/example/scripts/run.py", list(STAGES)),
        ("docs/requirements/001-fixture/requirement.md", []),
        (".cursor/rules/dev-workflow.mdc", []),
        (".cursor/skills/fix-issue/SKILL.md", []),
        ("AGENTS.md", []),
        ("scripts/README.md", []),
    ],
)
def test_path_selection(path: str, stages: list[str]) -> None:
    plan = stage_plan({path})
    assert plan["localStages"] == stages
    assert plan["pathImpacts"][0]["reason"]


def test_selection_unions_layers_and_preserves_installer_requirements() -> None:
    assert stage_plan(set())["localStages"] == []
    assert stage_plan({"agent/tests/test_x.py", "frontend/src/App.tsx"})["localStages"] == [
        "backend",
        "frontend",
    ]
    plan = verification_plan(
        {"frontend/src-tauri/src/runtime_services/supervisor.rs"}, ["windows"], "macos"
    )
    assert plan["localStages"] == ["rust"]
    assert plan["windowsPackageRequired"] is True
    assert plan["handoffTo"] == ["windows"]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            "git",
            "-c",
            "user.name=Check Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "core.hooksPath=disabled-hooks",
            *args,
        ],
        cwd=root,
        text=True,
        stderr=subprocess.PIPE,
    ).strip()


def write(root: Path, path: str, content: str = "fixture\n") -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "main")
    write(tmp_path, "frontend/src/button.tsx")
    write(tmp_path, "agent/src/core.py")
    write(tmp_path, "README.md")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "baseline")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(tmp_path, "switch", "-c", "fix/fixture")
    return tmp_path


def test_git_plan_covers_commits_and_every_worktree_layer(repository: Path) -> None:
    write(repository, "frontend/src/已提交.tsx")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "frontend change")
    write(repository, "frontend/src/staged.tsx")
    git(repository, "add", ".")
    write(repository, "frontend/src/button.tsx", "unstaged\n")
    write(repository, "frontend/src/untracked with spaces.tsx")
    plan = plan_for_checkout(repository, "origin/main", [])
    assert plan["localStages"] == ["frontend"]
    assert set(plan["changedPaths"]) == {
        "frontend/src/已提交.tsx",
        "frontend/src/staged.tsx",
        "frontend/src/button.tsx",
        "frontend/src/untracked with spaces.tsx",
    }
    assert plan["baseCommit"] == git(repository, "rev-parse", "origin/main")
    assert plan["headCommit"] == git(repository, "rev-parse", "HEAD")


def test_rename_out_of_backend_keeps_the_removed_path(repository: Path) -> None:
    git(repository, "mv", "agent/src/core.py", "frontend/src/core.py")
    plan = plan_for_checkout(repository, "origin/main", [])
    assert plan["localStages"] == ["backend", "frontend"]
    assert set(plan["changedPaths"]) == {"agent/src/core.py", "frontend/src/core.py"}


def test_unstaged_deletion_and_staged_change_cannot_cancel_each_other(repository: Path) -> None:
    write(repository, "agent/src/core.py", "staged backend change\n")
    git(repository, "add", ".")
    write(repository, "agent/src/core.py")
    (repository / "frontend/src/button.tsx").unlink()
    assert plan_for_checkout(repository, "origin/main", [])["localStages"] == [
        "backend",
        "frontend",
    ]


def test_missing_or_advanced_baseline_does_not_report_no_checks(repository: Path) -> None:
    with pytest.raises(ValueError, match="Cannot resolve"):
        plan_for_checkout(repository, "missing", [])
    git(repository, "switch", "main")
    write(repository, "README.md", "advanced main\n")
    git(repository, "commit", "-am", "advance")
    git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repository, "switch", "fix/fixture")
    with pytest.raises(ValueError, match="does not contain baseline"):
        plan_for_checkout(repository, "origin/main", [])
    git(repository, "merge", "--ff-only", "origin/main")
    assert plan_for_checkout(repository, "origin/main", [])["localStages"] == []


@pytest.fixture
def runner(repository: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    spec = importlib.util.spec_from_file_location(
        "affected_check_runner", Path(__file__).with_name("run.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", repository)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    return module


def test_cli_plan_is_readonly_and_frontend_change_runs_only_frontend(
    repository: Path,
    runner: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write(repository, "frontend/src/button.tsx", "changed\n")
    calls: list[str] = []
    monkeypatch.setattr(runner, "_run_frontend", lambda: calls.append("frontend"))
    monkeypatch.setattr(runner, "_run_backend", lambda args: calls.append("backend"))
    monkeypatch.setattr(runner, "_run_rust", lambda: calls.append("rust"))
    assert runner.main(["--plan"]) == 0
    assert json.loads(capsys.readouterr().out)["localStages"] == ["frontend"]
    assert calls == []
    monkeypatch.setenv("CODEX_THREAD_ID", "fixture")
    assert runner.main([]) == 0
    assert calls == ["frontend"]


def test_codex_multi_stage_prints_only_required_commands_before_running(
    repository: Path,
    runner: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write(repository, "frontend/src/button.tsx", "changed\n")
    write(repository, "agent/src/core.py", "changed\n")
    monkeypatch.setenv("CODEX_THREAD_ID", "fixture")
    monkeypatch.setattr(runner, "_run_backend", lambda args: pytest.fail("must not start checks"))
    assert runner.main([]) == 2
    error = capsys.readouterr().err
    assert "--stage backend" in error and "--stage frontend" in error
    assert "--stage rust" not in error


def test_cli_document_only_does_not_claim_tests_passed(
    repository: Path,
    runner: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write(repository, "README.md", "docs\n")
    assert runner.main([]) == 0
    output = capsys.readouterr().out
    assert "no tests executed" in output
    assert "stages passed" not in output


def test_cli_full_check_is_available_without_baseline_and_stops_at_failure(
    repository: Path,
    runner: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git(repository, "update-ref", "-d", "refs/remotes/origin/main")
    calls: list[str] = []

    def fail_backend(args: list[str]) -> None:
        calls.append("backend")
        assert args == ["-q"]
        raise SystemExit(7)

    monkeypatch.setattr(runner, "_run_backend", fail_backend)
    monkeypatch.setattr(runner, "_run_frontend", lambda: calls.append("frontend"))
    with pytest.raises(SystemExit) as error:
        runner.main(["--stage", "all", "-q"])
    assert error.value.code == 7
    assert calls == ["backend"]


def test_cli_cannot_silently_drop_pytest_options_on_frontend_only(
    repository: Path,
    runner: Any,
) -> None:
    write(repository, "frontend/src/button.tsx", "changed\n")
    with pytest.raises(SystemExit) as error:
        runner.main(["-q"])
    assert error.value.code == 2


def test_unknown_change_runs_all_stages_in_order_in_ordinary_terminal(
    repository: Path,
    runner: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(repository, "new-module/entry.py")
    calls: list[str] = []
    monkeypatch.setattr(runner, "_run_backend", lambda args: calls.append("backend"))
    monkeypatch.setattr(runner, "_run_frontend", lambda: calls.append("frontend"))
    monkeypatch.setattr(runner, "_run_rust", lambda: calls.append("rust"))
    assert runner.main([]) == 0
    assert calls == list(STAGES)


def test_invalid_baseline_exits_before_checks_or_success_message(
    runner: Any,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_run_backend", lambda args: pytest.fail("must not run"))
    with pytest.raises(SystemExit) as error:
        runner.main(["--base", "missing"])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "Cannot resolve" in output.err
    assert "stages passed" not in output.out and "no tests executed" not in output.out


def test_explicit_backend_stage_preserves_pytest_arguments_without_a_baseline(
    repository: Path,
    runner: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git(repository, "update-ref", "-d", "refs/remotes/origin/main")
    calls: list[list[str]] = []
    monkeypatch.setenv("CODEX_THREAD_ID", "fixture")
    monkeypatch.setattr(runner, "_run_backend", lambda args: calls.append(args))
    assert runner.main(["--stage", "backend", "scripts/check", "-q"]) == 0
    assert calls == [["scripts/check", "-q"]]
