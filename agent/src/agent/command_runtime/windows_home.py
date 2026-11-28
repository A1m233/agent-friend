"""Host-owned directories must remain accessible across elevated sandbox setup.

Only explicitly supplied runtime directories may be repaired. The host grant is
inheritable inside Codex home; protected ACLs, other ACEs and owners are preserved.
"""

from __future__ import annotations

import base64
import os
import stat
import tempfile
from pathlib import Path


def home_directories(home: Path, managed_root: Path | None) -> tuple[Path, ...]:
    home = home.absolute()
    if managed_root is None:
        return (home,)
    root = managed_root.absolute()
    if home.parent != root or root == root.parent:
        raise ValueError("managed_home_root must be the dedicated parent of codex_home")
    return (root, home)


def prepare_home(directories: tuple[Path, ...]) -> None:
    """Create as the unelevated host, and verify access before provisioning."""
    for directory in directories:
        # Do not follow a junction/symlink into a different privileged repair scope.
        for ancestor in (*reversed(directory.parents), directory):
            try:
                info = ancestor.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("sandbox host directory must not traverse a reparse point")
        directory.mkdir(parents=True, exist_ok=True)
        with os.scandir(directory):
            pass
        with tempfile.TemporaryFile(dir=directory):
            pass
    # Elevated provisioning also creates this host-consumed file. A directory
    # grant alone does not repair an existing administrator-owned config.
    config = directories[-1] / "config.toml"
    try:
        with config.open("rb"):
            pass
    except FileNotFoundError:
        pass


def elevated_command(script: str, system_root: str) -> list[str]:
    """Run a bounded script through the same visible UAC boundary as setup."""
    powershell = Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    parent = (
        "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
        f"$p=Start-Process -FilePath {literal(str(powershell))} "
        "-ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',"
        f"'{encoded}') -Verb RunAs -Wait -PassThru -WindowStyle Hidden;"
        "[Console]::Out.WriteLine('AF_ELEVATED_WAIT_COMPLETED');exit $p.ExitCode"
    )
    return [
        str(powershell),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        base64.b64encode(parent.encode("utf-16-le")).decode("ascii"),
    ]


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def repair_home_script(directories: tuple[Path, ...], user: str) -> str:
    """Grant host access without resetting owners, other ACEs or protected ACLs.

    Validate the entire path before changing any ACL. Missing descendants are
    created later by the unelevated host, never by this script.
    """
    paths = ",".join(literal(str(path)) for path in directories)
    return (
        "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
        f"$targets=@({paths});"
        f"$account=[Security.Principal.NTAccount]::new({literal(user)});"
        "$sid=$account.Translate([Security.Principal.SecurityIdentifier]);"
        "foreach($target in $targets){"
        "$cursor=[IO.DirectoryInfo]::new($target);"
        "while($null -ne $cursor){"
        "try{$attrs=[IO.File]::GetAttributes($cursor.FullName)}"
        "catch [IO.FileNotFoundException]{$cursor=$cursor.Parent;continue}"
        "catch [IO.DirectoryNotFoundException]{$cursor=$cursor.Parent;continue}"
        "if(($attrs -band [IO.FileAttributes]::ReparsePoint) -ne 0){"
        "throw 'sandbox host directory contains a reparse point'};"
        "if(($attrs -band [IO.FileAttributes]::Directory) -eq 0){"
        "throw 'sandbox host directory is not a directory'};"
        "$cursor=$cursor.Parent}};"
        "foreach($target in $targets){"
        "if(-not [IO.Directory]::Exists($target)){continue};"
        "$acl=Get-Acl -LiteralPath $target;"
        "$inherit=[Security.AccessControl.InheritanceFlags]::None;"
        "if($target -eq $targets[-1]){"
        "$inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'};"
        "$rule=[Security.AccessControl.FileSystemAccessRule]::new("
        "$sid,[Security.AccessControl.FileSystemRights]::FullControl,"
        "$inherit,"
        "[Security.AccessControl.PropagationFlags]::None,"
        "[Security.AccessControl.AccessControlType]::Allow);"
        "$acl.AddAccessRule($rule);Set-Acl -LiteralPath $target -AclObject $acl};"
    )
