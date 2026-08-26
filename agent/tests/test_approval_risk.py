from __future__ import annotations

from pathlib import Path, PurePosixPath

from agent.approvals import CatastrophicActionPolicy
from agent.command_runtime.models import (
    CommandApprovalDetails,
    CommandSkillBinding,
    PendingCommandApproval,
)
from agent.filesystem.models import ApprovalAction, PendingFilesystemApproval


def _command(
    argv: tuple[str, ...],
    *,
    workdir: str,
    execution: str = "host_user",
    skill: CommandSkillBinding | None = None,
) -> PendingCommandApproval:
    return PendingCommandApproval.create(
        session_id="sid-1",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="test",
        details=CommandApprovalDetails(
            cmd=" ".join(argv),
            argv=argv,
            execution=execution,  # type: ignore[arg-type]
            workdir=workdir,
            cwd_source="explicit",
            mode="buffered",
            timeout_seconds=30,
            filesystem_access="unrestricted",
            additional_roots=(),
            host_derived_roots=(),
            network_access="unrestricted",
            interactive=False,
            skill=skill,
        ),
    )


def _installer_binding(*, fingerprint: str = "a" * 64) -> CommandSkillBinding:
    return CommandSkillBinding(
        skill_id="builtin/skill-installer",
        entry="scripts/install-skill-from-github.py",
        workdir=".",
        fingerprint=fingerprint,
    )


def test_posix_forced_recursive_root_delete_is_catastrophic() -> None:
    policy = CatastrophicActionPolicy(
        path_flavor="posix",
        home=PurePosixPath("/home/test"),  # type: ignore[arg-type]
    )

    assessment = policy.assess(
        _command(("/bin/sh", "-lc", "rm -rf / && echo never"), workdir="/work")
    )

    assert assessment.code == "critical-root-recursive-delete"

    wrapped = policy.assess(_command(("sudo", "-u", "root", "rm", "-rf", "/"), workdir="/work"))
    assert wrapped.code == "critical-root-recursive-delete"


def test_posix_forced_recursive_unresolved_delete_is_catastrophic() -> None:
    policy = CatastrophicActionPolicy(
        path_flavor="posix",
        home=PurePosixPath("/home/test"),  # type: ignore[arg-type]
    )

    assessment = policy.assess(_command(("/bin/sh", "-lc", "rm -rf $TARGET"), workdir="/work"))

    assert assessment.code == "unresolved-recursive-delete-target"


def test_narrow_recursive_delete_and_unknown_script_remain_normal() -> None:
    policy = CatastrophicActionPolicy(
        path_flavor="posix",
        home=PurePosixPath("/home/test"),  # type: ignore[arg-type]
    )

    narrow = policy.assess(_command(("/bin/sh", "-lc", "rm -rf /work/tmp/build"), workdir="/work"))
    unknown_script = policy.assess(_command(("python", "cleanup.py"), workdir="/work"))

    assert narrow.level == "normal"
    assert unknown_script.level == "normal"


def test_deleting_an_ancestor_of_home_is_catastrophic() -> None:
    policy = CatastrophicActionPolicy(
        path_flavor="posix",
        home=PurePosixPath("/home/test"),  # type: ignore[arg-type]
    )

    assessment = policy.assess(_command(("rm", "-rf", "/home"), workdir="/work"))

    assert assessment.code == "critical-root-recursive-delete"


def test_disk_format_commands_are_catastrophic() -> None:
    posix = CatastrophicActionPolicy(
        path_flavor="posix",
        home=PurePosixPath("/home/test"),  # type: ignore[arg-type]
    )
    windows = CatastrophicActionPolicy(
        path_flavor="windows",
        home=Path("C:/Users/test"),
    )

    assert posix.assess(_command(("mkfs.ext4", "/dev/sda"), workdir="/work")).code == "disk-wipe"
    assert (
        windows.assess(
            _command(("pwsh.exe", "-Command", "Clear-Disk -Number 0"), workdir="C:/work")
        ).code
        == "disk-wipe"
    )
    assert windows.assess(_command(("format.com", "C:"), workdir="C:/work")).code == "disk-wipe"


def test_windows_shell_root_delete_and_narrow_delete_are_distinguished() -> None:
    policy = CatastrophicActionPolicy(
        path_flavor="windows",
        home=Path("C:/Users/test"),
    )

    root = policy.assess(
        _command(
            ("C:/Windows/System32/cmd.exe", "/D", "/S", "/C", "rmdir /s /q C:\\"),
            workdir="C:/work",
        )
    )
    narrow = policy.assess(
        _command(
            (
                "C:/Program Files/PowerShell/7/pwsh.exe",
                "-Command",
                "Remove-Item -Recurse -Force C:\\work\\tmp",
            ),
            workdir="C:/work",
        )
    )

    assert root.code == "critical-root-recursive-delete"
    assert narrow.level == "normal"


def test_structured_filesystem_root_delete_is_catastrophic_but_child_is_not() -> None:
    root = Path.cwd().anchor or "C:\\"
    policy = CatastrophicActionPolicy(path_flavor="windows", home=Path("C:/Users/test"))
    catastrophic = PendingFilesystemApproval.create(
        session_id="sid-1",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="test",
        items=(),
        actions=(ApprovalAction("a1", "delete", Path(root), recursive=True),),
    )
    narrow = PendingFilesystemApproval.create(
        session_id="sid-1",
        run_id="run-1",
        tool_call_id="tool-2",
        reason="test",
        items=(),
        actions=(ApprovalAction("a2", "delete", Path(root) / "tmp" / "build", recursive=True),),
    )

    assert policy.assess(catastrophic).code == "critical-root-recursive-delete"
    assert policy.assess(narrow).level == "normal"


def test_risk_fingerprint_changes_with_command_target() -> None:
    policy = CatastrophicActionPolicy(
        path_flavor="posix",
        home=PurePosixPath("/home/test"),  # type: ignore[arg-type]
    )

    first = policy.assess(_command(("rm", "-rf", "/work/a"), workdir="/work"))
    second = policy.assess(_command(("rm", "-rf", "/work/b"), workdir="/work"))

    assert first.fingerprint != second.fingerprint


def test_first_party_skill_replacement_flags_remain_normal() -> None:
    policy = CatastrophicActionPolicy(
        path_flavor="windows",
        home=Path("C:/Users/test"),
    )

    local = policy.assess(
        _command(
            (
                "python",
                "scripts/install-skill-from-github.py",
                "--update",
                "demo",
                "--replace-local-changes",
            ),
            workdir="C:/skill-snapshot",
            execution="sandboxed",
            skill=_installer_binding(),
        )
    )
    unmanaged = policy.assess(
        _command(
            (
                "python",
                "scripts/install-skill-from-github.py",
                "--repo",
                "owner/repo",
                "--path",
                "demo",
                "--name",
                "demo",
                "--replace-unmanaged",
            ),
            workdir="C:/skill-snapshot",
            execution="sandboxed",
            skill=_installer_binding(),
        )
    )
    cmd_prefixed = policy.assess(
        _command(
            (
                "cmd.exe",
                "/c",
                'set "PYTHONPATH=scripts" && python scripts\\install-skill-from-github.py '
                "--update demo --replace-local-changes",
            ),
            workdir="C:/skill-snapshot",
            execution="sandboxed",
            skill=_installer_binding(),
        )
    )

    assert local.level == "normal"
    assert local.code is None
    assert unmanaged.level == "normal"
    assert unmanaged.code is None
    assert cmd_prefixed.level == "normal"
    assert cmd_prefixed.code is None


def test_skill_binding_remains_part_of_normal_command_fingerprint() -> None:
    policy = CatastrophicActionPolicy(path_flavor="windows", home=Path("C:/Users/test"))
    argv = ("python", "custom.py", "--replace-local-changes")

    unrelated = policy.assess(_command(argv, workdir="C:/work", execution="sandboxed"))
    changed_snapshot = policy.assess(
        _command(
            (
                "python",
                "scripts/install-skill-from-github.py",
                "--update",
                "demo",
                "--replace-local-changes",
            ),
            workdir="C:/skill-snapshot",
            execution="sandboxed",
            skill=_installer_binding(fingerprint="b" * 64),
        )
    )
    original_snapshot = policy.assess(
        _command(
            (
                "python",
                "scripts/install-skill-from-github.py",
                "--update",
                "demo",
                "--replace-local-changes",
            ),
            workdir="C:/skill-snapshot",
            execution="sandboxed",
            skill=_installer_binding(),
        )
    )

    assert unrelated.level == "normal"
    assert changed_snapshot.fingerprint != original_snapshot.fingerprint
