"""Windows-only isolated installer startup/export acceptance; never targets production."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from startup_evidence import ROOT, digest
from startup_journal import analyze


def summarize_network(path: Path, allowed_ports: tuple[int, ...]) -> dict[str, Any]:
    # Chromium writes one event per line. Forced process cleanup may leave the final
    # document unfinished; retain that limitation and count only complete events.
    text = path.read_text(encoding="utf-8")
    complete = True
    try:
        events = json.loads(text)["events"]
    except json.JSONDecodeError:
        complete = False
        events = []
        for line in text.splitlines():
            with suppress(json.JSONDecodeError):
                row = json.loads(line.rstrip(","))
                if isinstance(row, dict) and "params" in row:
                    events.append(row)
    ports: dict[int, int] = {}
    for event in events:
        url = event.get("params", {}).get("url")
        if not isinstance(url, str):
            continue
        parsed = urlsplit(url)
        if parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port:
            ports[parsed.port] = ports.get(parsed.port, 0) + 1
    return {
        "recording": "Chromium NetLog enabled before shell launch",
        "completeDocument": complete,
        "loopbackUrlEventsByPort": ports,
        "ok": bool(ports) and set(ports).issubset(allowed_ports),
    }


def collect(installer: Path, output: Path, report: dict[str, Any]) -> None:
    if os.name != "nt":
        raise ValueError("installed-evidence requires Windows")
    sys.path.insert(0, str(ROOT / "scripts/package"))
    from install_contract import (
        INSTALLER_SMOKE_PROFILE,
        validate_profile_isolation,
    )
    from installer_smoke import (
        _exact_smoke_processes,
        _run_installer,
        _stop_exact_smoke_processes,
        _windows_paths,
        cleanup_smoke_state,
    )
    from releases import validate_release_directory
    from windows_shell_paths import verify_smoke_environment

    verify_smoke_environment()
    validate_profile_isolation()
    installer = installer.resolve(strict=True)
    manifest = validate_release_directory(installer.parent, formal=False)
    if manifest.get("profile_id") != "installer-smoke":
        raise ValueError("only governed installer-smoke candidates may be installed")
    if installer.name != manifest["installer"]["name"]:
        raise ValueError("installer must be the exact executable verified by the release manifest")
    paths = _windows_paths()
    for key in ("install", "data", "settings", "logs", "move_install", "long_move_install"):
        if paths[key].exists():
            raise ValueError(f"existing isolated state must not be taken over: {key}")
    if _exact_smoke_processes(paths["install"]):
        raise ValueError("isolated profile already running")
    output.mkdir(parents=True, exist_ok=False)
    report.update(
        {"candidate": manifest, "installerSha256": digest(installer), "artifacts": str(output)}
    )
    # Do not inherit provider secrets, dev overrides or another task's lifecycle identity.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(
            (
                "AGENT_",
                "VOICE_",
                "DEEPSEEK_",
                "OPENAI_",
                "ANTHROPIC_",
                "KIMI_",
                "TAVILY_",
                "VOLCENGINE_",
            )
        )
        and not key.upper().endswith(("_API_KEY", "_SECRET_KEY"))
    }
    env["AGENT_BRIDGE_IM_ENABLED"] = "false"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        f"--remote-debugging-port={port} --remote-debugging-address=127.0.0.1"
    )
    shell = paths["install"] / f"{INSTALLER_SMOKE_PROFILE.main_binary_name}.exe"
    journal = paths["logs"] / "tauri.log.runtime"

    def launch() -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [str(shell)],
            cwd=shell.parent,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def events() -> list[dict[str, Any]]:
        if not journal.exists():
            return []
        rows = []
        for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
            with suppress(ValueError):
                rows.append(json.loads(line))
        return rows

    try:
        result = _run_installer(installer)
        if result.returncode != 0:
            raise ValueError(f"isolated installer failed: {result.returncode}")
        receipt = json.loads((paths["install"] / "active-install.json").read_text(encoding="utf-8"))
        if (
            receipt.get("buildId") != manifest.get("build_id")
            or receipt.get("profileId") != "installer-smoke"
        ):
            raise ValueError("installed identity does not match candidate")
        report["receipt"] = receipt
        interrupted = launch()
        deadline = time.monotonic() + 60
        interruption = None
        while time.monotonic() < deadline and interrupted.poll() is None:
            interruption = next(
                (
                    row
                    for row in events()
                    if row.get("processId") == interrupted.pid
                    and row.get("kind") == "phase-started"
                    and row.get("details", {}).get("phase") == "payload-verification"
                ),
                None,
            )
            if interruption:
                break
            time.sleep(0.01)
        if not interruption:
            raise ValueError("could not observe payload start for controlled interruption")
        interrupted.terminate()
        interrupted.wait(timeout=10)
        _stop_exact_smoke_processes(paths["install"])
        report["interruptedSession"] = interruption["processSessionId"]
        netlog = output / "webview-network.json"
        env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] += f' --log-net-log="{netlog}"'
        normal = launch()
        report["normalPid"] = normal.pid
        result = subprocess.run(
            [
                "node",
                str(ROOT / "scripts/startup-profile/installed-probe.mjs"),
                str(port),
                str(output / "desktop-probe.json"),
                str(INSTALLER_SMOKE_PROFILE.bridge_port),
                str(INSTALLER_SMOKE_PROFILE.voice_port),
            ],
            cwd=ROOT,
            timeout=240,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode:
            raise ValueError(f"desktop probe failed: {result.stderr[-1500:]}")
        desktop = json.loads((output / "desktop-probe.json").read_text(encoding="utf-8"))
        report["desktop"] = desktop
        bundle = Path(desktop["bundle"]["path"])
        shutil.copy2(bundle, output / "diagnostics.zip")
        report["bundleSha256"] = digest(output / "diagnostics.zip")
        _stop_exact_smoke_processes(paths["install"])
        report["network"] = summarize_network(
            netlog, (INSTALLER_SMOKE_PROFILE.bridge_port, INSTALLER_SMOKE_PROFILE.voice_port)
        )
        logs = []
        for source in paths["logs"].glob("tauri.log.runtime*"):
            target = output / source.name
            shutil.copy2(source, target)
            logs.append(target)
        normal_session = next(
            row["processSessionId"] for row in events() if row.get("processId") == normal.pid
        )
        report["normal"] = analyze(logs, normal_session)
        report["interrupted"] = analyze(logs, report["interruptedSession"])
        inspection = subprocess.run(
            [
                sys.executable,
                str(
                    ROOT
                    / ".cursor/skills/inspect-agent-friend-diagnostics/scripts/inspect_bundle.py"
                ),
                str(output / "diagnostics.zip"),
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        summary = json.loads(inspection.stdout)
        (output / "bundle-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        sessions = summary["startupTimeline"].get("processes", [])
        interrupted_row = next(
            (row for row in sessions if row["processSessionId"] == report["interruptedSession"]), {}
        )
        normal_row = next(
            (row for row in sessions if row["processSessionId"] == normal_session), {}
        )
        observed = summary["installIntegrity"]["runtime"]["observedReadiness"].get("bridge", {})
        report["serviceIdentityMatches"] = (
            all(
                observed.get(key) == receipt.get(key)
                for key in ("profileId", "buildId", "buildIdentitySha256")
            )
            and observed.get("service") == "bridge"
        )
        report["ok"] = bool(
            report["normal"]["phaseCoverageComplete"]
            and report["normal"]["frontendReadyObserved"]
            and report["normal"]["payloadVerification"]
            and interrupted_row.get("unfinishedPhases")
            and normal_row
            and desktop.get("editorEnabled")
            and desktop.get("inputVerified")
            and desktop.get("routingVerified")
            and report["network"]["ok"]
            and report["serviceIdentityMatches"]
        )
    finally:
        _stop_exact_smoke_processes(paths["install"])
        # Save failed-start logs too, before removing only the governed isolated namespace.
        for source in paths["logs"].glob("tauri.log.runtime*"):
            if not (output / source.name).exists():
                shutil.copy2(source, output / source.name)
        cleanup_smoke_state()
