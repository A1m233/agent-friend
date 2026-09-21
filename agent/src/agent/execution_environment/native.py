"""Product-owned native executables shared by sandbox and host process tools."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path

from .models import ManagedEnvironmentError

ALIASES = frozenset({"pwsh", "pdftoppm", "pdfinfo"})


class NativeTools:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._tools: dict[str, Path] | None = None

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> NativeTools | None:
        root = env.get("AGENT_FRIEND_EXECUTION_ENVIRONMENT_ROOT", "").strip()
        return cls(Path(root) / "native") if root and os.name == "nt" else None

    def _path(self, value: str) -> Path:
        raw = self.root / value
        if raw.is_symlink() or raw.is_junction():
            raise ValueError("linked native resource")
        path = raw.resolve()
        if self.root not in path.parents or not path.is_file():
            raise ValueError("invalid native resource")
        return path

    def validate(self) -> None:
        if self._tools is not None:
            return
        try:
            manifest = json.loads((self.root / "identity.json").read_text("utf-8"))
            lock = self.root / "vendor-lock.json"
            if (
                manifest["schemaVersion"] != 1
                or manifest["identity"] != hashlib.sha256(lock.read_bytes()).hexdigest()
            ):
                raise ValueError("native identity mismatch")
            inventory = manifest["files"]
            expected = {entry["path"] for entry in inventory}
            actual = {
                p.relative_to(self.root).as_posix()
                for p in self.root.rglob("*")
                if p.is_file() and p != self.root / "identity.json"
            }
            if expected != actual or len(expected) != len(inventory):
                raise ValueError("native inventory mismatch")
            for entry in inventory:
                path = self._path(entry["path"])
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if path.stat().st_size != entry["size"] or digest != entry["sha256"]:
                    raise ValueError("native content mismatch")
            tools = manifest["tools"]
            if set(tools) != ALIASES:
                raise ValueError("native executable contract mismatch")
            self._tools = {name: self._path(relative) for name, relative in tools.items()}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ManagedEnvironmentError(
                "managed_native_tools_unavailable",
                "产品自带 PowerShell/PDF 工具缺失或校验失败；请修复执行环境，不要改用未知 PATH 程序重试。",
                provider="native",
            ) from exc

    def resolve(self, name: str) -> Path | None:
        if "/" in name or "\\" in name:
            return None
        alias = name.casefold().removesuffix(".exe")
        if alias not in ALIASES:
            return None
        self.validate()
        assert self._tools is not None
        return self._tools[alias]

    def apply_environment(self, env: dict[str, str]) -> dict[str, str]:
        self.validate()
        assert self._tools is not None
        directories = dict.fromkeys(str(path.parent) for path in self._tools.values())
        result = dict(env)
        result["PATH"] = os.pathsep.join((*directories, result.get("PATH", "")))
        result["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
        result["POWERSHELL_UPDATECHECK"] = "Off"
        return result
