from __future__ import annotations

import json
import subprocess

import pytest
import storage_governance as governance
from build_storage import Storage
from build_storage.files import read_json
from paths import create_build_paths, resolve_package_roots


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    roots = resolve_package_roots(
        repo, state_dir=tmp_path / "state", releases_dir=tmp_path / "releases"
    )
    storage = Storage(repo, control=tmp_path / "control")
    monkeypatch.setattr(governance, "Storage", lambda _: storage)
    monkeypatch.setattr(governance, "package_needs", lambda *a, **k: [("work", tmp_path, 1)])
    return roots, storage


def write_identity(paths):
    paths.generated_config_dir.mkdir()
    (paths.generated_config_dir / "build-identity.json").write_text(
        '{"exact":"identity"}', encoding="utf-8"
    )
    (paths.generated_config_dir / "guide-baseline.json").write_text(
        '{"guide":"exact"}', encoding="utf-8"
    )
    (paths.build_dir / "package-timings.json").write_text('{"steps":[]}', encoding="utf-8")


def test_runtime_verify_removes_work_and_keeps_identity_and_guide(scenario):
    roots, storage = scenario
    paths = create_build_paths(roots, build_id="verify-test")
    with governance.build_storage(paths, purpose="runtime-verify", retain=False):
        write_identity(paths)
    governance.finalize(paths, remove=True)
    assert not paths.build_dir.exists()
    reports = list((storage.control / "reports" / storage.repo_id).glob("*.json"))
    report = read_json(reports[0])
    assert json.loads(report["evidence"]["build-identity.json"])["exact"] == "identity"
    assert json.loads(report["evidence"]["guide-baseline.json"])["guide"] == "exact"
    assert paths.cargo_target_dir.parent.exists()


def test_retained_runtime_can_resume_without_losing_previous_evidence(scenario):
    roots, storage = scenario
    paths = create_build_paths(roots, build_id="retained-test")
    with governance.build_storage(paths, purpose="runtime", retain=True):
        write_identity(paths)
    governance.finalize(paths, remove=False)
    row = next(r for r in storage.inventory()["objects"] if r["category"] == "package-build")
    assert row["state"] == "awaiting-resume"
    assert paths.build_dir.is_dir()
    with governance.build_storage(paths, purpose="installer", retain=False):
        assert (paths.generated_config_dir / "guide-baseline.json").exists()
    governance.finalize(paths, remove=True)
    assert not paths.build_dir.exists()
    assert len(list((storage.control / "reports" / storage.repo_id).glob("*.json"))) == 2


def test_failure_preserves_scene_and_report(scenario):
    roots, storage = scenario
    paths = create_build_paths(roots, build_id="failed-test")
    with (
        pytest.raises(RuntimeError, match="test failure"),
        governance.build_storage(paths, purpose="runtime-verify", retain=False),
    ):
        write_identity(paths)
        raise RuntimeError("test failure")
    row = next(r for r in storage.inventory()["objects"] if r["category"] == "package-build")
    assert row["state"] == "failed"
    assert row["retainUntil"]
    report = read_json(next((storage.control / "reports" / storage.repo_id).glob("*.json")))
    assert report["outcome"] == "failed"
    assert paths.build_dir.is_dir()


def test_plan_distinguishes_verification_resume_and_matrix_lifetimes(scenario):
    roots, storage = scenario
    expected = {
        "runtime-verify": "archive-evidence-and-remove-build-work",
        "runtime": "retain-for-resume",
        "installer-smoke-build": "retain-until-matrix-passes",
    }
    for purpose, lifecycle in expected.items():
        report = governance.package_plan(roots, purpose=purpose)
        assert report["purpose"] == purpose
        assert report["lifecycle"]["success"] == lifecycle
        assert report["lifecycle"]["releaseArtifacts"] == "preserved"
        assert report["verification"]["status"] == "not-verified-by-this-plan"
    assert not storage.control.exists()


def test_package_plan_combines_budget_and_space_without_cleanup(scenario):
    roots, storage = scenario
    target = roots.work_root / "build-cache"
    with storage.use(target, "package-cache"):
        (target / "data").write_bytes(b"x" * 100)
    storage.configure({"budgetBytes": 50})
    report = governance.package_plan(roots)
    assert report["space"]["allowed"]
    assert not report["storage"]["budgetSatisfied"]
    assert not report["allowed"]
    assert (target / "data").exists()
    assert governance.preflight(roots)["allowed"]
    assert not target.exists()
