from __future__ import annotations

import fnmatch
import importlib.util
import json
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "frontend" / "src-tauri" / "src" / "diagnostics" / "bundle-contract-v1.json"
INSPECTOR_PATH = (
    ROOT
    / ".cursor"
    / "skills"
    / "inspect-agent-friend-diagnostics"
    / "scripts"
    / "inspect_bundle.py"
)


def _load_inspector() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "agent_friend_diagnostic_inspector", INSPECTOR_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSPECTOR = _load_inspector()


def test_command_inventory_is_bounded_to_codes_and_counts() -> None:
    value = {
        "status": "repair_required",
        "events": [
            {"code": "helper_launch_failed", "message": "secret", "path": "private-file"},
            {"code": "secret-code"},
        ],
        "gaps": ["private-gap"],
    }
    summary = INSPECTOR.summarize_command_runtime(value, collected=True)
    assert summary["codes"] == {"helper_launch_failed": 1}
    assert summary["invalidCount"] == 1
    assert "secret" not in json.dumps(summary)
    assert "private" not in json.dumps(summary)
    assert INSPECTOR.summarize_command_runtime(None, collected=False) == {
        "evidence": "not-collected"
    }
    assert (
        INSPECTOR.summarize_command_runtime({"events": [], "status": []}, collected=True)["status"]
        == "unknown"
    )


def test_incident_consumer_is_content_free_and_tolerates_bad_fields() -> None:
    summary = INSPECTOR.summarize_sandbox_incidents(
        {
            "incidents": [
                {
                    "schemaVersion": 1,
                    "outcome": {},
                    "stage": [],
                    "error": {"codes": ["helper_private_secret", "orchestrator_helper_incomplete"]},
                    "message": "private secret",
                }
            ],
            "gaps": None,
        }
    )
    assert summary["codes"] == {"orchestrator_helper_incomplete": 1}
    assert "secret" not in json.dumps(summary)
    assert summary["gapCount"] == 0


def _load_contract() -> dict[str, object]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _contract_manifest_fields(contract: dict[str, object]) -> set[str]:
    core = contract["core"]
    assert isinstance(core, dict)
    capabilities = contract["capabilities"]
    assert isinstance(capabilities, list)
    fields = set(core["manifestFields"])
    for capability in capabilities:
        assert isinstance(capability, dict)
        fields.update(capability["manifestFields"])
    return fields


def _contract_entry_patterns(contract: dict[str, object]) -> list[str]:
    core = contract["core"]
    assert isinstance(core, dict)
    capabilities = contract["capabilities"]
    assert isinstance(capabilities, list)
    patterns = list(core["entryPatterns"])
    for capability in capabilities:
        assert isinstance(capability, dict)
        patterns.extend(capability["entryPatterns"])
    return patterns


def _pet_input_fixture() -> tuple[dict[str, object], dict[str, str]]:
    process = "123-456"
    recorded_at = "2026-09-05T01:02:03Z"
    identity = {
        "buildId": "20260905T010000Z-abcdef1",
        "sourceCommit": "a" * 40,
        "sourceDirty": True,
        "buildMode": "dev",
        "platform": "windows",
        "webviewVersion": "152.0.4191.66",
        "appVersion": "0.0.1",
        "identityComplete": True,
    }
    record = {
        "schemaVersion": 1,
        "processSessionId": process,
        "recordSeq": 2,
        "recordedAt": recorded_at,
        "processMonotonicMs": 200,
        "source": "frontend",
        "kind": "hit-decision",
        "pageInstanceId": "ab12-1234",
        "frontendSeq": 1,
        "monotonicMs": 200,
        "reason": "outside",
        "physical": {"x": 987654.321, "y": 123.456},
        "css": {"x": 790123.4568, "y": 98.7648},
    }
    header = {
        "schemaVersion": 1,
        "kind": "segment-header",
        "source": "collector",
        "processSessionId": process,
        "recordSeq": 2,
        "recordedAt": recorded_at,
        "createdAtMs": 1788570123000,
        "identity": identity,
    }
    incident_summary = {
        "incidentId": "123-456-1",
        "processSessionId": process,
        "pageInstanceId": "ab12-1234",
        "signal": "dpr-mismatch",
        "firstRecordedAt": recorded_at,
        "lastRecordedAt": recorded_at,
        "count": 3,
        "recovery": "unknown",
        "closed": True,
        "truncated": False,
    }
    incident = {
        "schemaVersion": 1,
        "identity": identity,
        **incident_summary,
        "closed": True,
        "firstSignal": record,
        "context": [record],
    }
    entries = {
        "diagnostics/pet-input/history-001.jsonl": "".join(
            json.dumps(item) + "\n" for item in (header, record)
        ),
        "diagnostics/pet-input/incident-001.json": json.dumps(incident),
    }
    artifacts = [
        {
            "entry": entry,
            "kind": "history" if entry.endswith(".jsonl") else "incident",
            "bytes": len(body.encode("utf-8")),
            "processSessionId": process,
            "firstRecordSeq": 2,
            "lastRecordSeq": 2,
            "firstRecordedAt": recorded_at,
            "lastRecordedAt": recorded_at,
            "status": "available",
        }
        for entry, body in entries.items()
    ]
    coverage = {
        "schemaVersion": 1,
        "status": "available",
        "artifacts": artifacts,
        "identities": [{"processSessionId": process, "identity": identity}],
        "counters": {
            "dropped": 3,
            "invalid": 0,
            "rateLimited": 2,
            "writeFailed": 0,
            "evicted": 0,
            "expired": 0,
            "metadataFolded": 0,
            "aggregated": 8,
        },
        "limits": dict(INSPECTOR.PET_INPUT_LIMITS),
        "durability": {
            "scope": "persisted-only",
            "tailCompleteness": "unknown",
            "normalFlushMs": 1000,
            "syncRequestMs": 5000,
        },
        "gaps": [{"kind": "unknown", "scope": "collector"}],
        "retainedIncidents": [incident_summary],
        "exportBytes": sum(item["bytes"] for item in artifacts),
        "failureCount": 0,
    }
    for _ in range(5):
        coverage["exportBytes"] = sum(item["bytes"] for item in artifacts) + len(
            json.dumps(coverage, ensure_ascii=False).encode("utf-8")
        )
    return coverage, entries


def _build_bundle(
    path: Path,
    contract: dict[str, object],
    *,
    extra_capabilities: tuple[str, ...] = (),
    capabilities_override: object | None = None,
) -> None:
    capabilities = contract["capabilities"]
    assert isinstance(capabilities, list)
    capability_ids = [
        capability["id"] for capability in capabilities if isinstance(capability, dict)
    ]
    shell_snapshot = {
        "schemaVersion": 1,
        "capturedAt": "2026-08-01T00:00:01Z",
        "capability": "supported",
        "foregroundHwnd": "0x100",
        "windows": [
            {
                "hwnd": "0x100",
                "roles": ["foreground", "fullscreen-geometry-candidate"],
                "fullscreenGeometryCandidate": True,
            },
            {
                "hwnd": "0x200",
                "roles": ["taskbar-primary"],
                "fullscreenGeometryCandidate": False,
            },
        ],
        "taskbarAutoHide": False,
        "taskbarAlwaysOnTop": True,
        "taskbarAboveForegroundFullscreen": True,
        "probeErrors": [],
    }
    shell_journal = {
        "schemaVersion": 1,
        "sequence": 1,
        "processSessionId": "123-456",
        "recordedAt": "2026-08-01T00:00:02Z",
        "events": [
            {"source": "win-event", "kind": "foreground"},
            {"source": "tauri-window", "kind": "focused"},
        ],
        "truncatedEventCount": 2,
        "droppedEventCount": 3,
        "snapshot": shell_snapshot,
    }
    attachment_summary = {
        "includeOriginalsRequested": False,
        "itemCount": 0,
        "estimatedBytes": 0,
        "includedBytes": 0,
        "skippedBytes": 0,
        "failedBytes": 0,
        "maxOriginalBytes": 200 * 1024 * 1024,
    }
    pet_coverage, pet_entries = _pet_input_fixture()
    manifest = {
        "formatVersion": contract["formatVersion"],
        "schemaRevision": contract["schemaRevision"],
        "capabilities": (
            [*capability_ids, *extra_capabilities]
            if capabilities_override is None
            else capabilities_override
        ),
        "generatedAt": "2026-08-01T00:00:03+00:00",
        "outcome": "complete",
        "requestedSessionIds": ["session-1"],
        "includedSessionIds": ["session-1"],
        "failures": [],
        "excluded": [],
        "logLimits": {
            "maxBytesPerFile": 5 * 1024 * 1024,
            "maxBytesTotal": 20 * 1024 * 1024,
        },
        "logs": [
            {
                "source": "tauri.log",
                "entry": "logs/tauri.log",
                "modifiedAtUnixSeconds": 1,
                "originalBytes": 4,
                "tailStartOffset": 0,
                "includedBytes": 4,
            }
        ],
        "windowsShell": {
            "supported": True,
            "artifacts": [
                "diagnostics/windows-shell/current.json",
                "diagnostics/windows-shell/events-001.jsonl",
            ],
            "failureCount": 0,
        },
        "attachments": attachment_summary,
        "petInputEvidence": pet_coverage,
        "renderEvidence": {
            "supported": True,
            "includeRequested": True,
            "automaticIncidentIncluded": True,
            "pendingCapture": {
                "captureId": "capture-1",
                "capturedAt": "2026-08-01T00:00:02+00:00",
                "trigger": "webview2-gpu-presented-probe",
                "complete": True,
                "webview2Available": True,
                "desktopAvailable": True,
                "errorCount": 0,
            },
            "artifacts": [
                "render-evidence/webview2-pet.png",
                "render-evidence/windows-desktop.png",
            ],
            "failureCount": 0,
            "maxTotalBytes": 64 * 1024 * 1024,
        },
    }
    assert set(manifest) == _contract_manifest_fields(contract)

    json_entries = {
        "manifest.json": manifest,
        "diagnostics/pet-input/coverage.json": pet_coverage,
        "diagnostics/pet-render/coverage.json": {
            "schemaVersion": 1,
            "artifacts": [],
            "gaps": ["not-covered"],
        },
        "diagnostics/application.json": {
            "product": "agent-friend",
            "version": "0.0.0-test",
            "buildMode": "debug",
            "packageBuild": {
                "distribution": "test",
                "buildMode": "test",
                "buildId": "fixture",
                "source": {"commit": "0" * 40, "dirty": False},
            },
        },
        "diagnostics/environment.json": {
            "osType": "Windows",
            "osVersion": "test",
            "architecture": "x86_64",
        },
        "diagnostics/runtime-services.json": {},
        "diagnostics/runtime-config.json": {"items": []},
        "diagnostics/command-runtime.json": {
            "schemaVersion": 1,
            "status": "repair_required",
            "events": [{"code": "helper_launch_failed"}],
            "gaps": [],
        },
        "diagnostics/command-runtime-incidents.json": {
            "schemaVersion": 1,
            "incidents": [
                {
                    "schemaVersion": 1,
                    "id": "fixture",
                    "stage": "start",
                    "outcome": "failed",
                    "error": {"codes": ["orchestrator_helper_incomplete"]},
                }
            ],
            "gaps": [],
        },
        "diagnostics/execution-environments.json": {
            "schemaVersion": 1,
            "status": "ready",
            "providers": [
                {
                    "provider": "python",
                    "status": "ready",
                    "runtimeVersion": "3.12.14",
                    "identity": "0123456789abcdef",
                    "platform": "windows",
                    "architecture": "x86_64",
                    "errorCode": None,
                    "prepareDurationBucket": "1s_to_5s",
                    "sessionOverlays": {
                        "activeCount": 1,
                        "inactiveCount": 2,
                        "totalBytesBucket": "10_to_100_mib",
                    },
                    "sharedCache": {"bytesBucket": "lt_10_mib"},
                    "lastGc": {"status": "not-run"},
                    "runtimePayload": {"declared": True, "validated": True},
                }
            ],
        },
        "diagnostics/desktop-instance.json": {
            "schemaVersion": 1,
            "identity": {
                "attemptId": "diagnostic-snapshot",
                "runtimeKind": "production",
                "instanceDomain": "agent-friend-production",
                "profileId": "production",
                "tauriIdentifier": "com.agentfriend.desktop",
                "buildIdentity": None,
                "executablePath": "<USER_HOME>/agent-friend.exe",
                "installRoot": "<USER_HOME>/agent-friend",
                "worktree": None,
            },
            "owner": {
                "instanceDomain": "agent-friend-production",
                "runtimeKind": "production",
                "pid": 1234,
                "processSessionId": "1234-5678",
                "profileId": "production",
                "tauriIdentifier": "com.agentfriend.desktop",
                "buildIdentity": None,
                "executablePath": "<USER_HOME>/agent-friend.exe",
                "installRoot": "<USER_HOME>/agent-friend",
                "worktree": None,
                "publishedAt": "2026-08-01T00:00:00Z",
            },
        },
        "install/integrity.json": {
            "schemaVersion": 1,
            "status": "consistent",
            "errorCode": None,
            "shell": {
                "available": True,
                "buildId": "fixture",
                "profileId": "production",
                "buildIdentitySha256": "c" * 64,
            },
            "activeInstall": {
                "present": True,
                "parseable": True,
                "buildId": "fixture",
                "profileId": "production",
                "buildIdentitySha256": "c" * 64,
                "payloadManifestSha256": "d" * 64,
                "payloadRelativePath": "packages/fixture",
            },
            "payload": {
                "validated": True,
                "buildId": "fixture",
                "profileId": "production",
                "buildIdentitySha256": "c" * 64,
                "payloadManifestSha256": "d" * 64,
                "runtimeExecutableSha256": "e" * 64,
            },
            "runtime": {
                "observedReadiness": {
                    "bridge": {
                        "service": "bridge",
                        "buildId": "fixture",
                        "profileId": "production",
                        "buildIdentitySha256": "c" * 64,
                        "status": "ready",
                    },
                    "voice": None,
                },
                "jobObject": {"packaged": True, "created": True, "assignedCount": 1},
            },
            "residue": {
                "legacyFixedRuntimePresent": False,
                "incompleteTransactionCount": 0,
            },
        },
        "diagnostics/webview2.json": {
            "schemaVersion": 1,
            "supported": True,
            "runtime": {
                "browserVersion": "147.0.0.0",
                "failureReportFolderPath": "<USER_HOME>/WebView2/Crashpad/reports",
                "environmentRegistrationWindow": "pet",
            },
            "failureReports": [
                {
                    "fileName": "fixture.dmp",
                    "bytes": 4096,
                    "modifiedAtUnixSeconds": 1785523202,
                }
            ],
            "failureReportScanError": None,
            "graphics": {
                "supported": True,
                "dwmCompositionEnabled": True,
                "remoteSession": False,
                "adapters": [{"description": "Fixture GPU"}],
                "errors": [],
            },
            "registeredWindows": ["pet", "chat"],
            "registrationFailures": [],
            "eventBufferLimit": 128,
            "droppedEventCount": 1,
            "events": [
                {
                    "sequence": 2,
                    "capturedAt": "2026-08-01T00:00:02+00:00",
                    "eventType": "process-failed",
                    "windowLabel": "pet",
                    "processFailedKind": "gpu-process-exited",
                    "reason": "crashed",
                    "exitCode": -1,
                    "processDescription": None,
                    "browserProcessExitKind": None,
                    "browserProcessId": None,
                    "visibilityState": None,
                }
            ],
        },
        "diagnostics/webview2-render-evidence.json": {
            "schemaVersion": 1,
            "supported": True,
            "includeRequested": True,
            "automaticIncidentIncluded": True,
            "status": {
                "supported": True,
                "armed": False,
                "waitingForVisibleAfterHidden": False,
                "captureInProgress": False,
                "pending": {
                    "captureId": "capture-1",
                    "capturedAt": "2026-08-01T00:00:02+00:00",
                    "trigger": "webview2-gpu-presented-probe",
                    "complete": True,
                    "webview2Available": True,
                    "desktopAvailable": True,
                    "errorCount": 0,
                },
                "storageError": None,
            },
            "capture": {
                "schemaVersion": 2,
                "captureId": "capture-1",
                "trigger": "webview2-gpu-presented-probe",
                "hiddenDurationMs": 1500,
                "captureStartedAt": "2026-08-01T00:00:01+00:00",
                "captureCompletedAt": "2026-08-01T00:00:02+00:00",
                "startDeltaMs": 12,
                "targetWindow": {"label": "pet"},
                "graphics": {
                    "dwmCompositionEnabled": True,
                    "remoteSession": False,
                    "adapters": [{"description": "Fixture GPU"}],
                },
                "windowsShellSnapshot": {"capability": "supported"},
                "webview2": {
                    "fileName": "webview2-pet.png",
                    "captureStartedUnixMs": 1000,
                    "captureCompletedUnixMs": 1100,
                    "width": 800,
                    "height": 600,
                    "bytes": 3,
                    "sha256": "a" * 64,
                    "pixelMetrics": {
                        "totalPixels": 480000,
                        "transparentPixelCount": 400000,
                        "opaquePixelCount": 80000,
                        "nearBlackOpaquePixelCount": 100,
                        "nearBlackOpaqueRatio": 0.00125,
                        "nearWhiteOpaquePixelCount": 50,
                        "nearWhiteOpaqueRatio": 0.000625,
                        "averageOpaqueLuma": 120.0,
                    },
                },
                "desktop": {
                    "fileName": "windows-desktop.png",
                    "captureStartedUnixMs": 1012,
                    "captureCompletedUnixMs": 1200,
                    "width": 1920,
                    "height": 1080,
                    "bytes": 3,
                    "sha256": "b" * 64,
                    "pixelMetrics": {
                        "totalPixels": 2073600,
                        "transparentPixelCount": 0,
                        "opaquePixelCount": 2073600,
                        "nearBlackOpaquePixelCount": 2000000,
                        "nearBlackOpaqueRatio": 0.9645,
                        "nearWhiteOpaquePixelCount": 100,
                        "nearWhiteOpaqueRatio": 0.000048,
                        "averageOpaqueLuma": 3.0,
                    },
                },
                "errors": [],
                "automaticIncident": True,
                "recoveryGeneration": 1,
                "recoveryAssessment": {
                    "generation": 1,
                    "baselineCaptureId": "baseline-1",
                    "verdict": "unsafe",
                    "reasons": ["presented desktop near-black ratio increased"],
                    "baselineDesktopNearBlackRatio": 0.1,
                    "presentedDesktopNearBlackRatio": 0.9645,
                    "nearBlackRatioDelta": 0.8645,
                    "baselineDesktopNearWhiteRatio": 0.001,
                    "presentedDesktopNearWhiteRatio": 0.000048,
                    "nearWhiteRatioDelta": -0.000952,
                    "baselineWebviewTransparentRatio": 0.833333,
                },
            },
            "includedArtifacts": [
                "render-evidence/webview2-pet.png",
                "render-evidence/windows-desktop.png",
            ],
        },
        "diagnostics/model-catalog.json": {
            "schemaVersion": 1,
            "catalogRevision": "fixture",
            "providers": [{"id": "provider", "models": [{"id": "a"}, {"id": "b"}]}],
        },
        "diagnostics/attachments.json": {
            "formatVersion": 1,
            "summary": attachment_summary,
            "items": [],
        },
        "diagnostics/windows-shell/current.json": shell_snapshot,
        "sessions/session-001.json": {
            "session_id": "session-1",
            "coverage": {
                "schemaVersion": 1,
                "mode": "recent",
                "requestedTurns": 10,
                "includedTurns": 1,
                "completeWithinRequestedRange": True,
                "gaps": [],
            },
            "events": [
                {"type": "user_message", "payload": {"content": "private"}},
                {
                    "type": "provider_reasoning_state",
                    "payload": {
                        "provider": "kimi",
                        "model": "kimi/kimi-k2.7-code",
                        "format": "reasoning_content",
                        "replayScope": "conversation",
                        "redacted": True,
                        "dataCharCount": 24,
                    },
                },
            ],
        },
    }
    text_entries = {
        **pet_entries,
        "diagnostics/windows-shell/events-001.jsonl": json.dumps(shell_journal) + "\n",
        "logs/tauri.log": "safe",
    }
    binary_entries = {
        "render-evidence/webview2-pet.png": b"png",
        "render-evidence/windows-desktop.png": b"png",
    }
    entry_names = {*json_entries, *text_entries, *binary_entries}
    patterns = _contract_entry_patterns(contract)
    assert all(
        any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns) for name in entry_names
    )

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in json_entries.items():
            archive.writestr(name, json.dumps(value, ensure_ascii=False))
        for name, value in text_entries.items():
            archive.writestr(name, value)
        for name, value in binary_entries.items():
            archive.writestr(name, value)


def test_current_producer_capabilities_have_real_inspector_handlers(tmp_path: Path) -> None:
    contract = _load_contract()
    capabilities = contract["capabilities"]
    assert isinstance(capabilities, list)
    capability_ids = {
        capability["id"] for capability in capabilities if isinstance(capability, dict)
    }
    assert capability_ids.issubset(INSPECTOR.CAPABILITY_HANDLERS)

    bundle = tmp_path / "agent-friend-diagnostics-contract-fixture.zip"
    _build_bundle(bundle, contract)
    summary = INSPECTOR.inspect_bundle(bundle, verify_crc=True)

    assert summary["warnings"] == []
    assert summary["manifest"]["schemaRevision"] == contract["schemaRevision"]
    assert summary["manifest"]["unhandledCapabilities"] == []
    assert summary["modelCatalog"]["providerCount"] == 1
    assert summary["modelCatalog"]["modelCount"] == 2
    assert summary["windowsShell"]["current"]["fullscreenCandidateCount"] == 1
    assert summary["windowsShell"]["journal"]["recordCount"] == 1
    assert summary["windowsShell"]["journal"]["eventCount"] == 2
    assert summary["windowsShell"]["journal"]["droppedEventCount"] == 3
    assert summary["windowsShell"]["journal"]["truncatedEventCount"] == 2
    assert summary["webView2"]["runtimeVersion"] == "147.0.0.0"
    assert summary["webView2"]["failureReportCount"] == 1
    assert summary["webView2"]["newestFailureReportUnixSeconds"] == 1785523202
    assert summary["webView2"]["eventCount"] == 1
    assert summary["webView2"]["eventKinds"] == {"process-failed": 1}
    assert summary["webView2"]["processFailureKinds"] == {"gpu-process-exited": 1}
    assert summary["webView2"]["droppedEventCount"] == 1
    assert summary["webView2RenderEvidence"]["captureId"] == "capture-1"
    assert summary["webView2RenderEvidence"]["trigger"] == "webview2-gpu-presented-probe"
    assert summary["webView2RenderEvidence"]["automaticIncident"] is True
    assert summary["webView2RenderEvidence"]["startDeltaMs"] == 12
    assert summary["webView2RenderEvidence"]["webView2Image"]["nearBlackOpaqueRatio"] == 0.00125
    assert summary["webView2RenderEvidence"]["webView2Image"]["nearWhiteOpaqueRatio"] == 0.000625
    assert summary["webView2RenderEvidence"]["desktopImage"]["nearBlackOpaqueRatio"] == 0.9645
    assert summary["webView2RenderEvidence"]["recoveryAssessment"]["verdict"] == "unsafe"
    assert summary["webView2RenderEvidence"]["missingArtifacts"] == []
    assert summary["manifest"]["renderEvidenceSummary"]["artifacts"] == [
        "render-evidence/webview2-pet.png",
        "render-evidence/windows-desktop.png",
    ]
    assert summary["commandRuntime"]["codes"] == {"helper_launch_failed": 1}
    assert summary["installIntegrity"]["status"] == "consistent"
    assert summary["installIntegrity"]["evidence"] == "collected"
    assert summary["installIntegrity"]["payload"]["validated"] is True
    assert summary["installIntegrity"]["runtime"]["jobObjectAssignedCount"] == 1
    assert summary["desktopInstance"]["runtimeKind"] == "production"
    assert summary["desktopInstance"]["instanceDomain"] == "agent-friend-production"
    assert summary["desktopInstance"]["owner"]["pid"] == 1234
    assert summary["executionEnvironments"]["status"] == "ready"
    assert summary["executionEnvironments"]["providerCount"] == 1
    assert summary["executionEnvironments"]["providerStatuses"] == {"ready": 1}
    assert summary["executionEnvironments"]["activeOverlayCount"] == 1
    assert summary["executionEnvironments"]["inactiveOverlayCount"] == 2
    assert summary["executionEnvironments"]["validatedPayloadCount"] == 1
    assert summary["sessions"][0]["providerReasoningStateCount"] == 1
    assert summary["sessions"][0]["providerReasoningRedactedCount"] == 1
    assert summary["sessions"][0]["providerReasoningPayloadLeakCount"] == 0
    assert summary["petInputEvidence"]["evidence"] == "collected"
    assert summary["petInputEvidence"]["historyRecordCount"] == 1
    assert summary["petInputEvidence"]["incidentContextRecordCount"] == 1
    assert summary["petInputEvidence"]["retainedIncidents"][0]["recovery"] == "unknown"
    assert summary["petInputEvidence"]["counters"]["dropped"] == 3
    assert summary["petInputEvidence"]["identities"][0]["identity"]["sourceDirty"] is True


def test_inspector_warns_when_render_evidence_artifact_is_missing(tmp_path: Path) -> None:
    contract = _load_contract()
    complete_bundle = tmp_path / "complete.zip"
    missing_bundle = tmp_path / "missing-render-artifact.zip"
    _build_bundle(complete_bundle, contract)

    with (
        zipfile.ZipFile(complete_bundle, "r") as source,
        zipfile.ZipFile(missing_bundle, "w", compression=zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            if info.filename != "render-evidence/windows-desktop.png":
                target.writestr(info, source.read(info.filename))

    summary = INSPECTOR.inspect_bundle(missing_bundle, verify_crc=True)

    assert summary["webView2RenderEvidence"]["missingArtifacts"] == [
        "render-evidence/windows-desktop.png"
    ]
    assert any("missing artifacts" in warning for warning in summary["warnings"])


def test_inspector_warns_when_bundle_declares_an_unhandled_capability(tmp_path: Path) -> None:
    contract = _load_contract()
    bundle = tmp_path / "agent-friend-diagnostics-future-fixture.zip"
    _build_bundle(bundle, contract, extra_capabilities=("future-evidence-v1",))

    summary = INSPECTOR.inspect_bundle(bundle, verify_crc=False)

    assert summary["manifest"]["unhandledCapabilities"] == ["future-evidence-v1"]
    assert any("future-evidence-v1" in warning for warning in summary["warnings"])


def test_inspector_reports_malformed_capability_list_without_crashing(tmp_path: Path) -> None:
    contract = _load_contract()
    bundle = tmp_path / "agent-friend-diagnostics-malformed-capabilities.zip"
    _build_bundle(bundle, contract, capabilities_override="not-an-array")

    summary = INSPECTOR.inspect_bundle(bundle, verify_crc=False)

    assert summary["manifest"]["capabilities"] == []
    assert "manifest capabilities is not an array" in summary["warnings"]


def test_inspector_accepts_older_v1_without_webview2_capability(tmp_path: Path) -> None:
    contract = _load_contract()
    current_bundle = tmp_path / "current.zip"
    older_bundle = tmp_path / "older-v1.zip"
    _build_bundle(current_bundle, contract)

    with (
        zipfile.ZipFile(current_bundle, "r") as source,
        zipfile.ZipFile(older_bundle, "w", compression=zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            if info.filename in (
                "diagnostics/webview2.json",
                "install/integrity.json",
                "diagnostics/execution-environments.json",
            ) or info.filename.startswith(
                ("diagnostics/webview2-render-evidence.json", "render-evidence/")
            ):
                continue
            data = source.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["schemaRevision"] = 1
                manifest["capabilities"].remove("webview2-process-health-v1")
                for capability in (
                    "webview2-render-evidence-v1",
                    "webview2-render-evidence-v2",
                    "webview2-render-evidence-v3",
                ):
                    if capability in manifest["capabilities"]:
                        manifest["capabilities"].remove(capability)
                manifest["capabilities"].remove("install-integrity-v1")
                manifest["capabilities"].remove("managed-execution-environment-v1")
                manifest.pop("renderEvidence")
                data = json.dumps(manifest).encode()
            target.writestr(info.filename, data)

    summary = INSPECTOR.inspect_bundle(older_bundle, verify_crc=True)

    assert summary["manifest"]["schemaRevision"] == 1
    assert summary["webView2"] is None
    assert summary["webView2RenderEvidence"] is None
    assert summary["installIntegrity"] == {
        "status": "unavailable",
        "evidence": "not-collected",
    }
    assert summary["executionEnvironments"] == {
        "status": "unavailable",
        "evidence": "not-collected",
    }
    assert not any("webview2" in warning.lower() for warning in summary["warnings"])


def test_inspector_accepts_schema_v2_without_render_evidence(tmp_path: Path) -> None:
    contract = _load_contract()
    current_bundle = tmp_path / "current.zip"
    schema_v2_bundle = tmp_path / "schema-v2.zip"
    _build_bundle(current_bundle, contract)

    with (
        zipfile.ZipFile(current_bundle, "r") as source,
        zipfile.ZipFile(schema_v2_bundle, "w", compression=zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            if (
                info.filename == "diagnostics/webview2-render-evidence.json"
                or info.filename == "install/integrity.json"
                or info.filename == "diagnostics/execution-environments.json"
                or info.filename.startswith("render-evidence/")
            ):
                continue
            data = source.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["schemaRevision"] = 2
                for capability in (
                    "webview2-render-evidence-v1",
                    "webview2-render-evidence-v2",
                    "webview2-render-evidence-v3",
                ):
                    if capability in manifest["capabilities"]:
                        manifest["capabilities"].remove(capability)
                manifest["capabilities"].remove("install-integrity-v1")
                manifest["capabilities"].remove("managed-execution-environment-v1")
                manifest.pop("renderEvidence")
                data = json.dumps(manifest).encode()
            target.writestr(info.filename, data)

    summary = INSPECTOR.inspect_bundle(schema_v2_bundle, verify_crc=True)

    assert summary["manifest"]["schemaRevision"] == 2
    assert summary["webView2"]["runtimeVersion"] == "147.0.0.0"
    assert summary["webView2"]["failureReportCount"] == 1
    assert summary["webView2"]["newestFailureReportUnixSeconds"] == 1785523202
    assert summary["webView2RenderEvidence"] is None
    assert summary["installIntegrity"] == {
        "status": "unavailable",
        "evidence": "not-collected",
    }
    assert summary["executionEnvironments"] == {
        "status": "unavailable",
        "evidence": "not-collected",
    }
    assert not any("render evidence" in warning.lower() for warning in summary["warnings"])


def _rewrite_pet_bundle(
    source_path: Path,
    target_path: Path,
    *,
    coverage: object | None = None,
    replacements: dict[str, bytes] | None = None,
    omit: tuple[str, ...] = (),
    old_version: bool = False,
    synchronize_manifest: bool = True,
    manifest_updates: dict[str, object] | None = None,
) -> None:
    with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(target_path, "w") as target:
        if isinstance(coverage, dict) and type(coverage.get("exportBytes")) is int:
            data_bytes = sum(
                len(replacements[info.filename])
                if replacements and info.filename in replacements
                else info.file_size
                for info in source.infolist()
                if info.filename.startswith("diagnostics/pet-input/")
                and info.filename != "diagnostics/pet-input/coverage.json"
                and info.filename not in omit
            )
            for _ in range(5):
                coverage["exportBytes"] = data_bytes + len(json.dumps(coverage).encode())
        for info in source.infolist():
            name = info.filename
            if name in omit or (old_version and name.startswith("diagnostics/pet-input/")):
                continue
            data = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(data)
                if old_version:
                    manifest.pop("petInputEvidence")
                    manifest.pop("schemaRevision")
                    manifest.pop("capabilities")
                elif coverage is not None and synchronize_manifest:
                    manifest["petInputEvidence"] = coverage
                if manifest_updates:
                    manifest.update(manifest_updates)
                data = json.dumps(manifest).encode()
            elif name == "diagnostics/pet-input/coverage.json" and coverage is not None:
                data = json.dumps(coverage).encode()
            if replacements and name in replacements:
                data = replacements[name]
            target.writestr(name, data)


def test_pet_input_old_v1_without_capabilities_is_not_corruption(tmp_path: Path) -> None:
    current = tmp_path / "current.zip"
    old = tmp_path / "old.zip"
    _build_bundle(current, _load_contract())
    _rewrite_pet_bundle(current, old, old_version=True)

    summary = INSPECTOR.inspect_bundle(old, verify_crc=True)

    assert summary["petInputEvidence"] == {"evidence": "not-collected"}
    assert summary["manifest"]["schemaRevision"] is None
    assert not any("pet input" in warning for warning in summary["warnings"])


def test_pet_input_missing_declared_coverage_is_an_explicit_gap(tmp_path: Path) -> None:
    current = tmp_path / "current.zip"
    missing = tmp_path / "missing.zip"
    _build_bundle(current, _load_contract())
    _rewrite_pet_bundle(current, missing, omit=("diagnostics/pet-input/coverage.json",))

    summary = INSPECTOR.inspect_bundle(missing, verify_crc=True)

    assert summary["petInputEvidence"]["evidence"] == "missing"
    assert any("required coverage" in warning for warning in summary["warnings"])
    assert summary["modelCatalog"]["modelCount"] == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "healthy"),
        ("artifacts", {}),
        ("identities", "private text"),
        ("counters", {"dropped": True}),
        ("limits", {"totalBytes": 999999999}),
        ("gaps", [{"kind": {"text": "secret"}}]),
        ("failureCount", -1),
        ("exportBytes", False),
        ("retainedIncidents", [{}]),
        ("durability", {"scope": "live-all-frames"}),
    ],
)
def test_pet_input_malformed_metadata_is_not_silently_coerced(
    tmp_path: Path, field: str, value: object
) -> None:
    current = tmp_path / "current.zip"
    malformed = tmp_path / "malformed.zip"
    _build_bundle(current, _load_contract())
    coverage, _ = _pet_input_fixture()
    coverage[field] = value
    _rewrite_pet_bundle(current, malformed, coverage=coverage)

    summary = INSPECTOR.inspect_bundle(malformed, verify_crc=True)

    assert summary["petInputEvidence"]["evidence"] == "invalid"
    assert any("pet input" in warning for warning in summary["warnings"])
    assert "private text" not in json.dumps(summary)
    assert "secret" not in json.dumps(summary)


def test_pet_input_missing_artifact_and_manifest_mismatch_are_visible(tmp_path: Path) -> None:
    current = tmp_path / "current.zip"
    mismatch = tmp_path / "mismatch.zip"
    _build_bundle(current, _load_contract())
    coverage, _ = _pet_input_fixture()
    coverage["failureCount"] = 1
    _rewrite_pet_bundle(
        current,
        mismatch,
        coverage=coverage,
        synchronize_manifest=False,
        omit=("diagnostics/pet-input/incident-001.json",),
    )

    summary = INSPECTOR.inspect_bundle(mismatch, verify_crc=True)

    assert summary["petInputEvidence"]["evidence"] == "invalid"
    assert summary["petInputEvidence"]["missingReferencedEntries"] == [
        "diagnostics/pet-input/incident-001.json"
    ]
    assert any("manifest and coverage" in warning for warning in summary["warnings"])


def test_pet_input_inventory_does_not_dump_coordinates_or_additive_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    current = tmp_path / "current.zip"
    future = tmp_path / "additive.zip"
    _build_bundle(current, _load_contract())
    coverage, _ = _pet_input_fixture()
    coverage["futurePrivateBody"] = {"text": "NEVER_DUMP_PET_PAYLOAD"}
    coverage["counters"]["NEVER_DUMP_PET_COUNTER"] = 7
    coverage["identities"][0]["identity"]["path"] = "NEVER_DUMP_PET_PATH"
    _rewrite_pet_bundle(current, future, coverage=coverage)

    summary = INSPECTOR.inspect_bundle(future, verify_crc=True)
    INSPECTOR.print_human(summary)
    output = json.dumps(summary) + capsys.readouterr().out

    assert summary["petInputEvidence"]["evidence"] == "collected"
    assert "987654.321" not in output
    assert "790123.4568" not in output
    assert "NEVER_DUMP" not in output


@pytest.mark.parametrize("damage", ["partial-line", "invalid-json", "wrong-process"])
def test_pet_input_history_validation_preserves_other_evidence(tmp_path: Path, damage: str) -> None:
    current = tmp_path / "current.zip"
    broken = tmp_path / "broken.zip"
    _build_bundle(current, _load_contract())
    coverage, entries = _pet_input_fixture()
    entry = "diagnostics/pet-input/history-001.jsonl"
    data = entries[entry].encode()
    if damage == "partial-line":
        data = data[:-1]
    elif damage == "invalid-json":
        data += b"{invalid}\n"
    else:
        data = data.replace(b'"123-456"', b'"123-999"')
    coverage["artifacts"][0]["bytes"] = len(data)
    _rewrite_pet_bundle(current, broken, coverage=coverage, replacements={entry: data})

    summary = INSPECTOR.inspect_bundle(broken, verify_crc=True)

    assert summary["petInputEvidence"]["invalidArtifactCount"] == 1
    assert summary["petInputEvidence"]["incidentContextRecordCount"] == 1
    assert summary["petInputEvidence"]["evidence"] == "invalid"


def test_pet_input_empty_directory_coverage_is_valid_without_history(tmp_path: Path) -> None:
    current = tmp_path / "current.zip"
    empty = tmp_path / "empty.zip"
    _build_bundle(current, _load_contract())
    coverage, entries = _pet_input_fixture()
    coverage.update(
        {
            "status": "not-collected",
            "artifacts": [],
            "identities": [],
            "retainedIncidents": [],
            "exportBytes": 0,
            "gaps": [{"kind": "not-collected", "scope": "collector"}],
        }
    )
    _rewrite_pet_bundle(current, empty, coverage=coverage, omit=tuple(entries))

    summary = INSPECTOR.inspect_bundle(empty, verify_crc=True)

    assert summary["petInputEvidence"]["evidence"] == "collected"
    assert summary["petInputEvidence"]["status"] == "not-collected"
    assert summary["petInputEvidence"]["historyRecordCount"] == 0
    assert summary["warnings"] == []


def test_pet_input_rejects_excessive_artifact_count_before_parsing(tmp_path: Path) -> None:
    bundle = tmp_path / "too-many.zip"
    _build_bundle(bundle, _load_contract())
    with zipfile.ZipFile(bundle, "a") as archive:
        for index in range(513):
            archive.writestr(f"diagnostics/pet-input/history-{index + 1000}.jsonl", b"{}\n")

    summary = INSPECTOR.inspect_bundle(bundle, verify_crc=False)

    assert summary["petInputEvidence"]["evidence"] == "invalid"
    assert any("file-count or total-byte limit" in warning for warning in summary["warnings"])


def test_pet_input_missing_durability_counter_stays_unknown(tmp_path: Path) -> None:
    current = tmp_path / "current.zip"
    unknown = tmp_path / "unknown.zip"
    _build_bundle(current, _load_contract())
    coverage, _ = _pet_input_fixture()
    coverage["counters"] = {}
    coverage["durability"].update(
        {
            "normalEnd": None,
            "lastWritten": {
                "processSessionId": "123-456",
                "recordSeq": 2,
                "recordedAt": "2026-09-05T01:02:03Z",
            },
            "lastSyncRequested": None,
        }
    )
    _rewrite_pet_bundle(current, unknown, coverage=coverage)

    summary = INSPECTOR.inspect_bundle(unknown, verify_crc=True)["petInputEvidence"]

    assert summary["evidence"] == "collected"
    assert "dropped" not in summary["counters"]
    assert summary["durability"]["normalEnd"] is None
    assert summary["durability"]["lastSyncRequested"] is None
    assert summary["durability"]["lastWritten"]["recordSeq"] == 2


def test_pet_input_native_incident_can_have_unknown_page_identity(tmp_path: Path) -> None:
    current = tmp_path / "current.zip"
    unknown = tmp_path / "native-incident.zip"
    _build_bundle(current, _load_contract())
    coverage, entries = _pet_input_fixture()
    coverage["retainedIncidents"][0]["pageInstanceId"] = None
    entry = "diagnostics/pet-input/incident-001.json"
    incident = json.loads(entries[entry])
    incident["pageInstanceId"] = None
    incident["firstSignal"].pop("pageInstanceId")
    incident["firstSignal"]["source"] = "native"
    data = json.dumps(incident).encode()
    coverage["artifacts"][1]["bytes"] = len(data)
    _rewrite_pet_bundle(current, unknown, coverage=coverage, replacements={entry: data})

    summary = INSPECTOR.inspect_bundle(unknown, verify_crc=True)["petInputEvidence"]

    assert summary["evidence"] == "collected"
    assert summary["retainedIncidents"][0]["pageInstanceId"] is None


def test_pet_input_window_event_is_valid_without_dumping_geometry(tmp_path: Path) -> None:
    current = tmp_path / "current.zip"
    observed = tmp_path / "window-event.zip"
    _build_bundle(current, _load_contract())
    coverage, entries = _pet_input_fixture()
    entry = "diagnostics/pet-input/history-001.jsonl"
    records = [json.loads(line) for line in entries[entry].splitlines()]
    event = {
        "schemaVersion": 1,
        "processSessionId": "123-456",
        "recordSeq": 3,
        "ingressSeq": 4,
        "recordedAt": "2026-09-05T01:02:04Z",
        "processMonotonicMs": 1000,
        "source": "native",
        "kind": "window-observed",
        "windowScaleFactor": 1.25,
        "windowSize": {"width": 5120, "height": 1600},
        "windowOrigin": {"x": -2560, "y": 0},
    }
    data = "".join(json.dumps(record) + "\n" for record in [*records, event]).encode()
    coverage["artifacts"][0].update(
        {
            "bytes": len(data),
            "lastRecordSeq": 3,
            "lastRecordedAt": event["recordedAt"],
        }
    )
    _rewrite_pet_bundle(current, observed, coverage=coverage, replacements={entry: data})

    summary = INSPECTOR.inspect_bundle(observed, verify_crc=True)["petInputEvidence"]

    assert summary["evidence"] == "collected"
    assert summary["historyRecordCount"] == 2
    assert "windowOrigin" not in json.dumps(summary)


def test_pet_input_retains_bounded_eviction_and_noncontiguous_drop_evidence(tmp_path: Path) -> None:
    current = tmp_path / "current.zip"
    retained = tmp_path / "retention.zip"
    _build_bundle(current, _load_contract())
    coverage, _ = _pet_input_fixture()
    coverage["counters"]["frontendDropped"] = 3
    coverage["evictions"] = [
        {
            "kind": "evicted",
            "scope": "history",
            "bytes": 1024,
            "createdAtMs": 1788570123000,
            "processSessionId": "123-400",
            "firstRecordSeq": None,
            "path": "NEVER_DUMP_EVICTED_PATH",
        }
    ]
    coverage["durability"]["lastDroppedRange"] = {
        "processSessionId": "123-456",
        "firstIngressSeq": 5,
        "lastIngressSeq": 20,
        "interior": "not-necessarily-contiguous",
        "body": "NEVER_DUMP_DROP_BODY",
    }
    _rewrite_pet_bundle(current, retained, coverage=coverage)

    summary = INSPECTOR.inspect_bundle(retained, verify_crc=True)["petInputEvidence"]

    assert summary["evidence"] == "collected"
    assert summary["counters"]["frontendDropped"] == 3
    assert summary["evictions"][0]["firstRecordSeq"] is None
    assert summary["durability"]["lastDroppedRange"]["interior"] == "not-necessarily-contiguous"
    assert "NEVER_DUMP" not in json.dumps(summary)


@pytest.mark.parametrize("field", ["evictions", "lastDroppedRange"])
def test_pet_input_rejects_malformed_retention_positions(tmp_path: Path, field: str) -> None:
    current = tmp_path / "current.zip"
    malformed = tmp_path / "retention-invalid.zip"
    _build_bundle(current, _load_contract())
    coverage, _ = _pet_input_fixture()
    if field == "evictions":
        coverage["evictions"] = [{"kind": "expired", "createdAtMs": False}]
    else:
        coverage["durability"]["lastDroppedRange"] = {
            "processSessionId": "123-456",
            "firstIngressSeq": 20,
            "lastIngressSeq": 5,
            "interior": "not-necessarily-contiguous",
        }
    _rewrite_pet_bundle(current, malformed, coverage=coverage)

    summary = INSPECTOR.inspect_bundle(malformed, verify_crc=True)

    assert summary["petInputEvidence"]["evidence"] == "invalid"
    assert any("pet input coverage" in warning for warning in summary["warnings"])


def test_pet_input_export_time_write_failure_does_not_replace_missing_history(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current.zip"
    failed = tmp_path / "writer-failed.zip"
    _build_bundle(current, _load_contract())
    snapshot = {
        "scope": "export-time",
        "processSessionId": "456-789",
        "queuedBytes": 0,
        "dropped": 0,
        "invalid": 0,
        "writeFailed": 2,
        "workerStopped": True,
        "secret": "NEVER_DUMP_CURRENT_SECRET",
        "geometry": {"x": 987654.321, "y": 123.456},
    }
    with zipfile.ZipFile(current, "a") as archive:
        archive.writestr("diagnostics/pet-input/current.json", json.dumps(snapshot))
    coverage, entries = _pet_input_fixture()
    coverage.update(
        {
            "status": "not-collected",
            "artifacts": [],
            "identities": [],
            "retainedIncidents": [],
            "exportBytes": 0,
            "failureCount": 1,
            "gaps": [
                {"kind": "not-collected", "scope": "collector"},
                {"kind": "write-failed", "scope": "collector", "count": 2},
            ],
        }
    )
    _rewrite_pet_bundle(
        current,
        failed,
        coverage=coverage,
        omit=tuple(entries),
        manifest_updates={
            "outcome": "partial",
            "failures": [
                {
                    "collection": "pet-input-evidence",
                    "item": "current",
                    "message": "write-failed",
                }
            ],
        },
    )

    summary = INSPECTOR.inspect_bundle(failed, verify_crc=True)
    evidence = summary["petInputEvidence"]

    assert evidence["evidence"] == "collected"
    assert evidence["status"] == "not-collected"
    assert evidence["historyRecordCount"] == 0
    assert evidence["identities"] == []
    assert evidence["current"]["processSessionId"] == "456-789"
    assert evidence["current"]["writeFailed"] == 2
    assert evidence["current"]["workerStopped"] is True
    assert summary["manifest"]["outcome"] == "partial"
    assert summary["warnings"] == []
    assert "NEVER_DUMP" not in json.dumps(summary)
    assert "987654.321" not in json.dumps(summary)


@pytest.mark.parametrize(
    "field,value",
    [
        ("queuedBytes", -1),
        ("dropped", False),
        ("invalid", 1.5),
        ("writeFailed", "secret"),
        ("workerStopped", 0),
        ("processSessionId", "private-path"),
    ],
)
def test_pet_input_rejects_invalid_export_time_snapshot_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    current = tmp_path / "current.zip"
    malformed = tmp_path / "current-invalid.zip"
    _build_bundle(current, _load_contract())
    snapshot = {
        "scope": "export-time",
        "processSessionId": "456-789",
        "queuedBytes": 0,
        "dropped": 0,
        "invalid": 0,
        "writeFailed": 0,
        "workerStopped": False,
        field: value,
    }
    with zipfile.ZipFile(current, "a") as archive:
        archive.writestr("diagnostics/pet-input/current.json", json.dumps(snapshot))
    coverage, _ = _pet_input_fixture()
    _rewrite_pet_bundle(current, malformed, coverage=coverage)

    summary = INSPECTOR.inspect_bundle(malformed, verify_crc=True)

    assert summary["petInputEvidence"]["evidence"] == "invalid"
    assert summary["petInputEvidence"]["current"] == {"evidence": "invalid"}
    assert summary["petInputEvidence"]["historyRecordCount"] == 1
    assert "secret" not in json.dumps(summary)


def test_pet_render_inventory_counts_cross_process_incident_without_printing_body(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "render.zip"
    name = "diagnostics/pet-render/incident-001.json"
    records = [
        {
            "schemaVersion": 1,
            "processSessionId": session,
            "recordedAt": 1000,
            "recordSeq": 1,
            "event": {"private": "DO_NOT_PRINT"},
        }
        for session in ["old", "new"]
    ]
    incident = json.dumps({"schemaVersion": 1, "signal": "user-reported", "records": records})
    coverage = {
        "schemaVersion": 1,
        "gaps": ["dropped"],
        "dropped": 2,
        "artifacts": [{"entry": name, "bytes": len(incident.encode())}],
    }
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(name, incident)
        archive.writestr("diagnostics/pet-render/coverage.json", json.dumps(coverage))
    with zipfile.ZipFile(bundle) as archive:
        warnings: list[str] = []
        result = INSPECTOR.summarize_pet_render(
            archive, {"capabilities": ["pet-render-evidence-v1"]}, warnings
        )
    assert result["processCount"] == 2
    assert result["signals"] == {"user-reported": 1}
    assert result["presentation"] == "unknown"
    assert "DO_NOT_PRINT" not in json.dumps(result)
    assert warnings == []


def test_pet_render_missing_artifact_and_older_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "render.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(
            "diagnostics/pet-render/coverage.json",
            json.dumps(
                {
                    "schemaVersion": 1,
                    "artifacts": [
                        {"entry": "diagnostics/pet-render/history-001.jsonl", "bytes": 100}
                    ],
                    "gaps": [],
                }
            ),
        )
    with zipfile.ZipFile(bundle) as archive:
        warnings: list[str] = []
        INSPECTOR.summarize_pet_render(archive, {}, warnings)
        assert "pet render missing artifact" in warnings
    with zipfile.ZipFile(bundle, "w"):
        pass
    with zipfile.ZipFile(bundle) as archive:
        assert INSPECTOR.summarize_pet_render(archive, {}, []) == {"evidence": "not-collected"}
