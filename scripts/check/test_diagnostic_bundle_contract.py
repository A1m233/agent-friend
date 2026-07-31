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
    entry_names = {*json_entries, *text_entries}
    patterns = _contract_entry_patterns(contract)
    assert all(
        any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns) for name in entry_names
    )

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in json_entries.items():
            archive.writestr(name, json.dumps(value, ensure_ascii=False))
        for name, value in text_entries.items():
            archive.writestr(name, value)


def test_current_producer_capabilities_have_real_inspector_handlers(tmp_path: Path) -> None:
    contract = _load_contract()
    capabilities = contract["capabilities"]
    assert isinstance(capabilities, list)
    capability_ids = {
        capability["id"] for capability in capabilities if isinstance(capability, dict)
    }
    assert set(INSPECTOR.CAPABILITY_HANDLERS) == capability_ids

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
