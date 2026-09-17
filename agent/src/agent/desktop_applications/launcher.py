"""Bounded native-helper protocol. Never own or terminate the launched application."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ..tool_runtime.control import ToolInvocationControl


class NativeApplicationLauncher:
    def __init__(self, executable: Path | None, *, build_identity: str | None = None) -> None:
        self.executable = executable
        self.build_identity = build_identity

    def validate(self) -> str | None:
        if os.name != "nt":
            return "unsupported_platform"
        executable = self.executable
        if executable is None:
            return "launcher_unavailable"
        try:
            manifest = json.loads(
                executable.with_suffix(".identity.json").read_text(encoding="utf-8")
            )
            if not isinstance(manifest, dict) or not isinstance(manifest.get("identity"), dict):
                return "launcher_identity_mismatch"
            digest = hashlib.sha256(executable.read_bytes()).hexdigest()
            identity = manifest["identity"]
            if manifest.get("schema_version") != 1 or manifest.get("sha256") != digest:
                return "launcher_identity_mismatch"
            if (
                identity.get("protocol_version") != 1
                or identity.get("product") != "agent-friend-app-launcher"
            ):
                return "launcher_identity_mismatch"
            if self.build_identity is not None and json.loads(
                identity.get("build_identity", "")
            ) != json.loads(self.build_identity):
                return "launcher_identity_mismatch"
            actual = subprocess.run(
                [str(executable), "--identity"],
                capture_output=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
            if (
                actual.returncode != 0
                or len(actual.stdout) > 16384
                or json.loads(actual.stdout) != identity
            ):
                return "launcher_identity_mismatch"
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            return "launcher_unavailable"
        return None

    def launch(self, request: dict[str, object], control: ToolInvocationControl) -> dict[str, Any]:
        error = self.validate()
        if error:
            return {"status": "failed", "error_code": error}
        control.check_cancelled()
        assert self.executable is not None
        raw = json.dumps({"protocol_version": 1, **request}, ensure_ascii=False).encode("utf-8")
        if len(raw) > 65536:
            return {"status": "failed", "error_code": "request_too_large"}
        try:
            child = subprocess.Popen(
                [str(self.executable)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError:
            return {"status": "failed", "error_code": "launcher_start_failed"}

        def cancel(_reason: str) -> None:
            if child.poll() is None:
                child.kill()  # Only our waiting helper, never Explorer or its application.

        token = control.add_cancel_callback(cancel)
        try:
            try:
                stdout, _stderr = child.communicate(raw, timeout=12)
            except (subprocess.TimeoutExpired, OSError):
                cancel("timeout")
                child.communicate(timeout=5)
                return {"status": "unknown", "error_code": "launch_outcome_unknown"}
            if len(stdout) > 65536:
                return {"status": "unknown", "error_code": "invalid_launcher_response"}
            try:
                records = [json.loads(line) for line in stdout.splitlines()]
                result = records[-1]
            except (ValueError, IndexError):
                return {"status": "unknown", "error_code": "launch_outcome_unknown"}
            if not isinstance(result, dict) or result.get("status") not in {
                "accepted",
                "failed",
                "unknown",
            }:
                return {"status": "unknown", "error_code": "invalid_launcher_response"}
            if result.get("status") == "accepted" and (
                child.returncode != 0 or result.get("protocol_version") != 1
            ):
                return {"status": "unknown", "error_code": "invalid_launcher_response"}
            return result
        finally:
            control.remove_cancel_callback(token)
            if child.poll() is None:
                cancel("cleanup")
                child.wait(timeout=5)
