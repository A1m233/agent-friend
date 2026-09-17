"""Deterministic catastrophic-action policy for typed local approvals."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import posixpath
import re
import shlex
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal

from .models import RiskAssessment

PathFlavor = Literal["windows", "posix"]
_SHELL_NAMES = {"sh", "bash", "zsh", "dash"}
_POWERSHELL_NAMES = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
_CMD_NAMES = {"cmd", "cmd.exe"}
_DIRECT_WRAPPERS = {"command", "env", "nohup", "sudo"}
_MAX_WRAPPER_DEPTH = 3


class CatastrophicActionPolicy:
    """Classify a deliberately narrow set of mechanically provable disasters.

    This is not a general script or intent analyzer.  Unknown executables remain
    normal so the product does not claim that arbitrary program semantics were
    reviewed.  Existing sandbox and protected-path policies remain independent.
    """

    def __init__(
        self,
        *,
        critical_roots: Iterable[Path] = (),
        workspace_root_provider: Callable[[str], Path | None] | None = None,
        home: Path | None = None,
        path_flavor: PathFlavor | None = None,
    ) -> None:
        self._path_flavor: PathFlavor = path_flavor or ("windows" if os.name == "nt" else "posix")
        self._home = home or Path.home()
        self._workspace_root_provider = workspace_root_provider
        roots = [self._root_for_flavor(), self._home, *critical_roots]
        self._critical_root_keys = frozenset(self._path_key(str(item), None) for item in roots)

    def assess(self, request: Any) -> RiskAssessment:
        if getattr(request, "kind", None) == "filesystem":
            return self._assess_filesystem(request)
        if getattr(request, "kind", None) in {"command", "desktop_application"}:
            return self._assess_command(request)
        return self._normal({"kind": str(getattr(request, "kind", "unknown"))})

    def _assess_filesystem(self, request: Any) -> RiskAssessment:
        actions = []
        for action in getattr(request, "actions", ()):
            target = str(action.target)
            entry = {
                "action": action.action,
                "target": self._path_key(target, None),
                "recursive": bool(action.recursive),
                "expected_identity": action.expected_identity,
            }
            actions.append(entry)
            if (
                action.action == "delete"
                and action.recursive
                and self._is_critical_target(target, request.session_id, None)
            ):
                return self._catastrophic(
                    "critical-root-recursive-delete",
                    "递归删除目标解析为关键根路径。",
                    ("structured filesystem delete targets a critical root",),
                    {"kind": "filesystem", "actions": actions},
                )
        return self._normal({"kind": "filesystem", "actions": actions})

    def _assess_command(self, request: Any) -> RiskAssessment:
        details = request.details
        argv = tuple(details.launch_argv or details.argv)
        workdir = details.launch_workdir or details.workdir
        segments = _command_segments(argv)
        normalized_segments: list[dict[str, object]] = []
        for dialect, tokens in segments:
            normalized_segments.append({"dialect": dialect, "tokens": list(tokens)})
        for dialect, tokens in segments:
            if _is_disk_wipe(dialect, tokens, workdir=workdir):
                return self._catastrophic(
                    "disk-wipe",
                    "命令包含磁盘、卷或分区的格式化或清除操作。",
                    (f"recognized destructive disk command: {_command_name(tokens)}",),
                    self._command_fingerprint_payload(request, normalized_segments),
                )
            delete = _forced_recursive_delete(dialect, tokens)
            if delete is None:
                continue
            targets = delete
            if not targets or any(_unresolved_target(target, dialect) for target in targets):
                return self._catastrophic(
                    "unresolved-recursive-delete-target",
                    "强制递归删除的真实目标无法在执行前确定。",
                    ("forced recursive delete contains an unresolved target",),
                    self._command_fingerprint_payload(request, normalized_segments),
                )
            for target in targets:
                if self._is_critical_target(target, request.session_id, workdir):
                    return self._catastrophic(
                        "critical-root-recursive-delete",
                        "强制递归删除目标解析为关键根路径。",
                        (f"forced recursive delete targets critical root: {target}",),
                        self._command_fingerprint_payload(request, normalized_segments),
                    )
        return self._normal(self._command_fingerprint_payload(request, normalized_segments))

    def _command_fingerprint_payload(
        self,
        request: Any,
        segments: list[dict[str, object]],
    ) -> dict[str, object]:
        details = request.details
        skill = details.skill
        return {
            "kind": "command",
            "session_id": request.session_id,
            "run_id": request.run_id,
            "tool_call_id": request.tool_call_id,
            "argv": list(details.launch_argv or details.argv),
            "execution": details.execution,
            "workdir": self._path_key(details.launch_workdir or details.workdir, None),
            "filesystem_access": details.filesystem_access,
            "network_access": details.network_access,
            "skill": (
                {
                    "skill_id": skill.skill_id,
                    "entry": skill.entry,
                    "workdir": skill.workdir,
                    "fingerprint": skill.fingerprint,
                }
                if skill is not None
                else None
            ),
            "segments": segments,
        }

    def _is_critical_target(
        self,
        raw_target: str,
        session_id: str,
        workdir: str | None,
    ) -> bool:
        key = self._path_key(raw_target, workdir)
        roots = set(self._critical_root_keys)
        if self._workspace_root_provider is not None:
            workspace = self._workspace_root_provider(session_id)
            if workspace is not None:
                roots.add(self._path_key(str(workspace), None))
        # Deleting the complete current working directory via '.' is broad even
        # when that directory came from an explicit host path rather than the
        # managed session workspace.
        if workdir is not None and raw_target.strip().strip("\"'") in {".", "./", ".\\"}:
            roots.add(self._path_key(workdir, None))
        return any(self._target_contains_root(key, root) for root in roots)

    def _path_key(self, raw: str, workdir: str | None) -> str:
        value = raw.strip().strip("\"'")
        if (self._path_flavor == "windows") == (os.name == "nt") and not _unresolved_target(
            value, "powershell" if os.name == "nt" else "posix"
        ):
            candidate = Path(value).expanduser()
            if workdir and not candidate.is_absolute():
                candidate = Path(workdir) / candidate
            try:
                value = str(candidate.resolve(strict=False))
            except OSError:
                value = str(candidate)
        if self._path_flavor == "windows":
            if workdir and not ntpath.isabs(value):
                value = ntpath.join(workdir, value)
            normalized = ntpath.normcase(ntpath.normpath(value))
            drive, tail = ntpath.splitdrive(normalized)
            if drive and tail in {"", "."}:
                normalized = drive + "\\"
            return normalized.rstrip("\\") + "\\" if _is_windows_root(normalized) else normalized
        if workdir and not posixpath.isabs(value):
            value = posixpath.join(workdir, value)
        return posixpath.normpath(value)

    def _target_contains_root(self, target: str, root: str) -> bool:
        try:
            if self._path_flavor == "windows":
                return ntpath.commonpath((target, root)) == target
            return posixpath.commonpath((target, root)) == target
        except ValueError:
            return False

    def _root_for_flavor(self) -> str:
        if self._path_flavor == "windows":
            anchor = self._home.anchor or Path.cwd().anchor
            return anchor or "C:\\"
        return "/"

    @staticmethod
    def _normal(payload: dict[str, object]) -> RiskAssessment:
        return RiskAssessment("normal", _fingerprint({"assessment_code": None, **payload}))

    @staticmethod
    def _catastrophic(
        code: str,
        message: str,
        evidence: tuple[str, ...],
        payload: dict[str, object],
    ) -> RiskAssessment:
        return RiskAssessment(
            "catastrophic",
            _fingerprint({"assessment_code": code, **payload}),
            code=code,
            message=message,
            evidence=evidence,
        )


def _fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _command_segments(argv: tuple[str, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(_unwrap_segment("direct", argv, depth=0))


def _unwrap_segment(
    dialect: str,
    tokens: tuple[str, ...],
    *,
    depth: int,
) -> list[tuple[str, tuple[str, ...]]]:
    if not tokens:
        return []
    if depth >= _MAX_WRAPPER_DEPTH:
        return [(dialect, tokens)]
    name = _command_name(tokens)
    if name in _SHELL_NAMES:
        script = _argument_after(tokens, {"-c", "-lc"})
        if script is not None:
            return _parse_script(script, "posix", depth + 1)
    if name in _POWERSHELL_NAMES:
        script = _argument_after(tokens, {"-command", "/command", "-c"})
        if script is not None:
            return _parse_script(script, "powershell", depth + 1)
    if name in _CMD_NAMES:
        script = _argument_after(tokens, {"/c", "-c"})
        if script is not None:
            return _parse_script(script, "cmd", depth + 1)
    if name in _DIRECT_WRAPPERS:
        wrapped = _unwrap_direct_wrapper(name, tokens[1:])
        if wrapped:
            return _unwrap_segment(dialect, wrapped, depth=depth + 1)
    return [(dialect, tokens)]


def _parse_script(script: str, dialect: str, depth: int) -> list[tuple[str, tuple[str, ...]]]:
    result: list[tuple[str, tuple[str, ...]]] = []
    for segment in _split_shell_segments(script, dialect):
        try:
            parsed = tuple(
                _strip_token_quotes(token)
                for token in shlex.split(segment, posix=dialect == "posix")
            )
        except ValueError:
            parsed = (segment,)
        result.extend(_unwrap_segment(dialect, parsed, depth=depth))
    return result


def _split_shell_segments(script: str, dialect: str) -> tuple[str, ...]:
    separators = {";", "|", "&"}
    if dialect == "cmd":
        separators = {"|", "&"}
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(script):
        char = script[index]
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
            index += 1
            continue
        if char in separators:
            value = "".join(current).strip()
            if value:
                parts.append(value)
            current = []
            while index + 1 < len(script) and script[index + 1] == char:
                index += 1
            index += 1
            continue
        current.append(char)
        index += 1
    value = "".join(current).strip()
    if value:
        parts.append(value)
    return tuple(parts)


def _argument_after(tokens: tuple[str, ...], options: set[str]) -> str | None:
    for index, token in enumerate(tokens[:-1]):
        if token.casefold() in options:
            return tokens[index + 1]
    return None


def _command_name(tokens: tuple[str, ...]) -> str:
    if not tokens:
        return ""
    return ntpath.basename(tokens[0].strip().strip("\"'")).casefold()


def _is_disk_wipe(dialect: str, tokens: tuple[str, ...], *, workdir: str) -> bool:
    if not tokens:
        return False
    name = _command_name(tokens)
    lowered = tuple(token.casefold() for token in tokens[1:])
    if name.startswith("mkfs") or name.startswith("newfs"):
        return True
    if name == "wipefs" and any(token in {"-a", "--all"} for token in lowered):
        return not any(token in {"-n", "--no-act"} for token in lowered)
    if name == "dd":
        return any(
            re.match(r"^of=/dev/(?:disk|sd|hd|nvme|mmcblk)", token, re.I) for token in tokens
        )
    if (
        name == "diskutil"
        and lowered
        and lowered[0]
        in {
            "erasedisk",
            "erasevolume",
            "partitiondisk",
            "secureerase",
        }
    ):
        return True
    if name in {"format", "format.com"}:
        return any(re.match(r"^[a-z]:[\\/]?$", token, re.I) for token in tokens[1:])
    if dialect == "powershell" and name in {"clear-disk", "format-volume"}:
        return True
    if name in {"diskpart", "diskpart.exe"}:
        script = _argument_after(tokens, {"/s", "-s"})
        if script is None:
            return True
        path = Path(script)
        if not path.is_absolute():
            path = Path(workdir) / path
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[:65536]
        except OSError:
            return False
        return any(
            line.strip().casefold() in {"clean", "clean all"}
            or line.strip().casefold().startswith("format ")
            for line in content.splitlines()
        )
    return False


def _forced_recursive_delete(
    dialect: str,
    tokens: tuple[str, ...],
) -> tuple[str, ...] | None:
    if not tokens:
        return None
    name = _command_name(tokens)
    args = tokens[1:]
    if name == "rm":
        flags = "".join(token.lstrip("-") for token in args if token.startswith("-"))
        if "f" not in flags.casefold() or "r" not in flags.casefold():
            return None
        return tuple(token for token in args if not token.startswith("-"))
    if dialect == "powershell" and name in {
        "remove-item",
        "ri",
        "rm",
        "del",
        "erase",
        "rd",
        "rmdir",
    }:
        lowered = {token.casefold() for token in args if token.startswith("-")}
        if "-recurse" not in lowered or "-force" not in lowered:
            return None
        return tuple(
            token
            for token in args
            if not token.startswith("-") and token.casefold() not in {"path", "literalpath"}
        )
    if dialect == "cmd" and name in {"rmdir", "rd"}:
        lowered = {token.casefold() for token in args if token.startswith("/")}
        if "/s" not in lowered or "/q" not in lowered:
            return None
        return tuple(token for token in args if not token.startswith("/"))
    if dialect == "cmd" and name in {"del", "erase"}:
        lowered = {token.casefold() for token in args if token.startswith("/")}
        if not {"/s", "/f", "/q"}.issubset(lowered):
            return None
        return tuple(token for token in args if not token.startswith("/"))
    return None


def _unresolved_target(target: str, dialect: str) -> bool:
    if any(char in target for char in "*?["):
        return True
    if dialect == "posix":
        return "$" in target or "`" in target
    if dialect == "powershell":
        return "$" in target
    if dialect == "cmd":
        return bool(re.search(r"%[^%]+%|![^!]+!", target))
    return False


def _strip_token_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def _unwrap_direct_wrapper(name: str, args: tuple[str, ...]) -> tuple[str, ...]:
    if name == "env":
        index = 0
        while index < len(args) and (args[index].startswith("-") or "=" in args[index]):
            index += 1
        return args[index:]
    if name == "sudo":
        options_with_value = {"-C", "-D", "-g", "-h", "-p", "-R", "-T", "-u"}
        index = 0
        while index < len(args):
            value = args[index]
            if value == "--":
                return args[index + 1 :]
            if not value.startswith("-"):
                return args[index:]
            index += 2 if value in options_with_value and index + 1 < len(args) else 1
        return ()
    index = 0
    while index < len(args) and args[index].startswith("-"):
        index += 1
    return args[index:]


def _is_windows_root(value: str) -> bool:
    drive, tail = ntpath.splitdrive(value)
    return bool(drive and tail in {"", "\\", "/"})


__all__ = ["CatastrophicActionPolicy", "PathFlavor"]
