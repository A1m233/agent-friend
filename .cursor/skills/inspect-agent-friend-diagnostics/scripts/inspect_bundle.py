#!/usr/bin/env python3
"""Safely summarize an agent-friend diagnostic ZIP without extracting it."""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime
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
    "desktop-application-launch-v1": "desktopApplications",
    "selected-sessions-v1": "sessions",
    "selected-sessions-v2": "sessions",
    "selected-sessions-v3": "sessions",
    "selected-sessions-v4": "sessions",
    "sandbox-incidents-v1": "sandboxIncidents",
    "sandbox-incidents-v2": "sandboxIncidents",
    "sandbox-incidents-v3": "sandboxIncidents",
    "model-catalog-v1": "modelCatalog",
    "bounded-log-tail-v1": "logs",
    "full-retained-logs-v1": "logs",
    "attachment-integrity-v1": "attachments",
    "attachment-integrity-v2": "attachments",
    "windows-shell-history-v1": "windowsShell",
    "webview2-process-health-v1": "webView2",
    "webview2-render-evidence-v1": "webView2RenderEvidence",
    "webview2-render-evidence-v2": "webView2RenderEvidence",
    "webview2-render-evidence-v3": "webView2RenderEvidence",
    "webview2-render-evidence-v4": "webView2RenderEvidence",
    "install-integrity-v1": "installIntegrity",
    "desktop-instance-governance-v1": "desktopInstance",
    "managed-execution-environment-v1": "executionEnvironments",
    "command-runtime-health-v1": "commandRuntime",
    "pet-input-evidence-v1": "petInputEvidence",
    "pet-input-evidence-v2": "petInputEvidence",
    "pet-render-evidence-v1": "petRenderEvidence",
}

PET_INPUT_PREFIX = "diagnostics/pet-input/"
PET_INPUT_COVERAGE = PET_INPUT_PREFIX + "coverage.json"
PET_INPUT_LIMITS = {
    "historyBytes": 16 * 1024 * 1024,
    "historyAgeDays": 7,
    "segmentBytes": 2 * 1024 * 1024,
    "incidentBytes": 8 * 1024 * 1024,
    "incidentAgeDays": 30,
    "incidentCount": 32,
    "incidentFileBytes": 256 * 1024,
    "eventBytes": 4096,
    "queueEntries": 256,
    "queueBytes": 1024 * 1024,
    "totalBytes": 25 * 1024 * 1024,
}
PET_INPUT_GAPS = {
    "not-collected",
    "unsupported",
    "not-covered",
    "expired",
    "evicted",
    "truncated",
    "write-failed",
    "read-failed",
    "corrupt-tail",
    "unknown",
    "dropped",
    "invalid",
    "rate-limited",
    "metadata-folded",
    "clock-uncertain",
    "unclosed-process",
    "historical-metadata-unavailable",
}
PET_INPUT_COUNTERS = (
    "dropped",
    "frontendDropped",
    "invalid",
    "rateLimited",
    "writeFailed",
    "evicted",
    "expired",
    "metadataFolded",
    "aggregated",
    "truncated",
    "corruptTail",
    "readFailed",
)


class BundleError(Exception):
    """Raised when the archive cannot be safely interpreted."""


def _safe_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


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


def read_json(archive: zipfile.ZipFile, name: str, *, uncapped: bool = False) -> Any:
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise BundleError(f"missing required entry: {name}") from error
    if not uncapped and info.file_size > MAX_JSON_BYTES:
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


def summarize_desktop_instance(value: Any, *, collected: bool) -> dict[str, Any]:
    if value is None:
        return {"evidence": "missing" if collected else "not-collected"}
    if not isinstance(value, dict):
        return {"evidence": "invalid", "error": "desktop instance root is not an object"}
    identity = value.get("identity") if isinstance(value.get("identity"), dict) else {}
    owner = value.get("owner") if isinstance(value.get("owner"), dict) else None
    return {
        "evidence": "collected",
        "schemaVersion": value.get("schemaVersion"),
        "runtimeKind": identity.get("runtimeKind"),
        "instanceDomain": identity.get("instanceDomain"),
        "profileId": identity.get("profileId"),
        "processSessionId": owner.get("processSessionId") if owner else None,
        "owner": {
            "runtimeKind": owner.get("runtimeKind"),
            "pid": owner.get("pid"),
            "profileId": owner.get("profileId"),
            "processSessionId": owner.get("processSessionId"),
            "worktree": owner.get("worktree"),
            "installRoot": owner.get("installRoot"),
            "publishedAt": owner.get("publishedAt"),
        }
        if owner
        else None,
    }


def summarize_execution_environments(value: Any, *, collected: bool) -> dict[str, Any]:
    if value is None:
        return {
            "status": "unavailable",
            "evidence": "missing" if collected else "not-collected",
        }
    if not isinstance(value, dict):
        return {
            "status": "unavailable",
            "evidence": "invalid",
            "error": "execution environments root is not an object",
        }
    providers = value.get("providers") if isinstance(value.get("providers"), list) else []
    provider_items = [item for item in providers if isinstance(item, dict)]
    statuses = Counter(str(item.get("status", "<missing>")) for item in provider_items)
    error_codes = Counter(
        str(item["errorCode"]) for item in provider_items if item.get("errorCode") not in (None, "")
    )
    active_overlays = 0
    inactive_overlays = 0
    validated_payloads = 0
    declared_payloads = 0
    provider_summaries = []
    for item in provider_items:
        overlays = (
            item.get("sessionOverlays") if isinstance(item.get("sessionOverlays"), dict) else {}
        )
        payload = item.get("runtimePayload") if isinstance(item.get("runtimePayload"), dict) else {}
        active_overlays += _safe_int(overlays.get("activeCount"))
        inactive_overlays += _safe_int(overlays.get("inactiveCount"))
        declared_payloads += int(payload.get("declared") is True)
        validated_payloads += int(payload.get("validated") is True)
        provider_summaries.append(
            {
                "provider": item.get("provider"),
                "status": item.get("status"),
                "runtimeVersion": item.get("runtimeVersion"),
                "identity": item.get("identity"),
                "platform": item.get("platform"),
                "architecture": item.get("architecture"),
                "errorCode": item.get("errorCode"),
                "prepareDurationBucket": item.get("prepareDurationBucket"),
                "totalOverlayBytesBucket": overlays.get("totalBytesBucket"),
                "sharedCacheBytesBucket": (
                    item.get("sharedCache", {}).get("bytesBucket")
                    if isinstance(item.get("sharedCache"), dict)
                    else None
                ),
                "lastGcStatus": (
                    item.get("lastGc", {}).get("status")
                    if isinstance(item.get("lastGc"), dict)
                    else None
                ),
                "runtimePayloadDeclared": payload.get("declared"),
                "runtimePayloadValidated": payload.get("validated"),
            }
        )
    return {
        "status": value.get("status", "unavailable"),
        "evidence": "collected",
        "schemaVersion": value.get("schemaVersion"),
        "providerCount": len(provider_items),
        "providerStatuses": dict(sorted(statuses.items())),
        "errorCodes": dict(sorted(error_codes.items())),
        "activeOverlayCount": active_overlays,
        "inactiveOverlayCount": inactive_overlays,
        "declaredPayloadCount": declared_payloads,
        "validatedPayloadCount": validated_payloads,
        "providers": provider_summaries,
    }


def _pet_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _pet_number(value: Any, low: float, high: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high


def _pet_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 48:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _pet_id(value: Any, *, process: bool = False) -> bool:
    pattern = r"[0-9-]{1,96}" if process else r"[a-fA-F0-9-]{1,96}"
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _pet_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("identityComplete"), bool):
        raise BundleError("pet input identity is not an object")
    result: dict[str, Any] = {}
    choices = {
        "platform": {"windows", "macos", "linux", "unknown"},
        "buildMode": {"debug", "release", "dev", "installed", "unknown"},
    }
    patterns = {
        "sourceCommit": r"[a-fA-F0-9]{7,64}",
        "buildId": r"[a-fA-F0-9TZ-]{1,96}",
        "appVersion": r"[0-9.-]{1,48}",
        "webviewVersion": r"[0-9.-]{1,48}",
    }
    for key in ("sourceDirty", "identityComplete", *choices, *patterns):
        if key not in value:
            continue
        item = value[key]
        valid = (
            isinstance(item, bool)
            if key in {"sourceDirty", "identityComplete"}
            else isinstance(item, str)
            and (item in choices[key] if key in choices else re.fullmatch(patterns[key], item))
        )
        if not valid:
            raise BundleError("pet input identity has an invalid field")
        result[key] = item
    return result


def _pet_incident_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or "pageInstanceId" not in value:
        raise BundleError("pet input incident summary is not an object")
    for key in ("incidentId", "pageInstanceId"):
        item = value.get(key)
        if not _pet_id(item) and not (
            key == "pageInstanceId" and (item is None or _pet_integer(item))
        ):
            raise BundleError("pet input incident has an invalid correlation identity")
    if not _pet_id(value.get("processSessionId"), process=True):
        raise BundleError("pet input incident has an invalid process identity")
    if not all(_pet_timestamp(value.get(key)) for key in ("firstRecordedAt", "lastRecordedAt")):
        raise BundleError("pet input incident has an invalid time range")
    if (
        not _pet_integer(value.get("count"))
        or value.get("recovery") not in ("observed", "unknown")
        or not isinstance(value.get("truncated"), bool)
        or not isinstance(value.get("closed"), bool)
        or value.get("signal")
        not in (
            "dpr-mismatch",
            "dpr-command-failed",
            "passthrough-command-failed",
            "passthrough-verification-failed",
        )
    ):
        raise BundleError("pet input incident has invalid signal metadata")
    return {
        key: value[key]
        for key in (
            "incidentId",
            "processSessionId",
            "pageInstanceId",
            "signal",
            "firstRecordedAt",
            "lastRecordedAt",
            "count",
            "recovery",
            "closed",
            "truncated",
        )
    }


def _pet_envelope(value: Any, process_session: str) -> None:
    if (
        not isinstance(value, dict)
        or not _pet_integer(value.get("schemaVersion"))
        or value["schemaVersion"] != 1
    ):
        raise BundleError("pet input record has an invalid envelope")
    if value.get("processSessionId") != process_session:
        raise BundleError("pet input record process identity differs from its artifact")
    if value.get("kind") == "segment-header":
        _pet_identity(value.get("identity"))
        if (
            not _pet_integer(value.get("recordSeq"))
            or not _pet_timestamp(value.get("recordedAt"))
            or value.get("source") != "collector"
        ):
            raise BundleError("pet input history header has invalid sequence or time metadata")
        return
    if (
        not _pet_integer(value.get("recordSeq"))
        or not _pet_timestamp(value.get("recordedAt"))
        or not _pet_number(value.get("processMonotonicMs"), 0, 1e15)
        or value.get("source") not in ("frontend", "native", "collector")
        or not isinstance(value.get("kind"), str)
        or not re.fullmatch(r"[a-z-]{1,48}", value["kind"])
    ):
        raise BundleError("pet input record has invalid sequence, time, source or kind")
    for key in (
        "actualDpr",
        "displayDpr",
        "reportedDpr",
        "oldDpr",
        "newDpr",
        "scale",
        "windowScaleFactor",
    ):
        if key in value and not _pet_number(value[key], 0, 64):
            raise BundleError("pet input record has invalid scale metadata")
    for key in ("pageInstanceId", "requestId"):
        if key in value and not (_pet_id(value[key]) or _pet_integer(value[key])):
            raise BundleError("pet input record has an invalid correlation identity")
    for key in (
        "ingressSeq",
        "frontendSeq",
        "displayRevision",
        "sampleId",
        "dprRevision",
        "applySeq",
        "count",
        "firstSeq",
        "lastSeq",
        "droppedCount",
    ):
        if key in value and not _pet_integer(value[key]):
            raise BundleError("pet input record has an invalid sequence or counter")
    for key, expected in (
        ("physical", {"x", "y"}),
        ("css", {"x", "y"}),
        ("windowOrigin", {"x", "y"}),
        ("viewport", {"width", "height"}),
        ("windowSize", {"width", "height"}),
        ("spriteBounds", {"x", "y", "width", "height"}),
    ):
        if key in value:
            item = value[key]
            if (
                not isinstance(item, dict)
                or set(item) != expected
                or not all(
                    _pet_number(number, 0 if field in ("width", "height") else -1e6, 1e6)
                    for field, number in item.items()
                )
            ):
                raise BundleError("pet input record has invalid geometry metadata")


def _pet_read_json(archive: zipfile.ZipFile, entry: str, limit: int) -> Any:
    if archive.getinfo(entry).file_size > limit:
        raise BundleError("pet input JSON artifact exceeds its byte limit")
    try:
        return read_json(archive, entry)
    except RecursionError as error:
        raise BundleError("pet input JSON artifact exceeds nesting limits") from error


def _pet_durability(value: dict[str, Any]) -> dict[str, Any]:
    if (
        value.get("scope") != "persisted-only"
        or value.get("tailCompleteness") not in ("unknown", "complete")
        or not _pet_integer(value.get("normalFlushMs"))
        or not _pet_integer(value.get("syncRequestMs"))
    ):
        raise BundleError("pet input coverage has invalid durability metadata")
    result = {
        key: value[key] for key in ("scope", "tailCompleteness", "normalFlushMs", "syncRequestMs")
    }
    if "normalEnd" in value:
        if value["normalEnd"] is not None and not isinstance(value["normalEnd"], bool):
            raise BundleError("pet input coverage has an invalid normal-end marker")
        result["normalEnd"] = value["normalEnd"]
    for key in ("lastWritten", "lastSyncRequested"):
        if key not in value:
            continue
        position = value[key]
        if position is None:
            result[key] = None
            continue
        if not isinstance(position, dict):
            raise BundleError("pet input coverage has an invalid durability position")
        summary = {}
        for field, validator in (
            ("processSessionId", lambda item: _pet_id(item, process=True)),
            ("recordSeq", _pet_integer),
            ("recordedAt", _pet_timestamp),
        ):
            if field in position:
                if not validator(position[field]):
                    raise BundleError("pet input coverage has an invalid durability position")
                summary[field] = position[field]
        result[key] = summary
    if "lastDroppedRange" in value:
        dropped = value["lastDroppedRange"]
        if dropped is None:
            result["lastDroppedRange"] = None
        else:
            if (
                not isinstance(dropped, dict)
                or not _pet_id(dropped.get("processSessionId"), process=True)
                or not _pet_integer(dropped.get("firstIngressSeq"))
                or not _pet_integer(dropped.get("lastIngressSeq"))
                or dropped["lastIngressSeq"] < dropped["firstIngressSeq"]
                or dropped.get("interior") != "not-necessarily-contiguous"
            ):
                raise BundleError("pet input coverage has an invalid dropped-ingress range")
            result["lastDroppedRange"] = {
                key: dropped[key]
                for key in ("processSessionId", "firstIngressSeq", "lastIngressSeq", "interior")
            }
    return result


def _pet_evictions(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 256:
        raise BundleError("pet input coverage has an invalid eviction collection")
    result = []
    for item in value:
        if (
            not isinstance(item, dict)
            or item.get("kind") not in ("expired", "evicted")
            or item.get("scope") not in ("history", "incident")
            or not _pet_integer(item.get("bytes"))
            or not isinstance(item.get("createdAtMs"), int)
            or isinstance(item["createdAtMs"], bool)
        ):
            raise BundleError("pet input coverage has invalid eviction metadata")
        summary = {key: item[key] for key in ("kind", "scope", "bytes", "createdAtMs")}
        for key, valid in (
            ("processSessionId", lambda item: _pet_id(item, process=True)),
            ("firstRecordSeq", _pet_integer),
        ):
            if key in item:
                if item[key] is not None and not valid(item[key]):
                    raise BundleError("pet input coverage has an invalid eviction position")
                summary[key] = item[key]
        result.append(summary)
    return result


def _pet_artifact_records(
    archive: zipfile.ZipFile, artifact: dict[str, Any], identity: dict[str, Any] | None
) -> tuple[int, dict[str, Any] | None]:
    entry = artifact["entry"]
    process_session = artifact["processSessionId"]
    if artifact["kind"] == "incident":
        value = _pet_read_json(archive, entry, PET_INPUT_LIMITS["incidentFileBytes"])
        if (
            not isinstance(value, dict)
            or not _pet_integer(value.get("schemaVersion"))
            or value["schemaVersion"] != 1
        ):
            raise BundleError("pet input incident has an invalid root")
        summary = _pet_incident_summary(value)
        if summary["processSessionId"] != process_session:
            raise BundleError("pet input incident process identity differs from coverage")
        if _pet_identity(value.get("identity")) != identity:
            raise BundleError("pet input incident identity differs from coverage")
        if not isinstance(value.get("closed"), bool):
            raise BundleError("pet input incident has an invalid closure state")
        _pet_envelope(value.get("firstSignal"), process_session)
        context = value.get("context")
        if not isinstance(context, list):
            raise BundleError("pet input incident context is not an array")
        for record in context:
            _pet_envelope(record, process_session)
            if (
                len(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                > 4096
            ):
                raise BundleError("pet input context record exceeds its byte limit")
        return len(context), summary

    count = 0
    first_record = last_record = None
    with archive.open(entry) as stream:
        while True:
            raw = stream.readline(PET_INPUT_LIMITS["eventBytes"] + 2)
            if not raw:
                break
            if len(raw) > PET_INPUT_LIMITS["eventBytes"] + 1 or not raw.endswith(b"\n"):
                raise BundleError("pet input history has an oversized or incomplete line")
            try:
                record = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
                raise BundleError("pet input history has invalid JSON") from error
            _pet_envelope(record, process_session)
            if count == 0:
                if record.get("kind") != "segment-header":
                    raise BundleError("pet input history is missing its identity header")
                if _pet_identity(record.get("identity")) != identity:
                    raise BundleError("pet input history identity differs from coverage")
            else:
                if record.get("kind") == "segment-header":
                    raise BundleError("pet input history has more than one identity header")
                if first_record is None:
                    first_record = record
                last_record = record
            count += 1
    if not count:
        raise BundleError("pet input history is empty")
    for prefix, record in (("first", first_record), ("last", last_record)):
        if artifact[prefix + "RecordSeq"] != (
            record.get("recordSeq") if record else None
        ) or artifact[prefix + "RecordedAt"] != (record.get("recordedAt") if record else None):
            raise BundleError("pet input history range differs from coverage")
    return count - 1, None


def summarize_pet_input(
    archive: zipfile.ZipFile, manifest: dict[str, Any], warnings: list[str]
) -> dict[str, Any]:
    """Validate retained evidence without copying event bodies or unknown metadata to output."""
    names = set(archive.namelist())
    entries = sorted(name for name in names if name.startswith(PET_INPUT_PREFIX))
    capabilities = manifest.get("capabilities")
    declared = isinstance(capabilities, list) and bool(
        {"pet-input-evidence-v1", "pet-input-evidence-v2"}.intersection(capabilities)
    )
    if not declared and not entries and "petInputEvidence" not in manifest:
        return {"evidence": "not-collected"}
    if PET_INPUT_COVERAGE not in names:
        warnings.append("pet input evidence is missing required coverage metadata")
        return {"evidence": "missing", "entryCount": len(entries)}
    total_bytes = sum(archive.getinfo(entry).file_size for entry in entries)
    if len(entries) > 515 or total_bytes > PET_INPUT_LIMITS["totalBytes"]:
        warnings.append("pet input evidence exceeds its file-count or total-byte limit")
        return {"evidence": "invalid", "entryCount": len(entries)}
    try:
        coverage = _pet_read_json(archive, PET_INPUT_COVERAGE, 1024 * 1024)
        if (
            not isinstance(coverage, dict)
            or not _pet_integer(coverage.get("schemaVersion"))
            or coverage["schemaVersion"] != 1
        ):
            raise BundleError("pet input coverage has an invalid or unsupported schema")
        if coverage.get("status") not in (
            "available",
            "not-collected",
            "not-covered",
            "read-failed",
            "unsupported",
        ):
            raise BundleError("pet input coverage has an invalid status")
        for key in ("artifacts", "identities", "gaps", "retainedIncidents"):
            if not isinstance(coverage.get(key), list) or len(coverage[key]) > 512:
                raise BundleError("pet input coverage has an invalid collection")
        for key in ("counters", "limits", "durability"):
            if not isinstance(coverage.get(key), dict):
                raise BundleError("pet input coverage has an invalid metadata object")
        for key in ("exportBytes", "failureCount"):
            if not _pet_integer(coverage.get(key)):
                raise BundleError("pet input coverage has an invalid byte or failure count")
        limits = coverage["limits"]
        if any(
            not _pet_integer(limits.get(key)) or limits[key] > maximum
            for key, maximum in PET_INPUT_LIMITS.items()
        ):
            raise BundleError("pet input coverage has invalid resource limits")
        counters = {
            key: coverage["counters"][key]
            for key in PET_INPUT_COUNTERS
            if key in coverage["counters"]
        }
        if not all(_pet_integer(item) for item in counters.values()):
            raise BundleError("pet input coverage has an invalid counter")
        durability = _pet_durability(coverage["durability"])
        evictions = _pet_evictions(coverage.get("evictions"))
        identities = {}
        for item in coverage["identities"]:
            if not isinstance(item, dict) or not _pet_id(
                item.get("processSessionId"), process=True
            ):
                raise BundleError("pet input coverage has an invalid process identity")
            process = item["processSessionId"]
            if process in identities:
                raise BundleError("pet input coverage has duplicate process identities")
            identities[process] = _pet_identity(item.get("identity"))
        gaps = []
        for item in coverage["gaps"]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("kind"), str)
                or item["kind"] not in PET_INPUT_GAPS
            ):
                raise BundleError("pet input coverage has an invalid gap kind")
            if "count" in item and not _pet_integer(item["count"]):
                raise BundleError("pet input coverage has an invalid gap count")
            if "scope" in item and item["scope"] not in (
                "history",
                "incident",
                "collector",
                "metadata",
                "scan",
            ):
                raise BundleError("pet input coverage has an invalid gap scope")
            gaps.append({key: item[key] for key in ("kind", "count", "scope") if key in item})
        incidents = [_pet_incident_summary(item) for item in coverage["retainedIncidents"]]
        if len(incidents) > PET_INPUT_LIMITS["incidentCount"]:
            raise BundleError("pet input coverage exceeds its incident-count limit")
        artifacts = []
        for item in coverage["artifacts"]:
            if not isinstance(item, dict) or item.get("kind") not in ("history", "incident"):
                raise BundleError("pet input coverage has an invalid artifact")
            pattern = (
                r"diagnostics/pet-input/history-[0-9]+\.jsonl"
                if item["kind"] == "history"
                else r"diagnostics/pet-input/incident-[0-9]+\.json"
            )
            if not isinstance(item.get("entry"), str) or not re.fullmatch(pattern, item["entry"]):
                raise BundleError("pet input coverage references an invalid artifact name")
            if not _pet_id(item.get("processSessionId"), process=True):
                raise BundleError("pet input artifact has an invalid process identity")
            if item["processSessionId"] not in identities:
                raise BundleError("pet input artifact occurrence identity is missing")
            if not _pet_integer(item.get("bytes")) or item.get("status") not in (
                "available",
                "corrupt-tail",
                "truncated",
            ):
                raise BundleError("pet input artifact has invalid size or status")
            if "frozenBytes" in item and (
                not _pet_integer(item["frozenBytes"]) or item["frozenBytes"] < item["bytes"]
            ):
                raise BundleError("pet input artifact has an invalid frozen read boundary")
            for key in ("firstRecordSeq", "lastRecordSeq"):
                if item.get(key) is not None and not _pet_integer(item[key]):
                    raise BundleError("pet input artifact has an invalid sequence range")
            for key in ("firstRecordedAt", "lastRecordedAt"):
                if item.get(key) is not None and not _pet_timestamp(item[key]):
                    raise BundleError("pet input artifact has an invalid time range")
            artifacts.append(
                {
                    key: item.get(key)
                    for key in (
                        "entry",
                        "kind",
                        "bytes",
                        "processSessionId",
                        "firstRecordSeq",
                        "lastRecordSeq",
                        "firstRecordedAt",
                        "lastRecordedAt",
                        "status",
                    )
                }
            )
            if "frozenBytes" in item:
                artifacts[-1]["frozenBytes"] = item["frozenBytes"]
    except (BundleError, KeyError) as error:
        warnings.append(str(error))
        return {"evidence": "invalid", "entryCount": len(entries)}

    referenced = [item["entry"] for item in artifacts]
    missing = sorted(set(referenced).difference(names))
    unlisted = sorted(
        set(entries).difference(
            [PET_INPUT_COVERAGE, PET_INPUT_PREFIX + "current.json", *referenced]
        )
    )
    problems = []
    if coverage["exportBytes"] != total_bytes:
        problems.append("export byte count differs from archive")
    if coverage["failureCount"] > 0 and manifest.get("outcome") != "partial":
        problems.append("collection failures are not reflected in the bundle outcome")
    if len(referenced) != len(set(referenced)):
        problems.append("duplicate artifact references")
    if missing:
        problems.append("missing referenced artifacts")
    if unlisted:
        problems.append("unlisted artifacts")
    for kind, limit in (("history", "historyBytes"), ("incident", "incidentBytes")):
        if sum(item["bytes"] for item in artifacts if item["kind"] == kind) > limits[limit]:
            problems.append("artifact family exceeds its byte limit")
    manifest_summary = manifest.get("petInputEvidence")
    if not isinstance(manifest_summary, dict):
        problems.append("missing manifest summary")
    else:
        for key in ("status", "failureCount", "exportBytes", "artifacts"):
            if manifest_summary.get(key) != coverage[key]:
                problems.append("manifest and coverage summary differ")
                break
    record_counts = Counter()
    invalid_artifacts = 0
    for artifact in artifacts:
        entry = artifact["entry"]
        if entry not in names:
            continue
        try:
            info = archive.getinfo(entry)
            maximum = PET_INPUT_LIMITS[
                "segmentBytes" if artifact["kind"] == "history" else "incidentFileBytes"
            ]
            if info.file_size != artifact["bytes"] or info.file_size > maximum:
                raise BundleError("pet input artifact byte count is invalid")
            count, incident = _pet_artifact_records(
                archive, artifact, identities.get(artifact["processSessionId"])
            )
            if incident is not None and incident not in incidents:
                raise BundleError("pet input incident differs from retained summary")
            record_counts[artifact["kind"]] += count
        except (BundleError, OSError, RuntimeError, zipfile.BadZipFile) as error:
            invalid_artifacts += 1
            warnings.append(f"{entry}: {error}")
    current = None
    current_entry = PET_INPUT_PREFIX + "current.json"
    if current_entry in names:
        try:
            value = _pet_read_json(archive, current_entry, 1024 * 1024)
            if not isinstance(value, dict) or value.get("scope") != "export-time":
                raise BundleError("pet input current snapshot is not explicitly export-time")
            if (
                not _pet_id(value.get("processSessionId"), process=True)
                or not isinstance(value.get("workerStopped"), bool)
                or not all(
                    _pet_integer(value.get(key))
                    for key in ("queuedBytes", "dropped", "invalid", "writeFailed")
                )
            ):
                raise BundleError("pet input current snapshot has invalid identity or counters")
            current = {
                "present": True,
                **{
                    key: value[key]
                    for key in (
                        "scope",
                        "processSessionId",
                        "queuedBytes",
                        "dropped",
                        "invalid",
                        "writeFailed",
                        "workerStopped",
                    )
                },
            }
            if current["writeFailed"] > 0 and (
                coverage["failureCount"] == 0
                or not any(gap["kind"] == "write-failed" for gap in gaps)
            ):
                problems.append("current write failure is missing from coverage")
        except BundleError as error:
            warnings.append(str(error))
            current = {"evidence": "invalid"}
            invalid_artifacts += 1
    for problem in problems:
        warnings.append("pet input evidence: " + problem)
    return {
        "evidence": "invalid" if problems or invalid_artifacts else "collected",
        "schemaVersion": coverage["schemaVersion"],
        "status": coverage["status"],
        "entryCount": len(entries),
        "exportBytes": coverage["exportBytes"],
        "failureCount": coverage["failureCount"],
        "artifacts": artifacts,
        "identities": [
            {"processSessionId": key, "identity": value} for key, value in identities.items()
        ],
        "counters": counters,
        "gaps": gaps,
        "evictions": evictions,
        "limits": {key: limits[key] for key in PET_INPUT_LIMITS},
        "durability": durability,
        "retainedIncidents": incidents,
        "historyRecordCount": record_counts["history"],
        "incidentContextRecordCount": record_counts["incident"],
        "invalidArtifactCount": invalid_artifacts,
        "missingReferencedEntries": missing,
        "unlistedEntryCount": len(unlisted),
        "current": current,
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
            "nearWhiteOpaqueRatio": metrics.get("nearWhiteOpaqueRatio"),
            "averageOpaqueLuma": metrics.get("averageOpaqueLuma"),
        }

    errors = capture.get("errors") if capture and isinstance(capture.get("errors"), list) else []
    recovery_assessment = (
        capture.get("recoveryAssessment")
        if capture and isinstance(capture.get("recoveryAssessment"), dict)
        else None
    )
    graphics = (
        capture.get("graphics") if capture and isinstance(capture.get("graphics"), dict) else {}
    )
    return {
        "schemaVersion": value.get("schemaVersion"),
        "supported": value.get("supported"),
        "includeRequested": value.get("includeRequested"),
        "automaticIncidentIncluded": value.get("automaticIncidentIncluded"),
        "armed": status.get("armed"),
        "captureInProgress": status.get("captureInProgress"),
        "pending": pending,
        "captureId": capture.get("captureId") if capture else None,
        "capturedAt": capture.get("captureCompletedAt") if capture else None,
        "trigger": capture.get("trigger") if capture else None,
        "automaticIncident": capture.get("automaticIncident") if capture else None,
        "recoveryGeneration": capture.get("recoveryGeneration") if capture else None,
        "recoveryAssessment": recovery_assessment,
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
    provider_reasoning_state_count = 0
    provider_reasoning_redacted_count = 0
    provider_reasoning_payload_leak_count = 0
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
        if event.get("type") == "provider_reasoning_state":
            provider_reasoning_state_count += 1
            if isinstance(payload, dict) and payload.get("redacted") is True:
                provider_reasoning_redacted_count += 1
            if isinstance(payload, dict) and "data" in payload:
                provider_reasoning_payload_leak_count += 1

    return {
        "entry": entry,
        "sessionId": value.get("session_id"),
        "eventCount": len(events),
        "eventTypes": dict(sorted(event_types.items())),
        "runIdCount": len(run_ids),
        "attachmentReferenceCount": attachment_references,
        "malformedEventCount": malformed_events,
        "providerReasoningStateCount": provider_reasoning_state_count,
        "providerReasoningRedactedCount": provider_reasoning_redacted_count,
        "providerReasoningPayloadLeakCount": provider_reasoning_payload_leak_count,
        "activeEventsPresent": "active_events" in value,
        "coverage": summarize_session_coverage(value.get("coverage")),
    }


def summarize_session_coverage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"evidence": "not-collected"}
    result: dict[str, Any] = {"evidence": "collected"}
    mode = value.get("mode")
    result["mode"] = mode if mode in ("all", "recent") else "unknown"
    for key in ("requestedTurns", "includedTurns", "readBytes", "eventBytes", "snapshotBytes"):
        raw = value.get(key)
        result[key] = raw if type(raw) is int and raw >= 0 else None
    result["completeWithinRequestedRange"] = value.get("completeWithinRequestedRange") is True
    result["earlierHistoryOmitted"] = value.get("earlierHistoryOmitted") is True
    limits = value.get("limits")
    result["byteLimitsRemoved"] = (
        value.get("schemaVersion") == 2
        and isinstance(limits, dict)
        and all(
            key in limits and limits[key] is None
            for key in ("scanBytes", "eventBytes", "exportBytes")
        )
    )
    result["gapCount"] = len(value["gaps"]) if isinstance(value.get("gaps"), list) else 0
    return result


def summarize_sandbox_incidents(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"evidence": "not-collected"}
    records = value.get("incidents")
    if not isinstance(records, list):
        return {"evidence": "invalid"}
    outcomes: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    stages: Counter[str] = Counter()
    invalid = 0
    related = 0
    log_signals: Counter[str] = Counter()
    log_scopes: Counter[str] = Counter()
    log_window_count = 0
    log_gap_count = 0
    helper_probes: Counter[str] = Counter()
    helper_native_codes: Counter[str] = Counter()
    initial_probe_failures = 0
    for record in records[:32]:
        if not isinstance(record, dict) or record.get("schemaVersion") != 1:
            invalid += 1
            continue
        outcome, stage = record.get("outcome"), record.get("stage")
        outcomes[outcome if outcome in ("ok", "failed", "in_progress") else "unknown"] += 1
        stages[
            stage
            if stage in ("start", "visibility", "repair", "setup", "connection")
            else "unknown"
        ] += 1
        related += bool(record.get("relatedIncidents"))
        initial = record.get("initialProbeFailure")
        if initial is not None:
            if isinstance(initial, dict) and isinstance(initial.get("codes"), list):
                initial_probe_failures += 1
            else:
                invalid += 1
        probe = record.get("failureHelperProbe")
        if probe is not None:
            if (
                not isinstance(probe, dict)
                or probe.get("scope") != "host_non_elevated_argument_parsing"
            ):
                invalid += 1
            else:
                status = probe.get("status")
                if isinstance(status, str) and status in {
                    "in_progress",
                    "ready",
                    "failed",
                    "launch_failed",
                    "timed_out",
                    "not_applicable",
                    "unavailable",
                }:
                    helper_probes[status] += 1
                else:
                    invalid += 1
                native = probe.get("nativeCode")
                if type(native) is int and 0 <= native <= 0xFFFFFFFF:
                    helper_native_codes[str(native)] += 1
                elif native is not None:
                    invalid += 1
        window = record.get("logWindow")
        if isinstance(window, dict):
            log_window_count += 1
            gaps = window.get("gaps")
            log_gap_count += len(gaps) if isinstance(gaps, list) else 0
            sources = window.get("records")
            if not isinstance(sources, list):
                invalid += 1
                sources = []
            for source in sources[:2]:
                if not isinstance(source, dict):
                    invalid += 1
                    continue
                scope = source.get("scope")
                log_scopes[scope if scope in ("appended", "unattributed_tail") else "unknown"] += 1
                events = source.get("events")
                if not isinstance(events, list):
                    invalid += 1
                    continue
                for event in events[:64]:
                    signal = event.get("signal") if isinstance(event, dict) else None
                    if isinstance(signal, str) and signal in {
                        "setup_users_missing_or_incompatible",
                        "setup_marker_missing_or_incompatible",
                        "setup_firewall_settings_changed",
                        "users_delete_after_login_failure",
                        "refresh_spawn_requested",
                        "refresh_exit_failed",
                        "refresh_launch_failed",
                        "report_clear_before_failed",
                        "report_clear_after_failed",
                        "marker_parse_failed",
                    }:
                        log_signals[signal] += 1
                    else:
                        invalid += 1
        error = record.get("error")
        if isinstance(error, dict) and isinstance(error.get("codes"), list):
            for code in error.get("codes", []):
                if isinstance(code, str) and code in {
                    "orchestrator_sandbox_dir_create_failed",
                    "orchestrator_elevation_check_failed",
                    "orchestrator_elevation_required",
                    "orchestrator_payload_serialize_failed",
                    "orchestrator_helper_launch_failed",
                    "orchestrator_helper_launch_canceled",
                    "orchestrator_helper_exit_nonzero",
                    "orchestrator_helper_report_read_failed",
                    "orchestrator_helper_incomplete",
                    "helper_request_args_failed",
                    "helper_sandbox_dir_create_failed",
                    "helper_log_failed",
                    "helper_user_provision_failed",
                    "helper_users_group_create_failed",
                    "helper_user_create_or_update_failed",
                    "helper_dpapi_protect_failed",
                    "helper_users_file_write_failed",
                    "helper_setup_marker_write_failed",
                    "helper_sid_resolve_failed",
                    "helper_capability_sid_failed",
                    "helper_firewall_com_init_failed",
                    "helper_firewall_policy_access_failed",
                    "helper_firewall_policy_ineffective",
                    "helper_firewall_rule_create_or_add_failed",
                    "helper_firewall_rule_verify_failed",
                    "helper_read_acl_helper_spawn_failed",
                    "helper_sandbox_lock_failed",
                    "helper_unknown_error",
                }:
                    errors[code] += 1
    return {
        "evidence": "collected",
        "count": len(records),
        "outcomes": dict(outcomes),
        "stages": dict(stages),
        "codes": dict(errors),
        "repairLinkCount": related,
        "logWindowCount": log_window_count,
        "logSignals": dict(log_signals),
        "logScopes": dict(log_scopes),
        "logGapCount": log_gap_count,
        "failureHelperProbes": dict(helper_probes),
        "failureHelperNativeCodes": dict(helper_native_codes),
        "initialProbeFailureCount": initial_probe_failures,
        "invalidCount": invalid,
        "gapCount": len(value["gaps"]) if isinstance(value.get("gaps"), list) else 0,
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
            or "webview2-render-evidence-v2" in capabilities
            or "webview2-render-evidence-v3" in capabilities
            or "webview2-render-evidence-v4" in capabilities
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
        desktop_instance_collected = (
            "desktop-instance-governance-v1" in capabilities
            or "diagnostics/desktop-instance.json" in names
        )
        desktop_instance = (
            optional_json(archive, "diagnostics/desktop-instance.json", warnings)
            if desktop_instance_collected
            else None
        )
        execution_environments_collected = (
            "managed-execution-environment-v1" in capabilities
            or "diagnostics/execution-environments.json" in names
        )
        execution_environments = (
            optional_json(
                archive,
                "diagnostics/execution-environments.json",
                warnings,
            )
            if execution_environments_collected
            else None
        )
        render_evidence_summary = summarize_webview2_render_evidence(
            webview2_render_evidence,
            set(names),
        )
        if render_evidence_summary is not None:
            render_evidence_summary["gpuRecoveryPolicy"] = (
                "hidden-evidence-then-guarded-restart"
                if "webview2-render-evidence-v4" in capabilities
                else "legacy-or-unknown"
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
                sessions.append(
                    summarize_session(
                        read_json(archive, name, uncapped="selected-sessions-v4" in capabilities),
                        name,
                    )
                )
            except BundleError as error:
                sessions.append({"entry": name, "error": str(error)})
        if {"selected-sessions-v3", "selected-sessions-v4"}.intersection(capabilities):
            for item in sessions:
                if item.get("coverage", {}).get("evidence") != "collected":
                    warnings.append(f"selected-sessions-v3 coverage unavailable: {item['entry']}")
        if {"selected-sessions-v2", "selected-sessions-v3", "selected-sessions-v4"}.intersection(
            capabilities
        ):
            leaked_reasoning_entries = [
                item["entry"]
                for item in sessions
                if item.get("providerReasoningPayloadLeakCount", 0) > 0
            ]
            if leaked_reasoning_entries:
                warnings.append(
                    "selected-sessions-v2 contains unredacted provider reasoning payloads: "
                    + ", ".join(leaked_reasoning_entries)
                )

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
            "desktopInstance": summarize_desktop_instance(
                desktop_instance,
                collected=desktop_instance_collected,
            ),
            "executionEnvironments": summarize_execution_environments(
                execution_environments,
                collected=execution_environments_collected,
            ),
            "desktopApplications": summarize_desktop_applications(
                optional_json(archive, "diagnostics/desktop-applications.json", warnings)
                if "diagnostics/desktop-applications.json" in names
                else None,
                collected="desktop-application-launch-v1" in capabilities,
            ),
            "commandRuntime": summarize_command_runtime(
                optional_json(archive, "diagnostics/command-runtime.json", warnings)
                if "diagnostics/command-runtime.json" in names
                else None,
                collected="command-runtime-health-v1" in capabilities
                or "diagnostics/command-runtime.json" in names,
            ),
            "sandboxIncidents": summarize_sandbox_incidents(
                optional_json(archive, "diagnostics/command-runtime-incidents.json", warnings)
                if {
                    "sandbox-incidents-v1",
                    "sandbox-incidents-v2",
                    "sandbox-incidents-v3",
                }.intersection(capabilities)
                or "diagnostics/command-runtime-incidents.json" in names
                else None
            ),
            "petInputEvidence": summarize_pet_input(archive, manifest, warnings),
            "petRenderEvidence": summarize_pet_render(archive, manifest, warnings),
            "warnings": warnings,
        }


def summarize_desktop_applications(value: Any, *, collected: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"evidence": "missing" if collected else "not-collected"}
    records = value.get("records", [])
    if not isinstance(records, list) or len(records) > 128 or value.get("schemaVersion") != 1:
        return {"evidence": "invalid"}
    counts: Counter[str] = Counter()
    invalid = 0
    for record in records:
        status = record.get("status") if isinstance(record, dict) else None
        if not isinstance(status, str) or status not in {
            "accepted",
            "failed",
            "denied",
            "cancelled",
            "unknown",
        }:
            invalid += 1
        else:
            counts[status] += 1
    return {
        "evidence": "collected",
        "recordCount": len(records),
        "statuses": dict(counts),
        "invalidCount": invalid,
        "gapCount": len(value.get("gaps", [])) if isinstance(value.get("gaps"), list) else None,
        "presentation": "not_observed",
    }


def summarize_command_runtime(value: Any, *, collected: bool) -> dict[str, Any]:
    if not collected:
        return {"evidence": "not-collected"}
    if not isinstance(value, dict) or not isinstance(value.get("events"), list):
        return {"evidence": "unavailable"}
    events = value["events"]
    allowed_codes = {
        "ok",
        "requested",
        "failed",
        "cancelled",
        "timed_out",
        "not_authorized",
        "allowed",
        "helper_launch_failed",
        "helper_incomplete",
        "helper_exit_failed",
        "state_invalid",
        "runtime_lost",
        "unknown",
        "proxy_domain_denied",
        "proxy_private_target_denied",
        "proxy_route_denied",
        "payload_rejected",
    }
    counts: dict[str, int] = {}
    invalid = 0
    for event in events:
        code = event.get("code") if isinstance(event, dict) else None
        if isinstance(code, str) and code in allowed_codes:
            counts[code] = counts.get(code, 0) + 1
        else:
            invalid += 1
    states = {
        "unsupported",
        "checking",
        "setup_required",
        "setting_up",
        "ready",
        "repair_required",
        "unavailable",
    }
    return {
        "evidence": "collected",
        "status": value.get("status")
        if isinstance(value.get("status"), str) and value["status"] in states
        else "unknown",
        "eventCount": len(events),
        "codes": counts,
        "invalidCount": invalid,
        "gapCount": len(value["gaps"]) if isinstance(value.get("gaps"), list) else None,
    }


def summarize_pet_render(
    archive: zipfile.ZipFile, manifest: dict[str, Any], warnings: list[str]
) -> dict[str, Any]:
    prefix = "diagnostics/pet-render/"
    entry = prefix + "coverage.json"
    names = set(archive.namelist())
    declared = "pet-render-evidence-v1" in manifest.get("capabilities", [])
    if entry not in names:
        if declared:
            warnings.append("pet render missing coverage")
        return {"evidence": "missing" if declared else "not-collected"}
    result: dict[str, Any] = {
        "evidence": "collected",
        "historyRecords": 0,
        "incidentCount": 0,
        "signals": {},
        "processCount": 0,
        "presentation": "unknown",
        "gaps": [],
    }
    processes: set[str] = set()
    first_at: int | None = None
    last_at: int | None = None
    signals: Counter[str] = Counter()
    try:
        if archive.getinfo(entry).file_size > 16384:
            raise BundleError("pet render oversized coverage")
        coverage = read_json(archive, entry)
        if not isinstance(coverage, dict) or coverage.get("schemaVersion") != 1:
            raise BundleError("pet render invalid coverage")
        current = coverage.get("current", {})
        if isinstance(current, dict):
            result["currentWriteFailed"] = _safe_int(current.get("writeFailed"))
            if current.get("writerStopped") is True or result["currentWriteFailed"] > 0:
                warnings.append("pet render writer reported incomplete coverage")
        artifacts = coverage.get("artifacts")
        if not isinstance(artifacts, list) or len(artifacts) > 96:
            raise BundleError("pet render invalid artifact list")
        known_gaps = {
            "truncated",
            "incomplete-tail",
            "not-covered",
            "corrupt-status",
            "status-unavailable",
            "read-failed",
            "dropped",
            "invalid",
            "writeFailed",
            "evicted",
        }
        result["gaps"] = [
            g for g in coverage.get("gaps", []) if isinstance(g, str) and g in known_gaps
        ]
        for key in ("dropped", "invalid", "writeFailed", "evicted"):
            result[key] = _safe_int(coverage.get(key))
        total = 0
        seen: set[str] = set()
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise BundleError("pet render invalid artifact")
            name = artifact.get("entry", "")
            if (
                not isinstance(name, str)
                or not re.fullmatch(
                    r"diagnostics/pet-render/(history-\d+\.jsonl|incident-\d+\.json)", name
                )
                or name in seen
            ):
                raise BundleError("pet render invalid artifact name")
            seen.add(name)
            if name not in names:
                warnings.append("pet render missing artifact")
                continue
            size = archive.getinfo(name).file_size
            limit = 1024 * 1024 if name.endswith("jsonl") else 512 * 1024
            total += size
            if size > limit or total > 24 * 1024 * 1024:
                raise BundleError("pet render artifact budget exceeded")
            if size != artifact.get("bytes"):
                warnings.append("pet render artifact size mismatch")
            if name.endswith("jsonl"):
                with archive.open(name) as stream:
                    rows = []
                    for line in stream:
                        if len(line) > 16384:
                            raise BundleError("pet render oversized record")
                        if not line.endswith(b"\n"):
                            warnings.append("pet render incomplete tail")
                            continue
                        rows.append(json.loads(line))
                result["historyRecords"] += len(rows)
            else:
                incident = read_json(archive, name)
                if not isinstance(incident, dict) or incident.get("schemaVersion") != 1:
                    raise BundleError("pet render invalid incident")
                rows = incident.get("records")
                if not isinstance(rows, list):
                    raise BundleError("pet render invalid incident context")
                signal = incident.get("signal")
                if signal in {
                    "user-reported",
                    "context-lost",
                    "observer-failed",
                    "render-progress-missing",
                    "observation-gap",
                    "window-operation-failed",
                }:
                    signals[signal] += 1
                else:
                    warnings.append("pet render unknown signal")
                if _safe_int(incident.get("omittedRecords")) > 0:
                    result["gaps"].append("truncated")
                result["incidentCount"] += 1
            for row in rows:
                if not isinstance(row, dict) or row.get("schemaVersion") != 1:
                    raise BundleError("pet render invalid record")
                session = row.get("processSessionId")
                if not isinstance(session, str) or len(session) > 96:
                    raise BundleError("pet render invalid process identity")
                if not isinstance(row.get("recordedAt"), int) or not isinstance(
                    row.get("recordSeq"), int
                ):
                    raise BundleError("pet render invalid record ordering")
                at = row["recordedAt"]
                first_at = at if first_at is None else min(first_at, at)
                last_at = at if last_at is None else max(last_at, at)
                processes.add(session)
        unlisted = {n for n in names if n.startswith(prefix) and n != entry} - seen
        if unlisted:
            warnings.append("pet render unlisted artifacts")
    except (BundleError, ValueError, TypeError, KeyError) as error:
        # Never echo exception text: malformed JSON may contain private data.
        warnings.append("pet render invalid evidence: " + type(error).__name__)
        result["evidence"] = "partial"
    result["signals"] = dict(signals)
    result["processCount"] = len(processes)
    result["firstRecordedAtUnixMs"] = first_at
    result["lastRecordedAtUnixMs"] = last_at
    return result


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
    desktop_instance = summary["desktopInstance"]
    desktop_owner = desktop_instance.get("owner") or {}
    print(
        "Desktop instance: "
        f"evidence={desktop_instance.get('evidence')} "
        f"kind={desktop_instance.get('runtimeKind')} "
        f"domain={desktop_instance.get('instanceDomain')} "
        f"ownerKind={desktop_owner.get('runtimeKind')} "
        f"ownerPid={desktop_owner.get('pid')} "
        f"session={desktop_owner.get('processSessionId')}"
    )
    execution_environments = summary["executionEnvironments"]
    print(
        "Managed execution environments: "
        f"evidence={execution_environments.get('evidence')} "
        f"status={execution_environments.get('status')} "
        f"providers={execution_environments.get('providerCount')} "
        f"statuses={json.dumps(execution_environments.get('providerStatuses', {}), ensure_ascii=False, sort_keys=True)} "
        f"activeOverlays={execution_environments.get('activeOverlayCount')} "
        f"inactiveOverlays={execution_environments.get('inactiveOverlayCount')} "
        f"payloadValidated={execution_environments.get('validatedPayloadCount')}"
    )
    print("Command runtime: " + json.dumps(summary["commandRuntime"], ensure_ascii=False))
    print("Sandbox incidents: " + json.dumps(summary["sandboxIncidents"], ensure_ascii=False))
    render = summary["petRenderEvidence"]
    print(
        f"Pet render evidence: evidence={render.get('evidence')} "
        f"records={render.get('historyRecords')} incidents={render.get('incidentCount')} "
        f"processes={render.get('processCount')} presentation=unknown"
    )
    pet_input = summary["petInputEvidence"]
    print(
        "Pet input evidence: "
        f"evidence={pet_input.get('evidence')} status={pet_input.get('status')} "
        f"historyRecords={pet_input.get('historyRecordCount')} "
        f"incidents={len(pet_input.get('retainedIncidents', []))} "
        f"failures={pet_input.get('failureCount')} "
        f"gaps={len(pet_input.get('gaps', []))}"
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
