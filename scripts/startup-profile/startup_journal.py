"""Project startup spans without summing nested phases or inventing missing events."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from startup_evidence import digest

PHASES = frozenset(
    {
        "shell-context",
        "instance-gate",
        "settings-load",
        "runtime-discovery",
        "payload-verification",
        "tauri-build",
        "tauri-initialize",
        "tauri-setup",
        "service-start",
        "service-spawn",
        "service-readiness",
    }
)
BASE_PHASES = PHASES - {
    "service-start",
    "service-spawn",
    "service-readiness",
    "payload-verification",
}


def number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def analyze(paths: list[Path], session_id: str) -> dict[str, Any]:
    phases: dict[int, dict[str, Any]] = {}
    gaps: list[str] = []
    markers: list[dict[str, Any]] = []
    matched = 0
    inputs = []
    runtime_identities = []
    frontend = []
    verification = []
    for path in paths:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("log exceeds 32 MiB; provide bounded runtime log segments")
        inputs.append({"sha256": digest(path), "bytes": path.stat().st_size})
        with path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    event = json.loads(line)
                except ValueError:
                    gaps.append("invalid-json-line")
                    continue
                if not isinstance(event, dict) or event.get("processSessionId") != session_id:
                    continue
                matched += 1
                if matched > 4000:
                    raise ValueError("too many session events")
                details = event.get("details")
                if not isinstance(details, dict):
                    continue
                kind = event.get("kind")
                if event.get("source") == "startup" and kind == "frontend-milestone":
                    if (
                        details.get("milestone")
                        in {
                            "module-loaded",
                            "settings-ready",
                            "root-committed",
                            "models-ready",
                            "restore-ready",
                            "composer-ready",
                            "frontend-ready",
                        }
                        and isinstance(details.get("pageId"), str)
                        and number(details.get("elapsedMs"))
                        and number(details.get("pageElapsedMs"))
                    ):
                        frontend.append(
                            {
                                key: details[key]
                                for key in ("milestone", "pageId", "elapsedMs", "pageElapsedMs")
                            }
                        )
                    else:
                        gaps.append("invalid-frontend-milestone")
                    continue
                if kind == "identity-resolved":
                    identity = details.get("buildIdentity")
                    safe_identity = None
                    if isinstance(identity, dict):
                        safe_identity = {
                            key: identity.get(key)
                            for key in (
                                "schemaVersion",
                                "productVersion",
                                "buildId",
                                "sourceCommit",
                                "sourceTreeSha256",
                                "dirty",
                                "buildMode",
                                "distribution",
                                "profileId",
                            )
                            if isinstance(identity.get(key), (str, int, bool))
                        }
                    runtime_identities.append(
                        {
                            "runtimeKind": details.get("runtimeKind")
                            if details.get("runtimeKind") in {"dev", "production"}
                            else None,
                            "buildIdentity": safe_identity,
                        }
                    )
                if kind in {
                    "process-started",
                    "owner-published",
                    "page-visibility",
                    "runtime-attached",
                }:
                    marker = {"kind": kind, "recordedAt": event.get("recordedAt")}
                    if kind == "page-visibility":
                        marker["windowLabel"] = (
                            details.get("windowLabel")
                            if details.get("windowLabel") in {"pet", "chat", "bubble", "voice-call"}
                            else None
                        )
                        marker["visibilityState"] = (
                            details.get("visibilityState")
                            if details.get("visibilityState") in {"visible", "hidden"}
                            else None
                        )
                    markers.append(marker)
                if event.get("source") != "startup" or kind not in {
                    "phase-started",
                    "phase-finished",
                }:
                    continue
                phase_id = details.get("phaseId")
                phase = details.get("phase")
                status = details.get("status")
                fields = {
                    key: details.get(key) for key in ("startElapsedMs", "elapsedMs", "durationMs")
                }
                if (
                    not isinstance(phase_id, int)
                    or isinstance(phase_id, bool)
                    or phase_id < 1
                    or phase not in PHASES
                    or not all(number(value) for value in fields.values())
                    or fields["elapsedMs"] < fields["startElapsedMs"]
                    or abs(fields["elapsedMs"] - fields["startElapsedMs"] - fields["durationMs"])
                    > 0.1
                    or (kind == "phase-started" and status != "running")
                    or (kind == "phase-finished" and status not in {"ok", "error", "incomplete"})
                ):
                    gaps.append("invalid-phase-record")
                    continue
                context = details.get("context", {})
                if not isinstance(context, dict):
                    context = {}
                if phase == "payload-verification" and kind == "phase-finished":
                    values = context.get("verification", {})
                    keys = (
                        "totalMs",
                        "metadataMs",
                        "readMs",
                        "hashMs",
                        "manifestFiles",
                        "metadataChecked",
                        "filesVerified",
                        "bytesRead",
                    )
                    if isinstance(values, dict) and all(number(values.get(key)) for key in keys):
                        verification.append(
                            {
                                "phaseId": phase_id,
                                "status": status,
                                "applicable": context.get("applicable") is True,
                                **{key: values[key] for key in keys},
                            }
                        )
                    else:
                        gaps.append("invalid-verification-metrics")
                safe_context = {
                    "service": context.get("service")
                    if context.get("service") in {"bridge", "voice"}
                    else None,
                    "generation": context.get("generation")
                    if isinstance(context.get("generation"), int)
                    else None,
                }
                row = phases.setdefault(
                    phase_id, {"phaseId": phase_id, "phase": phase, "context": safe_context}
                )
                if row["phase"] != phase or row["context"] != safe_context:
                    gaps.append("conflicting-phase-identity")
                    continue
                key = "start" if kind == "phase-started" else "finish"
                if key in row:
                    gaps.append("duplicate-phase-event")
                else:
                    row[key] = {**fields, "status": status}
        if digest(path) != inputs[-1]["sha256"]:
            gaps.append("log-changed-during-read")
    for row in phases.values():
        if "start" not in row or "finish" not in row:
            gaps.append("unpaired-phase")
        elif row["start"]["startElapsedMs"] != row["finish"]["startElapsedMs"]:
            gaps.append("phase-clock-mismatch")
        elif row["finish"]["status"] != "ok":
            gaps.append("unsuccessful-phase")
    observed = {row["phase"] for row in phases.values()}
    missing = sorted(BASE_PHASES - observed)
    bridge_ready = any(
        row["phase"] == "service-readiness"
        and row["context"]["service"] == "bridge"
        and "start" in row
        and row.get("finish", {}).get("status") == "ok"
        for row in phases.values()
    )
    if not bridge_ready:
        gaps.append("bridge-identity-readiness-not-observed")
    return {
        "ok": matched > 0,
        "processSessionId": session_id,
        "inputs": inputs,
        "matchedEvents": matched,
        "observedRuntimeIdentities": runtime_identities,
        "runtimeSourceBinding": "from-log-not-runner-checkout" if runtime_identities else "unknown",
        "phases": list(phases.values()),
        "payloadVerification": verification,
        "frontendMilestones": frontend,
        "frontendReadyObserved": any(row["milestone"] == "frontend-ready" for row in frontend),
        "wallClockMarkers": markers,
        "missingPhases": missing,
        "gaps": sorted(set(gaps)),
        "phaseCoverageComplete": bool(phases) and not missing and not gaps,
        "firstInteractionVerified": False,
        "limitations": [
            "nested/overlapping durations must not be summed",
            "page visibility is not interactive readiness",
            "wall-clock markers cannot be subtracted from monotonic fields",
            "time before the first Rust observer is not measured",
        ],
    }
