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
MAX_JSONL_LINE_BYTES = 4 * 1024 * 1024
CORE_ENTRIES = (
    "manifest.json",
    "diagnostics/application.json",
    "diagnostics/environment.json",
    "diagnostics/runtime-services.json",
    "diagnostics/runtime-config.json",
)
CAPABILITY_HANDLERS = {
    "selected-sessions-v1": "sessions",
    "model-catalog-v1": "modelCatalog",
    "bounded-log-tail-v1": "logs",
    "attachment-integrity-v1": "attachments",
    "windows-shell-history-v1": "windowsShell",
    "webview2-process-health-v1": "webView2",
    "webview2-render-evidence-v1": "webView2RenderEvidence",
    "install-integrity-v1": "installIntegrity",
}


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


def summarize_model_catalog(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return {"error": "model catalog root is not an object"}
    providers = value.get("providers")
    provider_items = (
        [provider for provider in providers if isinstance(provider, dict)]
        if isinstance(providers, list)
        else []
    )
    return {
        "schemaVersion": value.get("schemaVersion"),
        "catalogRevision": value.get("catalogRevision"),
        "providerCount": len(provider_items),
        "modelCount": sum(
            len(provider.get("models", []))
            for provider in provider_items
            if isinstance(provider.get("models"), list)
        ),
    }


def summarize_webview2(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return {"error": "WebView2 diagnostics root is not an object"}
    runtime = value.get("runtime") if isinstance(value.get("runtime"), dict) else {}
    graphics = value.get("graphics") if isinstance(value.get("graphics"), dict) else {}
    events = value.get("events") if isinstance(value.get("events"), list) else []
    event_items = [event for event in events if isinstance(event, dict)]
    registration_failures = (
        value.get("registrationFailures")
        if isinstance(value.get("registrationFailures"), list)
        else []
    )
    failure_reports = (
        [report for report in value.get("failureReports", []) if isinstance(report, dict)]
        if isinstance(value.get("failureReports"), list)
        else []
    )
    registered_windows = (
        [label for label in value.get("registeredWindows", []) if isinstance(label, str)]
        if isinstance(value.get("registeredWindows"), list)
        else []
    )
    return {
        "schemaVersion": value.get("schemaVersion"),
        "supported": value.get("supported"),
        "runtimeVersion": runtime.get("browserVersion"),
        "failureReportFolderAvailable": bool(runtime.get("failureReportFolderPath")),
        "failureReportCount": len(failure_reports),
        "newestFailureReportUnixSeconds": max(
            (
                report["modifiedAtUnixSeconds"]
                for report in failure_reports
                if isinstance(report.get("modifiedAtUnixSeconds"), int)
            ),
            default=None,
        ),
        "failureReportScanError": value.get("failureReportScanError"),
        "environmentRegistrationWindow": runtime.get("environmentRegistrationWindow"),
        "graphicsAdapterCount": len(graphics.get("adapters", []))
        if isinstance(graphics.get("adapters"), list)
        else 0,
        "dwmCompositionEnabled": graphics.get("dwmCompositionEnabled"),
        "remoteSession": graphics.get("remoteSession"),
        "registeredWindows": registered_windows,
        "registrationFailureCount": len(registration_failures),
        "eventBufferLimit": value.get("eventBufferLimit"),
        "droppedEventCount": value.get("droppedEventCount"),
        "eventCount": len(event_items),
        "eventKinds": dict(
            sorted(
                Counter(str(event.get("eventType", "<missing>")) for event in event_items).items()
            )
        ),
        "processFailureKinds": dict(
            sorted(
                Counter(
                    str(event["processFailedKind"])
                    for event in event_items
                    if isinstance(event.get("processFailedKind"), str)
                ).items()
            )
        ),
        "processFailureReasons": dict(
            sorted(
                Counter(
                    str(event["reason"])
                    for event in event_items
                    if isinstance(event.get("reason"), str)
                ).items()
            )
        ),
    }


def summarize_install_integrity(value: Any, *, collected: bool) -> dict[str, Any]:
    if value is None:
        return {
            "status": "unavailable",
            "evidence": "missing" if collected else "not-collected",
        }
    if not isinstance(value, dict):
        return {
            "status": "unavailable",
            "evidence": "invalid",
            "error": "install integrity root is not an object",
        }
    shell = value.get("shell") if isinstance(value.get("shell"), dict) else {}
    active = value.get("activeInstall") if isinstance(value.get("activeInstall"), dict) else {}
    payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
    runtime = value.get("runtime") if isinstance(value.get("runtime"), dict) else {}
    residue = value.get("residue") if isinstance(value.get("residue"), dict) else {}
    job = runtime.get("jobObject") if isinstance(runtime.get("jobObject"), dict) else {}
    observed = (
        runtime.get("observedReadiness")
        if isinstance(runtime.get("observedReadiness"), dict)
        else {}
    )
    return {
        "schemaVersion": value.get("schemaVersion"),
        "status": value.get("status"),
        "errorCode": value.get("errorCode"),
        "evidence": "collected",
        "shell": {
            "available": shell.get("available"),
            "buildId": shell.get("buildId"),
            "profileId": shell.get("profileId"),
            "buildIdentitySha256": shell.get("buildIdentitySha256"),
        },
        "activeInstall": {
            "present": active.get("present"),
            "parseable": active.get("parseable"),
            "buildId": active.get("buildId"),
            "profileId": active.get("profileId"),
            "buildIdentitySha256": active.get("buildIdentitySha256"),
            "payloadManifestSha256": active.get("payloadManifestSha256"),
            "payloadRelativePath": active.get("payloadRelativePath"),
        },
        "payload": {
            "validated": payload.get("validated"),
            "buildId": payload.get("buildId"),
            "profileId": payload.get("profileId"),
            "buildIdentitySha256": payload.get("buildIdentitySha256"),
            "payloadManifestSha256": payload.get("payloadManifestSha256"),
            "runtimeExecutableSha256": payload.get("runtimeExecutableSha256"),
        },
        "runtime": {
            "observedReadiness": observed,
            "jobObjectCreated": job.get("created"),
            "jobObjectAssignedCount": job.get("assignedCount"),
        },
        "residue": {
            "legacyFixedRuntimePresent": residue.get("legacyFixedRuntimePresent"),
            "incompleteTransactionCount": residue.get("incompleteTransactionCount"),
        },
    }


def summarize_webview2_render_evidence(
    value: Any,
    archive_names: set[str],
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return {"error": "WebView2 render evidence root is not an object"}
    status = value.get("status") if isinstance(value.get("status"), dict) else {}
    pending = status.get("pending") if isinstance(status.get("pending"), dict) else None
    capture = value.get("capture") if isinstance(value.get("capture"), dict) else None
    included_artifacts = (
        [entry for entry in value.get("includedArtifacts", []) if isinstance(entry, str)]
        if isinstance(value.get("includedArtifacts"), list)
        else []
    )
    missing_artifacts = [entry for entry in included_artifacts if entry not in archive_names]

    def image_summary(image: Any) -> dict[str, Any] | None:
        if not isinstance(image, dict):
            return None
        metrics = image.get("pixelMetrics") if isinstance(image.get("pixelMetrics"), dict) else {}
        return {
            "width": image.get("width"),
            "height": image.get("height"),
            "bytes": image.get("bytes"),
            "sha256": image.get("sha256"),
            "captureStartedUnixMs": image.get("captureStartedUnixMs"),
            "captureCompletedUnixMs": image.get("captureCompletedUnixMs"),
            "transparentPixelCount": metrics.get("transparentPixelCount"),
            "opaquePixelCount": metrics.get("opaquePixelCount"),
            "nearBlackOpaqueRatio": metrics.get("nearBlackOpaqueRatio"),
            "averageOpaqueLuma": metrics.get("averageOpaqueLuma"),
        }

    errors = capture.get("errors") if capture and isinstance(capture.get("errors"), list) else []
    graphics = (
        capture.get("graphics") if capture and isinstance(capture.get("graphics"), dict) else {}
    )
    return {
        "schemaVersion": value.get("schemaVersion"),
        "supported": value.get("supported"),
        "includeRequested": value.get("includeRequested"),
        "armed": status.get("armed"),
        "captureInProgress": status.get("captureInProgress"),
        "pending": pending,
        "captureId": capture.get("captureId") if capture else None,
        "capturedAt": capture.get("captureCompletedAt") if capture else None,
        "trigger": capture.get("trigger") if capture else None,
        "hiddenDurationMs": capture.get("hiddenDurationMs") if capture else None,
        "startDeltaMs": capture.get("startDeltaMs") if capture else None,
        "webView2Image": image_summary(capture.get("webview2")) if capture else None,
        "desktopImage": image_summary(capture.get("desktop")) if capture else None,
        "graphicsAdapterCount": len(graphics.get("adapters", []))
        if isinstance(graphics.get("adapters"), list)
        else 0,
        "dwmCompositionEnabled": graphics.get("dwmCompositionEnabled"),
        "remoteSession": graphics.get("remoteSession"),
        "captureErrorCount": len(errors),
        "includedArtifacts": included_artifacts,
        "missingArtifacts": missing_artifacts,
    }


def summarize_shell_snapshot(value: Any, entry: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"entry": entry, "error": "shell snapshot root is not an object"}
    windows = value.get("windows")
    window_items = (
        [window for window in windows if isinstance(window, dict)]
        if isinstance(windows, list)
        else []
    )
    role_counts: Counter[str] = Counter()
    for window in window_items:
        roles = window.get("roles")
        if isinstance(roles, list):
            role_counts.update(str(role) for role in roles if isinstance(role, str))
    probe_errors = value.get("probeErrors")
    return {
        "entry": entry,
        "schemaVersion": value.get("schemaVersion"),
        "capturedAt": value.get("capturedAt"),
        "capability": value.get("capability"),
        "windowCount": len(window_items),
        "roleCounts": dict(sorted(role_counts.items())),
        "fullscreenCandidateCount": sum(
            1 for window in window_items if window.get("fullscreenGeometryCandidate") is True
        ),
        "taskbarAutoHide": value.get("taskbarAutoHide"),
        "taskbarAlwaysOnTop": value.get("taskbarAlwaysOnTop"),
        "taskbarAboveForegroundFullscreen": value.get("taskbarAboveForegroundFullscreen"),
        "probeErrorCount": len(probe_errors) if isinstance(probe_errors, list) else 0,
    }


def summarize_shell_journals(
    archive: zipfile.ZipFile,
    entries: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    record_count = 0
    malformed_record_count = 0
    event_count = 0
    event_kinds: Counter[str] = Counter()
    dropped_event_count = 0
    truncated_event_count = 0
    process_sessions: set[str] = set()
    recorded_times: list[str] = []
    snapshots_with_taskbar_above_fullscreen = 0
    snapshot_probe_error_count = 0

    for entry in entries:
        info = archive.getinfo(entry)
        if info.file_size > MAX_JSON_BYTES:
            warnings.append(f"shell journal entry exceeds {MAX_JSON_BYTES} bytes: {entry}")
            continue
        with archive.open(info, "r") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                if not raw_line.strip():
                    continue
                if len(raw_line) > MAX_JSONL_LINE_BYTES:
                    malformed_record_count += 1
                    warnings.append(
                        f"shell journal line exceeds {MAX_JSONL_LINE_BYTES} bytes: "
                        f"{entry}:{line_number}"
                    )
                    continue
                try:
                    record = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    malformed_record_count += 1
                    warnings.append(f"invalid shell journal record {entry}:{line_number}: {error}")
                    continue
                if not isinstance(record, dict):
                    malformed_record_count += 1
                    continue

                record_count += 1
                events = record.get("events")
                if isinstance(events, list):
                    event_items = [event for event in events if isinstance(event, dict)]
                    event_count += len(event_items)
                    event_kinds.update(str(event.get("kind", "<missing>")) for event in event_items)
                dropped_event_count += (
                    record.get("droppedEventCount")
                    if isinstance(record.get("droppedEventCount"), int)
                    else 0
                )
                truncated_event_count += (
                    record.get("truncatedEventCount")
                    if isinstance(record.get("truncatedEventCount"), int)
                    else 0
                )
                process_session_id = record.get("processSessionId")
                if isinstance(process_session_id, str):
                    process_sessions.add(process_session_id)
                recorded_at = record.get("recordedAt")
                if isinstance(recorded_at, str):
                    recorded_times.append(recorded_at)
                snapshot = record.get("snapshot")
                if isinstance(snapshot, dict):
                    if snapshot.get("taskbarAboveForegroundFullscreen") is True:
                        snapshots_with_taskbar_above_fullscreen += 1
                    probe_errors = snapshot.get("probeErrors")
                    if isinstance(probe_errors, list):
                        snapshot_probe_error_count += len(probe_errors)

    return {
        "segmentCount": len(entries),
        "recordCount": record_count,
        "malformedRecordCount": malformed_record_count,
        "eventCount": event_count,
        "eventKinds": dict(sorted(event_kinds.items())),
        "droppedEventCount": dropped_event_count,
        "truncatedEventCount": truncated_event_count,
        "processSessionCount": len(process_sessions),
        "firstRecordedAt": min(recorded_times) if recorded_times else None,
        "lastRecordedAt": max(recorded_times) if recorded_times else None,
        "snapshotsWithTaskbarAboveFullscreen": snapshots_with_taskbar_above_fullscreen,
        "snapshotProbeErrorCount": snapshot_probe_error_count,
    }


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
        schema_revision = manifest.get("schemaRevision")
        raw_capabilities = manifest.get("capabilities")
        capabilities = (
            [capability for capability in raw_capabilities if isinstance(capability, str)]
            if isinstance(raw_capabilities, list)
            else []
        )
        if raw_capabilities is not None and not isinstance(raw_capabilities, list):
            warnings.append("manifest capabilities is not an array")
        elif isinstance(raw_capabilities, list) and len(capabilities) != len(raw_capabilities):
            warnings.append("manifest capabilities contains non-string values")
        unhandled_capabilities = sorted(set(capabilities).difference(CAPABILITY_HANDLERS))
        if unhandled_capabilities:
            warnings.append(
                "unhandled diagnostic capabilities: " + ", ".join(unhandled_capabilities)
            )

        application = optional_json(archive, "diagnostics/application.json", warnings)
        environment = optional_json(archive, "diagnostics/environment.json", warnings)
        runtime_services = optional_json(archive, "diagnostics/runtime-services.json", warnings)
        runtime_config = optional_json(archive, "diagnostics/runtime-config.json", warnings)
        model_catalog = optional_json(archive, "diagnostics/model-catalog.json", warnings)
        attachments = optional_json(archive, "diagnostics/attachments.json", warnings)
        webview2 = (
            optional_json(archive, "diagnostics/webview2.json", warnings)
            if "webview2-process-health-v1" in capabilities or "diagnostics/webview2.json" in names
            else None
        )
        webview2_render_evidence = (
            optional_json(
                archive,
                "diagnostics/webview2-render-evidence.json",
                warnings,
            )
            if "webview2-render-evidence-v1" in capabilities
            or "diagnostics/webview2-render-evidence.json" in names
            else None
        )
        install_integrity_collected = (
            "install-integrity-v1" in capabilities or "install/integrity.json" in names
        )
        install_integrity = (
            optional_json(archive, "install/integrity.json", warnings)
            if install_integrity_collected
            else None
        )
        render_evidence_summary = summarize_webview2_render_evidence(
            webview2_render_evidence,
            set(names),
        )
        if render_evidence_summary and render_evidence_summary.get("missingArtifacts"):
            warnings.append(
                "render evidence metadata references missing artifacts: "
                + ", ".join(render_evidence_summary["missingArtifacts"])
            )

        shell_manifest = (
            manifest.get("windowsShell") if isinstance(manifest.get("windowsShell"), dict) else {}
        )
        shell_entries = sorted(
            name for name in names if name.startswith("diagnostics/windows-shell/")
        )
        referenced_shell_entries = (
            [entry for entry in shell_manifest.get("artifacts", []) if isinstance(entry, str)]
            if isinstance(shell_manifest.get("artifacts"), list)
            else []
        )
        missing_shell_entries = sorted(set(referenced_shell_entries).difference(names))
        unlisted_shell_entries = sorted(set(shell_entries).difference(referenced_shell_entries))
        if missing_shell_entries:
            warnings.append(
                "manifest references missing Windows Shell artifacts: "
                + ", ".join(missing_shell_entries)
            )
        if shell_manifest and unlisted_shell_entries:
            warnings.append(
                "Windows Shell artifacts are absent from manifest summary: "
                + ", ".join(unlisted_shell_entries)
            )
        shell_current = None
        shell_current_entry = "diagnostics/windows-shell/current.json"
        if shell_current_entry in names:
            try:
                shell_current = summarize_shell_snapshot(
                    read_json(archive, shell_current_entry), shell_current_entry
                )
            except BundleError as error:
                warnings.append(str(error))
        shell_journal_entries = [
            name
            for name in shell_entries
            if name.startswith("diagnostics/windows-shell/events-") and name.endswith(".jsonl")
        ]
        shell_journal = summarize_shell_journals(archive, shell_journal_entries, warnings)

        sessions: list[dict[str, Any]] = []
        for name in sorted(
            name for name in names if name.startswith("sessions/") and name.endswith(".json")
        ):
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
                    1
                    for item in items
                    if not all(item.get(field) is True for field in integrity_fields)
                ),
            }
            attachment_statuses = dict(
                sorted(
                    Counter(str(item.get("originalStatus", "<missing>")) for item in items).items()
                )
            )

        app = application if isinstance(application, dict) else {}
        package_build = app.get("packageBuild") if isinstance(app.get("packageBuild"), dict) else {}
        source = (
            package_build.get("source") if isinstance(package_build.get("source"), dict) else {}
        )
        config_items = runtime_config.get("items", []) if isinstance(runtime_config, dict) else []
        configured_count = sum(
            1 for item in config_items if isinstance(item, dict) and item.get("configured") is True
        )
        config_error_count = sum(
            1
            for item in config_items
            if isinstance(item, dict) and item.get("error") not in (None, "")
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
                "schemaRevision": schema_revision,
                "capabilities": capabilities,
                "unhandledCapabilities": unhandled_capabilities,
                "generatedAt": manifest.get("generatedAt"),
                "outcome": manifest.get("outcome"),
                "failures": manifest.get("failures", []),
                "requestedSessionIds": manifest.get("requestedSessionIds", []),
                "includedSessionIds": manifest.get("includedSessionIds", []),
                "excluded": manifest.get("excluded", []),
                "logLimits": manifest.get("logLimits"),
                "attachmentSummary": manifest.get("attachments"),
                "renderEvidenceSummary": manifest.get("renderEvidence"),
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
            "modelCatalog": summarize_model_catalog(model_catalog),
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
            "windowsShell": {
                "manifest": {
                    "supported": shell_manifest.get("supported"),
                    "artifacts": referenced_shell_entries,
                    "failureCount": shell_manifest.get("failureCount"),
                }
                if shell_manifest
                else None,
                "entryCount": len(shell_entries),
                "missingReferencedEntries": missing_shell_entries,
                "unlistedEntries": unlisted_shell_entries,
                "current": shell_current,
                "journal": shell_journal,
            },
            "webView2": summarize_webview2(webview2),
            "webView2RenderEvidence": render_evidence_summary,
            "installIntegrity": summarize_install_integrity(
                install_integrity,
                collected=install_integrity_collected,
            ),
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
        f"Manifest: format={manifest['formatVersion']} revision={manifest['schemaRevision']} "
        f"outcome={manifest['outcome']} "
        f"generatedAt={manifest['generatedAt']} failures={len(manifest['failures'])}"
    )
    print(
        "Capabilities: "
        f"declared={len(manifest['capabilities'])} "
        f"unhandled={json.dumps(manifest['unhandledCapabilities'], ensure_ascii=False)}"
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
    shell = summary["windowsShell"]
    shell_manifest = shell["manifest"] or {}
    shell_current = shell["current"] or {}
    shell_journal = shell["journal"]
    print(
        "Windows Shell: "
        f"supported={shell_manifest.get('supported')} entries={shell['entryCount']} "
        f"currentWindows={shell_current.get('windowCount')} "
        f"journalRecords={shell_journal['recordCount']} "
        f"events={shell_journal['eventCount']} dropped={shell_journal['droppedEventCount']} "
        f"truncated={shell_journal['truncatedEventCount']}"
    )
    webview2 = summary["webView2"] or {}
    print(
        "WebView2: "
        f"supported={webview2.get('supported')} runtime={webview2.get('runtimeVersion')} "
        f"registered={len(webview2.get('registeredWindows', []))} "
        f"failureReports={webview2.get('failureReportCount')} "
        f"events={webview2.get('eventCount')} dropped={webview2.get('droppedEventCount')} "
        f"kinds={json.dumps(webview2.get('processFailureKinds', {}), ensure_ascii=False, sort_keys=True)}"
    )
    render_evidence = summary["webView2RenderEvidence"] or {}
    print(
        "WebView2 render evidence: "
        f"requested={render_evidence.get('includeRequested')} "
        f"capture={render_evidence.get('captureId')} "
        f"trigger={render_evidence.get('trigger')} "
        f"deltaMs={render_evidence.get('startDeltaMs')} "
        f"artifacts={len(render_evidence.get('includedArtifacts', []))} "
        f"missing={len(render_evidence.get('missingArtifacts', []))}"
    )
    install_integrity = summary["installIntegrity"]
    print(
        "Install integrity: "
        f"status={install_integrity.get('status')} "
        f"evidence={install_integrity.get('evidence')} "
        f"error={install_integrity.get('errorCode')} "
        f"shellBuild={install_integrity.get('shell', {}).get('buildId')} "
        f"payloadValid={install_integrity.get('payload', {}).get('validated')} "
        f"runtimeObserved={json.dumps(install_integrity.get('runtime', {}).get('observedReadiness', {}), ensure_ascii=False, sort_keys=True)}"
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
