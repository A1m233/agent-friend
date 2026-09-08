"""Bounded, opt-in PresentMon capture. Never installs tools or elevates itself."""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import subprocess
import sys
import time
import uuid
from collections import Counter
from pathlib import Path


def doctor(executable: Path | None) -> dict[str, object]:
    windows = sys.platform == "win32"
    elevated = bool(ctypes.windll.shell32.IsUserAnAdmin()) if windows else False
    available = executable is not None and executable.is_file() and not executable.is_symlink()
    return {
        "supported": windows,
        "elevated": elevated,
        "toolAvailable": available,
        "ready": windows and elevated and available,
        "scope": "process-swapchain",
        "windowMapping": "not-verified",
        "automaticElevation": False,
        "automaticDownload": False,
    }


def summarize(path: Path, max_bytes: int = 256 * 1024 * 1024) -> dict[str, object]:
    if path.is_symlink() or path.stat().st_size > max_bytes:
        raise ValueError("invalid-or-oversized-trace")
    streams: dict[tuple[int, str], dict[str, object]] = {}
    modes: Counter[str] = Counter()
    missing_display = invalid = rows = 0
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not {"ProcessID", "SwapChainAddress"}.issubset(reader.fieldnames or []):
            raise ValueError("unsupported-presentmon-columns")
        has_display = "DisplayedTime" in (reader.fieldnames or [])
        for row in reader:
            rows += 1
            if rows > 2_000_000:
                raise ValueError("trace-row-limit")
            try:
                pid = int(row["ProcessID"])
                address = int(row["SwapChainAddress"], 16)
                if pid <= 0 or not 0 <= address <= 2**64 - 1:
                    raise ValueError
                swapchain = hex(address)
            except (ValueError, TypeError):
                invalid += 1
                continue
            key = (pid, swapchain)
            if key not in streams:
                if len(streams) >= 512:
                    raise ValueError("trace-stream-limit")
                streams[key] = {
                    "pid": pid,
                    "swapchain": swapchain,
                    "presents": 0,
                    "displayed": 0,
                    "notDisplayed": 0,
                }
            item = streams[key]
            item["presents"] = int(item["presents"]) + 1
            displayed = row.get("DisplayedTime")
            if not has_display or displayed in (None, ""):
                missing_display += 1
            elif displayed == "NA":
                item["notDisplayed"] = int(item["notDisplayed"]) + 1
            else:
                try:
                    value = float(displayed)
                    if not 0 <= value < float("inf"):
                        raise ValueError
                    item["displayed"] = int(item["displayed"]) + 1
                except ValueError:
                    missing_display += 1
            mode = row.get("PresentMode", "")
            allowed_modes = {
                "Hardware: Legacy Flip",
                "Hardware: Legacy Copy to front buffer",
                "Hardware: Independent Flip",
                "Composed: Flip",
                "Hardware Composed: Independent Flip",
                "Composed: Copy with GPU GDI",
                "Composed: Copy with CPU GDI",
            }
            modes[mode if mode in allowed_modes else "unknown"] += 1
    return {
        "schemaVersion": 1,
        "scope": "process-swapchain",
        "windowMapping": "not-verified",
        "pixelCorrectness": "unknown",
        "rows": rows,
        "invalidRows": invalid,
        "missingDisplayMetrics": missing_display,
        "presentModes": dict(modes),
        "streams": list(streams.values()),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def capture(
    executable: Path, pids: list[int], seconds: int, max_mib: int, output: Path
) -> dict[str, object]:
    if not doctor(executable)["ready"]:
        raise ValueError("capture-requires-windows-admin-and-local-presentmon")
    if not pids or len(pids) > 16 or any(pid <= 0 for pid in pids):
        raise ValueError("explicit-positive-target-pids-required")
    if not 1 <= seconds <= 120 or not 1 <= max_mib <= 256:
        raise ValueError("capture-budget-out-of-range")
    tool_hash = sha256(executable)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    trace = output / "presentmon.csv"
    session = "agent-friend-render-" + uuid.uuid4().hex
    # No name-wide capture, input tracking, injection, or stop-existing-session switches.
    command = [
        str(executable.resolve()),
        "--session_name",
        session,
        "--output_file",
        str(trace),
        "--timed",
        str(seconds),
        "--terminate_after_timed",
        "--no_track_input",
        "--no_console_stats",
    ]
    for pid in pids:
        command += ["--process_id", str(pid)]
    started = time.monotonic()
    reason = "completed"
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        while process.poll() is None:
            if trace.exists() and trace.stat().st_size >= max_mib * 1024 * 1024:
                reason = "size-limit"
                break
            if time.monotonic() - started > seconds + 10:
                reason = "timeout"
                break
            time.sleep(0.1)
    except OSError:
        reason = "capture-start-or-monitor-failed"
    except KeyboardInterrupt:
        reason = "user-stopped"
    finally:
        if process is not None and process.poll() is None:
            # Stop only this uniquely owned session before terminating our child.
            try:
                subprocess.run(
                    [
                        str(executable.resolve()),
                        "--session_name",
                        session,
                        "--terminate_existing_session",
                    ],
                    timeout=5,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.TimeoutExpired):
                reason = "session-stop-failed"
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    result: dict[str, object] = {
        "schemaVersion": 1,
        "session": session,
        "targetPids": pids,
        "seconds": round(time.monotonic() - started, 3),
        "stopReason": reason,
        "exitCode": process.returncode if process else None,
        "toolSha256": tool_hash,
        "maxMiB": max_mib,
        "scope": "process-swapchain",
        "windowMapping": "not-verified",
        "rawTraceSensitive": True,
        "includedInDiagnosticBundle": False,
    }
    if trace.exists():
        observed_bytes = trace.stat().st_size
        cap = max_mib * 1024 * 1024
        result["observedBytesBeforeRetentionCap"] = observed_bytes
        if observed_bytes > cap:
            with trace.open("r+b") as retained:
                retained.truncate(cap)
            result["retentionTruncated"] = True
        result["traceBytes"] = trace.stat().st_size
        result["traceSha256"] = sha256(trace)
        try:
            result["summary"] = summarize(trace)
        except (ValueError, csv.Error, UnicodeError):
            result["summaryError"] = "incomplete-or-unsupported-trace"
    summary = result.get("summary", {})
    result["outcome"] = (
        "complete"
        if (
            reason == "completed"
            and result["exitCode"] == 0
            and isinstance(summary, dict)
            and summary.get("rows", 0) > 0
            and not result.get("retentionTruncated")
        )
        else "partial"
    )
    (output / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    probe = sub.add_parser("doctor")
    probe.add_argument("--presentmon", type=Path)
    record = sub.add_parser("capture")
    record.add_argument("--presentmon", type=Path, required=True)
    record.add_argument("--pid", type=int, action="append", required=True)
    record.add_argument("--seconds", type=int, default=30)
    record.add_argument("--max-mib", type=int, default=64)
    record.add_argument("--output", type=Path, required=True)
    report = sub.add_parser("summarize")
    report.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "doctor":
            result = doctor(args.presentmon)
        elif args.action == "capture":
            result = capture(args.presentmon, args.pid, args.seconds, args.max_mib, args.output)
        else:
            result = summarize(args.path)
        print(json.dumps(result, indent=2))
        return 1 if args.action == "capture" and result.get("outcome") != "complete" else 0
    except (ValueError, OSError, csv.Error) as error:
        # Do not echo trace rows or arbitrary process output.
        print(json.dumps({"status": "failed", "errorType": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
