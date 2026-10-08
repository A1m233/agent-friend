"""Run native failures only in isolated debug fixture processes, never a desktop runtime."""

from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import tempfile
import time
from pathlib import Path

MODES = (
    "normal",
    "panic-caught",
    "panic",
    "ud2",
    "av",
    "stack-overflow",
    "fastfail",
    "forcekill",
    "worker-timeout",
    "worker-failure",
    "watcher-delay",
    "watcher-unavailable",
    "watcher-early-death",
    "storage-unwritable",
    "both-killed",
    "service-first",
    "shell-first",
)


def inspect_dump(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        header = stream.read(32)
        if len(header) != 32 or header[:4] != b"MDMP":
            raise RuntimeError("invalid minidump header")
        count, directory = struct.unpack_from("<II", header, 8)
        if count > 1024:
            raise RuntimeError("unbounded minidump stream table")
        stream.seek(directory)
        entries = stream.read(count * 12)
        for index in range(count):
            kind, size, offset = struct.unpack_from("<III", entries, index * 12)
            if kind == 6:
                if size < 32:
                    raise RuntimeError("missing exception context")
                stream.seek(offset)
                data = stream.read(32)
                thread_id, _, code, flags = struct.unpack_from("<IIII", data)
                address = struct.unpack_from("<Q", data, 24)[0]
                return {
                    "threadId": thread_id,
                    "exceptionCode": f"0x{code:08x}",
                    "exceptionFlags": flags,
                    "address": f"0x{address:x}",
                }
    raise RuntimeError("missing minidump exception stream")


def run_mode(
    executable: Path, mode: str, destination: Path, root: Path | None = None
) -> dict[str, object]:
    root = root or Path(tempfile.mkdtemp(prefix=f"af-native-smoke-{mode}-"))
    started = time.monotonic()
    env = os.environ.copy()
    env["AGENT_FRIEND_LOG_DIR"] = str(root)
    env["AGENT_FRIEND_DATA_DIR"] = str(root / "unused-data")
    with (root / "fixture-output.txt").open("wb") as output:
        process = subprocess.Popen(
            [str(executable), "--af-native-fixture-v1", mode, str(root)],
            cwd=root,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            exit_code = process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError(f"{mode}: fixture process exceeded deadline") from None
    sessions: list[dict] = []
    if mode in {"watcher-unavailable", "watcher-early-death", "storage-unwritable"}:
        coverage = json.loads((root / "fixture-coverage.json").read_text(encoding="utf-8"))
        expected_failure = {
            "watcher-unavailable": "helper-start-failed",
            "watcher-early-death": "watcher-exited",
            "storage-unwritable": "storage-unwritable",
        }[mode]
        if expected_failure not in coverage["failures"]:
            raise RuntimeError(f"{mode}: capture failure was not visible; root={root}")
        return {
            "mode": mode,
            "status": "passed",
            "durationSeconds": round(time.monotonic() - started, 3),
            "evidenceRoot": str(root),
            "failure": expected_failure,
        }
    if mode == "both-killed":
        recovered = run_mode(executable, "normal", destination, root)
        records = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in (root / "native-crashes" / "incidents").glob("*.json")
        ]
        if not any(i["kind"] == "unexpected-end-unknown" for i in records):
            raise RuntimeError(
                "both-killed: restart did not classify missing observer evidence as unknown"
            )
        return {**recovered, "mode": mode, "durationSeconds": round(time.monotonic() - started, 3)}
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        sessions = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in (root / "native-crashes" / "sessions").glob("*.json")
        ]
        if sessions and all(s.get("completed") for s in sessions):
            break
        time.sleep(0.1)
    if not sessions or not all(s.get("completed") for s in sessions):
        raise RuntimeError(f"{mode}: missing completed watcher evidence; root={root}")
    expected_capture_status = "degraded" if mode == "watcher-delay" else "ready"
    if any(s.get("captureStatus") != expected_capture_status for s in sessions):
        raise RuntimeError(f"{mode}: capture was not ready; root={root}")
    incidents = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (root / "native-crashes" / "incidents").glob("*.json")
    ]
    dumps = []
    for incident in incidents:
        if incident["dump"]["status"] == "captured":
            path = root / "native-crashes" / "dumps" / f"{incident['incidentId']}.dmp"
            dumps.append(
                {"path": str(path), "incidentId": incident["incidentId"], **inspect_dump(path)}
            )
    exits = [i for i in incidents if i["kind"] == "process-exit"]
    if len(exits) != 1:
        raise RuntimeError(f"{mode}: expected one process exit record")
    if mode in {
        "normal",
        "panic-caught",
        "worker-timeout",
        "worker-failure",
        "watcher-delay",
        "service-first",
        "shell-first",
    }:
        if exit_code != 0 or exits[0]["orderlyReason"] != "normal-return":
            raise RuntimeError(f"{mode}: orderly exit was misclassified")
    elif exits[0]["orderlyReason"] is not None:
        raise RuntimeError(f"{mode}: abnormal exit was marked orderly")
    expected = {"ud2": "0xc000001d", "av": "0xc0000005", "stack-overflow": "0xc00000fd"}.get(mode)
    if expected and not any(d["exceptionCode"] == expected for d in dumps):
        raise RuntimeError(f"{mode}: missing expected exception dump {expected}; root={root}")
    if mode.startswith("panic") and not any(
        i["kind"] == "rust-panic" and i["dump"]["status"] == "captured" for i in incidents
    ):
        raise RuntimeError(f"{mode}: missing panic dump; root={root}")
    if mode in {"normal", "fastfail", "forcekill"} and dumps:
        raise RuntimeError(f"{mode}: unexpected synthetic crash dump")
    if mode in {"worker-timeout", "worker-failure"}:
        expected_error = "dump-timeout" if mode == "worker-timeout" else "dump-write-failed"
        if not any(i["dump"].get("errorCode") == expected_error for i in incidents):
            raise RuntimeError(f"{mode}: failed worker did not leave the expected summary")
        if list((root / "native-crashes" / "dumps").glob("*.partial")):
            raise RuntimeError(f"{mode}: partial dump was retained")
        coverage = json.loads((root / "fixture-coverage.json").read_text(encoding="utf-8"))
        if expected_error not in coverage["failures"]:
            raise RuntimeError(f"{mode}: failed dump was not propagated to export failures")
    if mode == "watcher-delay":
        early = json.loads((root / "fixture-early-coverage.json").read_text(encoding="utf-8"))
        observation = json.loads((root / "handoff-observation.json").read_text(encoding="utf-8"))
        if early["captureStatus"] != "unavailable" or "handoff-timeout" not in early["failures"]:
            raise RuntimeError(
                "watcher-delay: handoff timeout was invisible while watcher remained alive"
            )
        if any(s["captureStatus"] != "degraded" for s in early["sessions"]):
            raise RuntimeError("watcher-delay: stale ready status survived live-health projection")
        if observation["firstMillis"] < 7900 or observation["secondMillis"] >= 1000:
            raise RuntimeError(
                "watcher-delay: initial deadline or subsequent disabled-capture bound failed"
            )
        if observation["beforeSequence"] != 1 or observation["afterSequence"] != 1:
            raise RuntimeError("watcher-delay: timed-out payload was reused by a later panic")
        events = [i for i in incidents if i["kind"] == "rust-panic"]
        if len(events) != 1 or events[0]["dump"]["errorCode"] != "capture-context-expired":
            raise RuntimeError(
                "watcher-delay: late observer did not reject expired exception context"
            )
        if dumps or list((root / "native-crashes" / "dumps").glob("*")):
            raise RuntimeError("watcher-delay: late or partial dump remained publishable")
        if "handoff-timeout" not in sessions[0]["failures"]:
            raise RuntimeError(
                "watcher-delay: resumed observer did not persist capture degradation"
            )
    if mode in {"service-first", "shell-first"}:
        services = exits[0]["services"]
        if len(services) != 1 or services[0]["order"] != mode or services[0]["exitCode"] != 7:
            raise RuntimeError(f"{mode}: kernel process exit ordering was not preserved")
    result = {
        "mode": mode,
        "status": "passed",
        "exitCode": exit_code,
        "durationSeconds": round(time.monotonic() - started, 3),
        "evidenceRoot": str(root),
        "sessionIds": [s["sessionId"] for s in sessions],
        "dumps": dumps,
    }
    (destination / f"{mode}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exe",
        type=Path,
        required=True,
        help="Previously built debug desktop EXE; release binaries reject fixture mode",
    )
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("native crash smoke is Windows-only; no desktop process will be launched")
    executable = args.exe.resolve(strict=True)
    report_root = Path(tempfile.mkdtemp(prefix="af-native-smoke-report-"))
    results = []
    for mode in args.modes:
        try:
            result = run_mode(executable, mode, report_root)
            results.append(result)
            print(
                json.dumps(
                    {"mode": mode, "status": "passed", "durationSeconds": result["durationSeconds"]}
                )
            )
        except (OSError, RuntimeError, ValueError) as error:
            results.append({"mode": mode, "status": "failed", "error": str(error)})
            print(json.dumps(results[-1]))
            break
    passed = len(results) == len(args.modes) and all(r["status"] == "passed" for r in results)
    report = report_root / "report.json"
    report.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "executable": str(executable),
                "passed": passed,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"report": str(report), "passed": passed}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
