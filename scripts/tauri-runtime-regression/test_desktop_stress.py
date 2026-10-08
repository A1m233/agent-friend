from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "af_desktop_stress_test", Path(__file__).with_name("desktop_stress.py")
)
assert SPEC and SPEC.loader
STRESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STRESS)


def manifest() -> dict:
    return {
        "profile_id": "installer-smoke",
        "build_id": "candidate-123",
        "installer": {"name": "candidate.exe"},
    }


@pytest.mark.parametrize("field,value", [("profile_id", "production"), ("build_id", "other-build")])
def test_candidate_guard_rejects_unrelated_identity(field, value):
    candidate = manifest()
    candidate[field] = value
    with pytest.raises(ValueError):
        STRESS.candidate_guard(candidate, Path("candidate.exe"), "candidate-123")


def test_candidate_guard_requires_exact_installer():
    with pytest.raises(ValueError):
        STRESS.candidate_guard(manifest(), Path("other.exe"), "candidate-123")
    STRESS.candidate_guard(manifest(), Path("candidate.exe"), "candidate-123")


def test_environment_removes_provider_secrets_and_runtime_overrides():
    original = {
        "PATH": "bin",
        "DEEPSEEK_API_KEY": "dummy",
        "OPENROUTER_API_KEY": "dummy",
        "AGENT_FRIEND_DATA_DIR": "real-user-data",
        "VOICE_BRIDGE_VOLC_ACCESS_KEY": "dummy",
        "VOLC_SPEECH_ACCESS_TOKEN": "dummy",
        "OTHER_ACCESS_TOKEN": "dummy",
    }
    cleaned = STRESS.clean_environment(original)
    assert cleaned == {
        "PATH": "bin",
        "AGENT_BRIDGE_IM_ENABLED": "false",
        "AGENT_FRIEND_CONFIG_MODE": "desktop",
    }
    assert original["AGENT_FRIEND_DATA_DIR"] == "real-user-data"


def test_runtime_identity_requires_exact_process_and_build(tmp_path):
    class Process:
        pid = 321

        def poll(self):
            return None

    identity = {
        "runtimeKind": "installer-smoke",
        "profileId": "installer-smoke",
        "buildIdentity": {"buildId": "candidate-123"},
    }
    event = {"processId": 321, "kind": "identity-resolved", "details": identity}
    journal = tmp_path / "runtime.jsonl"
    journal.write_text(json.dumps(event), encoding="utf-8")
    assert STRESS.wait_runtime_identity(Process(), journal, "candidate-123") == event
    with pytest.raises(RuntimeError, match="identity does not match"):
        STRESS.wait_runtime_identity(Process(), journal, "another-candidate")
