from __future__ import annotations

import fnmatch
import importlib.util
import json
import zipfile
from pathlib import Path
from types import ModuleType

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
        "renderEvidence": {
            "supported": True,
            "includeRequested": True,
            "automaticIncidentIncluded": False,
            "pendingCapture": {
                "captureId": "capture-1",
                "capturedAt": "2026-08-01T00:00:02+00:00",
                "trigger": "pet-page-visible-after-hidden",
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
            "automaticIncidentIncluded": False,
            "status": {
                "supported": True,
                "armed": False,
                "waitingForVisibleAfterHidden": False,
                "captureInProgress": False,
                "pending": {
                    "captureId": "capture-1",
                    "capturedAt": "2026-08-01T00:00:02+00:00",
                    "trigger": "pet-page-visible-after-hidden",
                    "complete": True,
                    "webview2Available": True,
                    "desktopAvailable": True,
                    "errorCount": 0,
                },
                "storageError": None,
            },
            "capture": {
                "schemaVersion": 1,
                "captureId": "capture-1",
                "trigger": "pet-page-visible-after-hidden",
                "hiddenDurationMs": 30000,
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
                        "averageOpaqueLuma": 3.0,
                    },
                },
                "errors": [],
                "automaticIncident": False,
                "recoveryGeneration": None,
                "recoveryAssessment": None,
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
            "events": [{"type": "user_message", "payload": {"content": "private"}}],
        },
    }
    text_entries = {
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
    assert summary["webView2RenderEvidence"]["startDeltaMs"] == 12
    assert summary["webView2RenderEvidence"]["webView2Image"]["nearBlackOpaqueRatio"] == 0.00125
    assert summary["webView2RenderEvidence"]["desktopImage"]["nearBlackOpaqueRatio"] == 0.9645
    assert summary["webView2RenderEvidence"]["missingArtifacts"] == []
    assert summary["manifest"]["renderEvidenceSummary"]["artifacts"] == [
        "render-evidence/webview2-pet.png",
        "render-evidence/windows-desktop.png",
    ]
    assert summary["installIntegrity"]["status"] == "consistent"
    assert summary["installIntegrity"]["evidence"] == "collected"
    assert summary["installIntegrity"]["payload"]["validated"] is True
    assert summary["installIntegrity"]["runtime"]["jobObjectAssignedCount"] == 1
    assert summary["desktopInstance"]["runtimeKind"] == "production"
    assert summary["desktopInstance"]["instanceDomain"] == "agent-friend-production"
    assert summary["desktopInstance"]["owner"]["pid"] == 1234


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
                ):
                    if capability in manifest["capabilities"]:
                        manifest["capabilities"].remove(capability)
                manifest["capabilities"].remove("install-integrity-v1")
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
                ):
                    if capability in manifest["capabilities"]:
                        manifest["capabilities"].remove(capability)
                manifest["capabilities"].remove("install-integrity-v1")
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
    assert not any("render evidence" in warning.lower() for warning in summary["warnings"])
