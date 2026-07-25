#!/usr/bin/env python3
"""Safely summarize an agent-friend diagnostic ZIP without extracting it."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any


MAX_JSON_BYTES = 64 * 1024 * 1024
CORE_ENTRIES = (
    "manifest.json",
    "diagnostics/application.json",
    "diagnostics/environment.json",
    "diagnostics/runtime-services.json",
    "diagnostics/runtime-config.json",
)


class BundleError(Exception):
    """Raised when the archive cannot be safely interpreted."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and summarize an agent-friend diagnostic ZIP in place."
    )
    parser.add_argument("bundle", type=Path, help="Path to agent-friend-diagnostics-*.zip")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument(
        "--skip-crc",
        action="store_true",
        help="Skip reading every entry for CRC verification",
    )
    return parser.parse_args()


def safe_entry_name(name: str) -> bool:
    if not name or "\\" in name or name.startswith("/"):
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and all(part not in ("", ".", "..") for part in path.parts)


def read_json(archive: zipfile.ZipFile, name: str) -> Any:
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise BundleError(f"missing required entry: {name}") from error
    if info.file_size > MAX_JSON_BYTES:
        raise BundleError(f"JSON entry exceeds {MAX_JSON_BYTES} bytes: {name}")
    try:
        with archive.open(info, "r") as stream:
            return json.load(stream)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BundleError(f"invalid JSON entry {name}: {error}") from error


def optional_json(archive: zipfile.ZipFile, name: str, warnings: list[str]) -> Any | None:
    if name not in archive.namelist():
        warnings.append(f"missing optional/additive entry: {name}")
        return None
    try:
        return read_json(archive, name)
    except BundleError as error:
        warnings.append(str(error))
        return None


def summarize_session(value: Any, entry: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"entry": entry, "error": "session root is not an object"}
    events = value.get("events")
    if not isinstance(events, list):
        return {"entry": entry, "error": "events is not an array"}

    event_types: Counter[str] = Counter()
    run_ids: set[str] = set()
    attachment_references = 0
    malformed_events = 0
    for event in events:
        if not isinstance(event, dict):
            malformed_events += 1
            continue
        event_types[str(event.get("type", "<missing>"))] += 1
        meta = event.get("meta")
        if isinstance(meta, dict) and isinstance(meta.get("run_id"), str):
            run_ids.add(meta["run_id"])
        payload = event.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("attachments"), list):
            attachment_references += len(payload["attachments"])

    return {
        "entry": entry,
        "sessionId": value.get("session_id"),
        "eventCount": len(events),
        "eventTypes": dict(sorted(event_types.items())),
        "runIdCount": len(run_ids),
        "attachmentReferenceCount": attachment_references,
        "malformedEventCount": malformed_events,
        "activeEventsPresent": "active_events" in value,
    }


def inspect_bundle(path: Path, verify_crc: bool) -> dict[str, Any]:
    if not path.is_file():
        raise BundleError(f"bundle does not exist or is not a file: {path}")

    warnings: list[str] = []
    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as error:
        raise BundleError(f"cannot open ZIP: {error}") from error

    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        duplicate_names = sorted(name for name, count in Counter(names).items() if count > 1)
        unsafe_names = sorted(name for name in names if not safe_entry_name(name))
        if duplicate_names:
            raise BundleError(f"duplicate ZIP entries: {', '.join(duplicate_names)}")
        if unsafe_names:
            raise BundleError(f"unsafe ZIP entry names: {', '.join(unsafe_names)}")

        bad_crc_entry = None
        if verify_crc:
            try:
                bad_crc_entry = archive.testzip()
            except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                raise BundleError(f"CRC verification failed: {error}") from error
            if bad_crc_entry is not None:
                raise BundleError(f"CRC mismatch in entry: {bad_crc_entry}")

        manifest = read_json(archive, "manifest.json")
        if not isinstance(manifest, dict):
            raise BundleError("manifest.json root is not an object")
        format_version = manifest.get("formatVersion")
        if format_version != 1:
            warnings.append(f"unknown diagnostic formatVersion: {format_version!r}")

        application = optional_json(archive, "diagnostics/application.json", warnings)
        environment = optional_json(archive, "diagnostics/environment.json", warnings)
        runtime_services = optional_json(
            archive, "diagnostics/runtime-services.json", warnings
        )
        runtime_config = optional_json(archive, "diagnostics/runtime-config.json", warnings)
        attachments = optional_json(archive, "diagnostics/attachments.json", warnings)

        sessions: list[dict[str, Any]] = []
        for name in sorted(name for name in names if name.startswith("sessions/") and name.endswith(".json")):
            try:
                sessions.append(summarize_session(read_json(archive, name), name))
            except BundleError as error:
                sessions.append({"entry": name, "error": str(error)})

        attachment_integrity = None
        attachment_statuses: dict[str, int] = {}
        if isinstance(attachments, dict) and isinstance(attachments.get("items"), list):
            items = [item for item in attachments["items"] if isinstance(item, dict)]
            integrity_fields = ("metadataMatches", "blobExists", "sizeMatches", "hashMatches")
            attachment_integrity = {
                "checkedItems": len(items),
                "failedItems": sum(
                    1 for item in items if not all(item.get(field) is True for field in integrity_fields)
                ),
            }
            attachment_statuses = dict(
                sorted(Counter(str(item.get("originalStatus", "<missing>")) for item in items).items())
            )

        app = application if isinstance(application, dict) else {}
        package_build = app.get("packageBuild") if isinstance(app.get("packageBuild"), dict) else {}
        source = package_build.get("source") if isinstance(package_build.get("source"), dict) else {}
        config_items = (
            runtime_config.get("items", []) if isinstance(runtime_config, dict) else []
        )
        configured_count = sum(
            1 for item in config_items if isinstance(item, dict) and item.get("configured") is True
        )
        config_error_count = sum(
            1 for item in config_items if isinstance(item, dict) and item.get("error") not in (None, "")
        )

        logs = manifest.get("logs") if isinstance(manifest.get("logs"), list) else []
        log_entries = []
        for item in logs:
            if not isinstance(item, dict):
                continue
            log_entries.append(
                {
                    "source": item.get("source"),
                    "entry": item.get("entry"),
                    "modifiedAtUnixSeconds": item.get("modifiedAtUnixSeconds"),
                    "originalBytes": item.get("originalBytes"),
                    "tailStartOffset": item.get("tailStartOffset"),
                    "includedBytes": item.get("includedBytes"),
                }
            )

        missing_core = [name for name in CORE_ENTRIES if name not in names]
        if missing_core:
            warnings.append(f"missing known v1 entries: {', '.join(missing_core)}")

        return {
            "bundle": str(path.resolve()),
            "archive": {
                "entryCount": len(infos),
                "uncompressedBytes": sum(info.file_size for info in infos),
                "compressedBytes": sum(info.compress_size for info in infos),
                "duplicateEntries": duplicate_names,
                "unsafeEntries": unsafe_names,
                "crcVerified": verify_crc,
                "crcOk": bad_crc_entry is None if verify_crc else None,
            },
            "manifest": {
                "formatVersion": format_version,
                "generatedAt": manifest.get("generatedAt"),
                "outcome": manifest.get("outcome"),
                "failures": manifest.get("failures", []),
                "requestedSessionIds": manifest.get("requestedSessionIds", []),
                "includedSessionIds": manifest.get("includedSessionIds", []),
                "excluded": manifest.get("excluded", []),
                "logLimits": manifest.get("logLimits"),
                "attachmentSummary": manifest.get("attachments"),
            },
            "runtimeIdentity": {
                "product": app.get("product"),
                "version": app.get("version"),
                "buildMode": app.get("buildMode"),
                "distribution": package_build.get("distribution"),
                "packageBuildMode": package_build.get("buildMode"),
                "buildId": package_build.get("buildId"),
                "commit": source.get("commit"),
                "dirty": source.get("dirty"),
                "environment": environment,
            },
            "runtimeServices": runtime_services,
            "runtimeConfig": {
                "itemCount": len(config_items),
                "configuredCount": configured_count,
                "errorCount": config_error_count,
            },
            "problem": {
                "present": "problem.txt" in names,
                "bytes": archive.getinfo("problem.txt").file_size if "problem.txt" in names else 0,
            },
            "sessions": sessions,
            "logs": log_entries,
            "attachments": {
                "summary": attachments.get("summary") if isinstance(attachments, dict) else None,
                "statuses": attachment_statuses,
                "integrity": attachment_integrity,
                "originalEntryCount": sum(1 for name in names if name.startswith("attachments/")),
            },
            "warnings": warnings,
        }


def print_human(summary: dict[str, Any]) -> None:
    archive = summary["archive"]
    manifest = summary["manifest"]
    identity = summary["runtimeIdentity"]
    print(f"Bundle: {summary['bundle']}")
    print(
        "Archive: "
        f"{archive['entryCount']} entries, CRC "
        f"{'ok' if archive['crcOk'] else 'not checked'}, "
        f"{archive['uncompressedBytes']} uncompressed bytes"
    )
    print(
        f"Manifest: format={manifest['formatVersion']} outcome={manifest['outcome']} "
        f"generatedAt={manifest['generatedAt']} failures={len(manifest['failures'])}"
    )
    print(
        f"Runtime: {identity['product']} {identity['version']} "
        f"distribution={identity['distribution']} packageBuildMode={identity['packageBuildMode']}"
    )
    print(f"Build: commit={identity['commit']} dirty={identity['dirty']} id={identity['buildId']}")
    print(
        f"Sessions: requested={len(manifest['requestedSessionIds'])} "
        f"included={len(manifest['includedSessionIds'])} files={len(summary['sessions'])}"
    )
    for session in summary["sessions"]:
        if "error" in session:
            print(f"  - {session['entry']}: ERROR {session['error']}")
        else:
            print(
                f"  - {session['entry']}: events={session['eventCount']} "
                f"runs={session['runIdCount']} attachmentRefs={session['attachmentReferenceCount']} "
                f"types={json.dumps(session['eventTypes'], ensure_ascii=False, sort_keys=True)}"
            )
    print(f"Logs: {len(summary['logs'])}")
    for log in summary["logs"]:
        print(
            f"  - {log['source']}: original={log['originalBytes']} "
            f"tailStart={log['tailStartOffset']} included={log['includedBytes']}"
        )
    attachments = summary["attachments"]
    print(
        f"Attachments: originals={attachments['originalEntryCount']} "
        f"statuses={json.dumps(attachments['statuses'], ensure_ascii=False, sort_keys=True)} "
        f"integrity={json.dumps(attachments['integrity'], ensure_ascii=False, sort_keys=True)}"
    )
    print(
        f"Problem text: {'present' if summary['problem']['present'] else 'absent'} "
        f"({summary['problem']['bytes']} bytes; content omitted)"
    )
    for warning in summary["warnings"]:
        print(f"WARNING: {warning}")


def main() -> int:
    args = parse_args()
    try:
        summary = inspect_bundle(args.bundle, verify_crc=not args.skip_crc)
    except BundleError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(summary, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        print()
    else:
        print_human(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
