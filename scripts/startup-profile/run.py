"""Startup evidence: local measurement and explicit isolated Windows installation acceptance."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from startup_evidence import ROOT, digest, now, report_header, source_identity
from startup_journal import analyze
from startup_verify import measure


def build_helper(target: Path, report: dict[str, Any]) -> None:
    target = target.expanduser().resolve()
    if target == ROOT or target.is_relative_to(ROOT):
        raise ValueError("Cargo target must be outside this source worktree")
    source_before = source_identity()
    env = os.environ.copy()
    # The probe is not a packaged desktop shell and must not inherit an install identity.
    for key in (
        "AGENT_FRIEND_BUILD_IDENTITY_JSON",
        "AGENT_FRIEND_INSTALL_PROFILE_JSON",
        "AGENT_FRIEND_PRODUCT_VERSION",
    ):
        env.pop(key, None)
    subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "--locked",
            "--manifest-path",
            str(ROOT / "frontend/src-tauri/Cargo.toml"),
            "--bin",
            "agent-friend-installer-helper",
            "--target-dir",
            str(target),
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )
    helper = (
        target
        / "release"
        / (
            "agent-friend-installer-helper.exe"
            if os.name == "nt"
            else "agent-friend-installer-helper"
        )
    )
    source_after = source_identity()
    report.update(
        {
            "ok": source_before == source_after,
            "helperPath": str(helper),
            "helperSha256": digest(helper),
            "helperSource": source_after,
            "sourceUnchangedDuringBuild": source_before == source_after,
            "limitations": [
                "release helper only; not a packaged product",
                "source hashes bind measurement inputs, not package identity",
            ],
        }
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-helper", help="compile only the release measurement helper")
    build.add_argument("--target-dir", required=True, type=Path)
    verify = sub.add_parser("verify", help="read-only payload verification; never install/restart")
    verify.add_argument("--helper", required=True, type=Path)
    verify.add_argument("--helper-build-report", type=Path)
    verify.add_argument("--install-root", required=True, type=Path)
    verify.add_argument("--repeats", default=5, type=int)
    verify.add_argument("--timeout", default=180.0, type=float)
    report = sub.add_parser("report", help="project one process session from existing runtime logs")
    report.add_argument("--log", action="append", required=True, type=Path)
    report.add_argument("--process-session-id", required=True)
    installed = sub.add_parser(
        "installed-evidence",
        help="Windows isolated installer-smoke startup, interruption and diagnostic export",
    )
    installed.add_argument("--installer", required=True, type=Path)
    installed.add_argument("--artifacts", required=True, type=Path)
    for command in (build, verify, report, installed):
        command.add_argument(
            "--output", required=True, type=Path, help="new JSON file; no overwrite"
        )
    return result


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    if args.command == "verify" and (not 1 <= args.repeats <= 10 or not 0 < args.timeout <= 300):
        cli.error("repeats must be 1..10 and timeout must be >0 and <=300 seconds")
    # Reserve before any build or payload read; refuse to destroy existing evidence.
    with args.output.open("x", encoding="utf-8", newline="\n") as destination:
        report: dict[str, Any] = {"schemaVersion": 1, "ok": False}
        try:
            report.update(report_header(args.command))
            if args.command == "verify":
                measure(
                    args.helper,
                    args.install_root,
                    args.repeats,
                    args.timeout,
                    report,
                    args.helper_build_report,
                )
            elif args.command == "report":
                report.update(analyze(args.log, args.process_session_id))
            elif args.command == "installed-evidence":
                from installed_evidence import collect

                collect(args.installer, args.artifacts, report)
            else:
                build_helper(args.target_dir, report)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
            RuntimeError,
            StopIteration,
        ) as error:
            report.update({"ok": False, "failureType": type(error).__name__})
            print(f"startup-profile failed: {error}")
        except KeyboardInterrupt:
            report.update({"ok": False, "failureType": "interrupted"})
        finally:
            report["completedAt"] = now()
            json.dump(report, destination, ensure_ascii=False, indent=2, allow_nan=False)
            destination.write("\n")
    print(json.dumps({"ok": report["ok"], "report": str(args.output)}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
