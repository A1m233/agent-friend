from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

import pytest
from agent.command_runtime import CommandRuntime, CommandRuntimeError
from agent.command_runtime.windows_home import home_directories, prepare_home, repair_home_script


def test_prepare_home_creates_nested_directories_as_host(tmp_path: Path) -> None:
    root = tmp_path / "command-runtime"
    home = root / "codex-home"
    prepare_home(home_directories(home, root))
    assert home.is_dir()
    assert list(home.iterdir()) == []
    prepare_home(home_directories(home, root))


def test_repair_scope_cannot_include_arbitrary_ancestor(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="dedicated parent"):
        home_directories(tmp_path / "runtime/home", tmp_path)


def test_prepare_home_rejects_file_without_replacing_it(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        prepare_home((home,))
    assert home.read_text(encoding="utf-8") == "keep"


def _state(home: Path) -> None:
    for relative, value in (
        (
            ".sandbox/setup_marker.json",
            {
                "version": 5,
                "offline_username": "offline",
                "online_username": "online",
                "proxy_ports": [],
            },
        ),
        (
            ".sandbox-secrets/sandbox_users.json",
            {
                "version": 5,
                "offline": {"username": "offline", "password": "YWJj"},
                "online": {"username": "online", "password": "YWJj"},
            },
        ),
    ):
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")


def _runtime(tmp_path: Path) -> CommandRuntime:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    return CommandRuntime(
        executable,
        codex_home=tmp_path / "runtime/home",
        managed_home_root=tmp_path / "runtime",
        execution_root=tmp_path / "execution",
        diagnostics_path=tmp_path / "logs/health.json",
        base_env={"SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows")},
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox initialization")
def test_first_setup_prepares_host_directories_before_elevation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt = _runtime(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        "agent.command_runtime.runtime.probe_setup_helper", lambda _: {"status": "ready"}
    )
    monkeypatch.setattr(
        rt, "_repair_host_home_access", lambda **_: pytest.fail("no ACL repair needed")
    )

    def setup(*_args: object, **_kwargs: object) -> None:
        assert (tmp_path / "runtime").is_dir()
        assert rt._codex_home.is_dir()
        calls.append("setup")
        _state(rt._codex_home)

    monkeypatch.setattr(rt, "_perform_windows_sandbox_setup", setup)
    monkeypatch.setattr(rt, "_probe_windows_sandbox", lambda: calls.append("probe"))
    assert rt.setup_windows_sandbox()["windows_sandbox_ready"]
    assert calls == ["setup", "probe"]


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox initialization")
@pytest.mark.parametrize("repair_allowed", [True, False])
def test_existing_identity_repairs_host_access_without_reprovisioning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repair_allowed: bool,
) -> None:
    rt = _runtime(tmp_path)
    _state(rt._codex_home)
    calls: list[str] = []
    monkeypatch.setattr(
        "agent.command_runtime.runtime.probe_setup_helper", lambda _: {"status": "ready"}
    )
    monkeypatch.setattr(
        rt,
        "_perform_windows_sandbox_setup",
        lambda *a, **k: pytest.fail("identity must be preserved"),
    )

    def prepare(_directories: tuple[Path, ...]) -> None:
        calls.append("prepare")
        if "repair" not in calls:
            raise PermissionError(13, "access denied")

    def repair(**_kwargs: object) -> None:
        calls.append("repair")
        if not repair_allowed:
            raise CommandRuntimeError("windows_sandbox_home_access_denied", "repair cancelled")

    monkeypatch.setattr("agent.command_runtime.runtime.prepare_home", prepare)
    monkeypatch.setattr(rt, "_repair_host_home_access", repair)
    monkeypatch.setattr(rt, "_probe_windows_sandbox", lambda: calls.append("probe"))
    if repair_allowed:
        assert rt.setup_windows_sandbox()["windows_sandbox_ready"]
        assert calls == ["prepare", "repair", "prepare", "probe"]
        evidence = rt.diagnostics()["incidentEvidence"]["incidents"][-1]
        assert evidence["hostDirectoryRepair"] == "ok"
    else:
        with pytest.raises(CommandRuntimeError, match="repair cancelled"):
            rt.setup_windows_sandbox()
        assert calls == ["prepare", "repair"]
        assert rt.sandbox_capability()["state"] == "repair_required"


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox state")
def test_unreadable_marker_is_repair_required_not_status_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt = _runtime(tmp_path)
    original = Path.exists

    def exists(path: Path) -> bool:
        if path.name == "setup_marker.json":
            raise PermissionError(13, "access denied")
        return original(path)

    monkeypatch.setattr(Path, "exists", exists)
    capability = rt.sandbox_capability()
    assert capability["state"] == "repair_required"
    error = capability["last_error"]
    assert isinstance(error, dict)
    assert error["error"] == "windows_sandbox_state_unreadable"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows ACL contract")
def test_native_acl_repair_preserves_protected_permissions_and_ownership(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    home = root / "home"
    child = home / "private"
    child.mkdir(parents=True)
    user = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"
    script = (
        f"$private=Get-Acl -LiteralPath '{child}';"
        "$private.SetAccessRuleProtection($true,$true);"
        f"Set-Acl -LiteralPath '{child}' -AclObject $private;"
        f"$before=(Get-Acl -LiteralPath '{child}').Sddl;"
        f"$owner=(Get-Acl -LiteralPath '{home}').Owner;"
        + repair_home_script(home_directories(home, root), user)
        + f"if($before -ne (Get-Acl -LiteralPath '{child}').Sddl){{exit 11}};"
        f"if($owner -ne (Get-Acl -LiteralPath '{home}').Owner){{exit 12}}"
    )
    result = subprocess.run(
        [
            str(Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
        ],
        capture_output=True,
        env={key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"},
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    prepare_home(home_directories(home, root))


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junction contract")
def test_prepare_home_rejects_junction(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    result = subprocess.run(
        [
            str(Path(os.environ["SYSTEMROOT"]) / "System32/cmd.exe"),
            "/D",
            "/C",
            "mklink",
            "/J",
            str(link),
            str(target),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    try:
        with pytest.raises(ValueError, match="reparse point"):
            prepare_home((link / "home",))
        assert not (target / "home").exists()
    finally:
        link.rmdir()
