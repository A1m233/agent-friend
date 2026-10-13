from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "_lib"))

from build_storage import Storage
from build_storage.execution import admit, use_target
from build_storage.files import MARKER, identity, read_json, write_json
from build_storage.locks import Busy, lock
from build_storage.policy import collect, plan, space_plan
from build_storage.reservations import reserve

LIB = Path(__file__).resolve().parents[1] / "_lib"


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return Storage(repo, control=tmp_path / "control", common=repo / ".git")


def object_with_bytes(storage: Storage, tmp_path: Path, name: str, size: int = 100) -> dict:
    path = tmp_path / name
    with storage.use(path, "package-cache"):
        (path / "data").write_bytes(b"x" * size)
    return read_json(storage.record_path(identity(path)))


def test_fast_inventory_is_read_only_and_does_not_scan(storage, tmp_path, monkeypatch):
    assert not storage.control.exists()
    assert storage.inventory()["objects"] == []
    assert not storage.control.exists()
    object_with_bytes(storage, tmp_path, "cache")
    monkeypatch.setattr(
        "build_storage.store.tree_size", lambda _: pytest.fail("fast inventory scanned")
    )
    assert storage.inventory()["objects"][0]["bytes"] == 100


def test_capacity_collects_only_owned_cache_preserving_installer(storage, tmp_path):
    object_with_bytes(storage, tmp_path, "cache", 100)
    installer = tmp_path / "releases" / "setup.exe"
    installer.parent.mkdir()
    installer.write_bytes(b"installer")
    storage.configure({"budgetBytes": 50, "lowWaterBytes": 1})
    result = collect(storage, plan(storage))
    assert len(result["deleted"]) == 1
    assert installer.read_bytes() == b"installer"
    assert result["postPlan"]["budgetSatisfied"]


@pytest.mark.parametrize("protection", ["pin", "retain", "running"])
def test_budget_never_overrides_protection(storage, tmp_path, protection):
    record = object_with_bytes(storage, tmp_path, "cache")
    if protection == "pin":
        storage.pin(record["id"], "keep")
    else:
        record["retainUntil"] = time.time() + 3600 if protection == "retain" else None
        record["state"] = "running" if protection == "running" else "idle"
        write_json(storage.record_path(record["id"]), record)
    storage.configure({"budgetBytes": 1, "lowWaterBytes": 1})
    result = plan(storage)
    assert not result["budgetSatisfied"]
    assert not result["candidates"]
    assert len(result["protected"]) == 1


def test_unknown_size_is_not_zero(storage, tmp_path):
    storage.register(tmp_path / "cache", "package-cache")
    result = plan(storage)
    assert result["unknownObjects"]
    assert not result["budgetSatisfied"]
    assert not result["candidates"]


def test_failed_operation_retained_and_scannable(storage, tmp_path):
    with (
        pytest.raises(RuntimeError, match="failure"),
        storage.use(tmp_path / "build", "package-build"),
    ):
        raise RuntimeError("failure")
    row = storage.inventory()["objects"][0]
    assert row["state"] == "failed"
    assert 47 * 3600 < row["retainUntil"] - time.time() <= 48 * 3600


def test_interruption_is_not_reaped_by_dead_pid(storage, tmp_path):
    with pytest.raises(KeyboardInterrupt), storage.use(tmp_path / "build", "package-build"):
        raise KeyboardInterrupt
    row = storage.inventory()["objects"][0]
    assert row["state"] == "running"
    with pytest.raises(Busy):
        storage.delete(row)


def test_delete_revalidates_a_pin_set_after_preview(storage, tmp_path):
    record = object_with_bytes(storage, tmp_path, "cache")
    storage.configure({"budgetBytes": 1, "lowWaterBytes": 1})
    preview = plan(storage)
    storage.pin(record["id"], "new protection")
    result = collect(storage, preview)
    assert len(result["skipped"]) == 1
    assert (tmp_path / "cache/data").exists()


def test_markers_must_match_and_nonempty_unknown_is_not_adopted(storage, tmp_path):
    target = tmp_path / "unknown"
    target.mkdir()
    (target / "important").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="adoption"):
        storage.register(target, "package-cache")
    with use_target(storage, target, "package-cache") as row:
        assert row is None
    assert not (target / MARKER).exists()
    record = object_with_bytes(storage, tmp_path, "cache")
    (tmp_path / "cache" / MARKER).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        storage.delete(record)
    assert (target / "important").is_file()


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_invalid_config_does_not_replace_valid_config(storage, value):
    storage.configure({"budgetBytes": 123})
    with pytest.raises(ValueError):
        storage.configure({"budgetBytes": value})
    assert storage.config["budgetBytes"] == 123


def test_new_root_does_not_hide_old_objects(storage, tmp_path):
    object_with_bytes(storage, tmp_path, "old")
    storage.configure({"buildRoot": str(tmp_path / "new")})
    assert storage.target("dev").is_relative_to(tmp_path / "new")
    assert storage.inventory()["objects"][0]["path"] == str(tmp_path / "old")


def test_source_control_and_overlapping_objects_are_rejected(storage, tmp_path):
    for path in (storage.repo, storage.repo / "target", storage.control, tmp_path):
        with pytest.raises(ValueError):
            storage.register(path, "dev")
    storage.register(tmp_path / "outer", "dev")
    with pytest.raises(ValueError, match="overlapping"):
        storage.register(tmp_path / "outer/inner", "dev")


def test_scan_cancelled_is_incomplete(storage, tmp_path, monkeypatch):
    storage.register(tmp_path / "cache", "dev")

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr("build_storage.store.tree_size", interrupt)
    assert storage.inventory(scan=True)["complete"] is False


def test_partial_delete_failure_retains_marker_for_retry(storage, tmp_path, monkeypatch):
    record = object_with_bytes(storage, tmp_path, "cache")
    original = Path.unlink

    def denied(path, *args, **kwargs):
        if path.name == "data":
            raise PermissionError("busy")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(Path, "unlink", denied)
        with pytest.raises(PermissionError):
            storage.delete(record)
    updated = read_json(storage.record_path(record["id"]))
    assert updated["state"] == "cleanup-failed"
    assert (tmp_path / "cache" / MARKER).exists()
    storage.delete(updated)
    assert not (tmp_path / "cache").exists()


def test_same_volume_demands_sum_and_separate_volumes_do_not(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "build_storage.policy.volume", lambda p: ("a" if p.name != "other" else "b", 100)
    )
    result = space_plan([("work", tmp_path / "work", 60), ("release", tmp_path / "release", 60)])
    assert not result["allowed"]
    assert result["volumes"][0]["shortfallBytes"] == 20
    assert space_plan([("work", tmp_path / "work", 60), ("release", tmp_path / "other", 60)])[
        "allowed"
    ]


def test_reservations_prevent_double_admission(storage, tmp_path, monkeypatch):
    monkeypatch.setattr("build_storage.policy.volume", lambda _: ("same", 100))
    with (
        reserve(storage, [("first", tmp_path, 60)]),
        pytest.raises(RuntimeError, match="unreserved"),
        reserve(storage, [("second", tmp_path, 60)]),
    ):
        pytest.fail("overcommitted volume")
    assert not list((storage.control / "reservations").glob("*.json"))


def test_cross_process_object_lock_protects_directory(storage, tmp_path):
    path = storage.object_lock(identity(tmp_path / "cache"))
    code = f"import sys;sys.path.insert(0,{str(LIB)!r});from pathlib import Path;from build_storage.locks import lock\nwith lock(Path({str(path)!r})): print('locked',flush=True);input()"
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(Busy), lock(path):
            pytest.fail("lock was not exclusive")
    finally:
        child.communicate("\n", timeout=10)
    with lock(path):
        pass


def test_archive_survives_cache_deletion(storage, tmp_path):
    record = object_with_bytes(storage, tmp_path, "cache")
    report = storage.archive(record, "passed", [])
    storage.delete(record)
    assert read_json(report)["outcome"] == "passed"


def test_archive_finalization_does_not_erase_prior_evidence(storage, tmp_path):
    record = object_with_bytes(storage, tmp_path, "cache")
    evidence = tmp_path / "identity.json"
    evidence.write_text('{"build":"exact"}', encoding="utf-8")
    report = storage.archive(record, "passed", [evidence])
    storage.archive(record, "passed", [])
    assert read_json(report)["evidence"]["identity.json"] == evidence.read_text(encoding="utf-8")


def test_reconcile_requires_explicit_operation_and_preserves_failed_scene(storage, tmp_path):
    with (
        pytest.raises(KeyboardInterrupt),
        reserve(storage, [("work", tmp_path, 1)]),
        storage.use(tmp_path / "build", "package-build"),
    ):
        raise KeyboardInterrupt
    row = storage.inventory()["objects"][0]
    assert row["operationId"] == storage.inventory()["reservations"][0]["operationId"]
    storage.reconcile(row["operationId"], reason="operator verified all child processes stopped")
    current = storage.inventory()
    assert not current["reservations"]
    assert current["objects"][0]["state"] == "failed"
    assert current["objects"][0]["retainUntil"] > time.time()


def test_execute_cli_preserves_cwd_arguments_and_records_target(tmp_path):
    import json

    repo = Path(__file__).resolve().parents[2]
    cli = repo / "scripts/build-storage/execute.py"
    target = tmp_path / "target"
    env = {
        **os.environ,
        "LOCALAPPDATA": str(tmp_path / "local"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
    }
    code = "import os,pathlib;pathlib.Path(os.environ['CARGO_TARGET_DIR'],'built').write_text(os.getcwd())"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(cli),
            "--cwd",
            str(tmp_path),
            "--target-dir",
            str(target),
            "dev",
            "--",
            sys.executable,
            "-c",
            code,
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (target / "built").read_text() == str(tmp_path)
    assert (target / MARKER).exists()
    assert json.loads((target / MARKER).read_text())["category"] == "dev"


def test_package_management_runs_without_uv_or_virtual_environment(tmp_path):
    import json

    repo = Path(__file__).resolve().parents[2]
    original = (repo / ".venv").exists()
    env = {
        **os.environ,
        "LOCALAPPDATA": str(tmp_path / "local"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
    }
    # Installed Python directly dispatches management; no uv or dependency group is entered.
    result = subprocess.run(
        [sys.executable, "-B", str(repo / "scripts/package/dispatch.py"), "workspace-status"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["objects"] == []
    assert (repo / ".venv").exists() == original
    assert not (tmp_path / "local").exists()


def test_junction_or_symlink_is_not_recursively_deleted(storage, tmp_path):
    record = object_with_bytes(storage, tmp_path, "cache")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("data", encoding="utf-8")
    link = tmp_path / "cache/link"
    if os.name == "nt":
        # A directory junction needs no developer-mode symlink privilege.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
        )
        if result.returncode:
            pytest.skip("junction creation unavailable")
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="linked"):
            storage.delete(record)
        assert (outside / "keep").exists()
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_admission_automatically_reclaims_only_enough_for_budget(storage, tmp_path):
    first = object_with_bytes(storage, tmp_path, "old", 100)
    second = object_with_bytes(storage, tmp_path, "new", 100)
    first["lastUsedAt"] = time.time() - 60
    write_json(storage.record_path(first["id"]), first)
    # A high maintenance low-water mark must not make admission delete both caches.
    storage.configure({"budgetBytes": 150, "lowWaterBytes": 10**18})
    assert storage.config["automaticCategories"] == []
    result = admit(storage, [("build", tmp_path, 1)])
    assert result["allowed"]
    assert [r["id"] for r in result["cleanup"]["deleted"]] == [first["id"]]
    assert storage.record_path(second["id"]).exists()
    assert storage.config["automaticCategories"] == []


def test_admission_does_not_clean_expired_cache_when_requirements_met(storage, tmp_path):
    row = object_with_bytes(storage, tmp_path, "cache")
    row["lastUsedAt"] = 1
    write_json(storage.record_path(row["id"]), row)
    storage.configure({"lowWaterBytes": 10**18})
    result = admit(storage, [("build", tmp_path, 1)])
    assert result["cleanup"] is None
    assert (tmp_path / "cache/data").exists()


def test_admission_cleans_only_short_volume_and_stops_at_actual_need(
    storage, tmp_path, monkeypatch
):
    other = object_with_bytes(storage, tmp_path, "other-volume", 100)
    old = object_with_bytes(storage, tmp_path, "old", 100)
    object_with_bytes(storage, tmp_path, "new", 100)
    old["lastUsedAt"] = 1
    other["lastUsedAt"] = 0
    for row in (old, other):
        write_json(storage.record_path(row["id"]), row)

    def capacity(path):
        if path.name == "other-volume":
            return "other", 10
        return "build", 100 if (tmp_path / "old").exists() else 200

    monkeypatch.setattr("build_storage.policy.volume", capacity)
    monkeypatch.setattr("build_storage.store.volume", capacity)
    result = admit(storage, [("work", tmp_path / "work", 150)])
    assert result["allowed"]
    assert [r["id"] for r in result["cleanup"]["deleted"]] == [old["id"]]
    assert (tmp_path / "new/data").exists()
    assert (tmp_path / "other-volume/data").exists()


@pytest.mark.parametrize("protection", ["pin", "retain", "running", "ownership"])
def test_automatic_admission_never_bypasses_protection(storage, tmp_path, protection):
    row = object_with_bytes(storage, tmp_path, "protected")
    if protection == "pin":
        storage.pin(row["id"], "user retained")
    elif protection == "ownership":
        (tmp_path / "protected" / MARKER).write_text("{}", encoding="utf-8")
    else:
        row["state"] = "running" if protection == "running" else "idle"
        row["retainUntil"] = time.time() + 3600 if protection == "retain" else None
        write_json(storage.record_path(row["id"]), row)
    storage.configure({"budgetBytes": 1})
    with pytest.raises(RuntimeError, match="build admission blocked"):
        admit(storage, [("build", tmp_path, 1)])
    assert (tmp_path / "protected/data").read_bytes() == b"x" * 100


def test_parallel_running_objects_use_one_growth_reservation(storage, tmp_path, monkeypatch):
    monkeypatch.setattr("build_storage.policy.volume", lambda _: ("volume", 1000))
    monkeypatch.setattr("build_storage.store.volume", lambda _: ("volume", 1000))
    object_with_bytes(storage, tmp_path, "cache", 100)
    storage.configure({"budgetBytes": 250})
    with (
        reserve(storage, [("operation", tmp_path, 120)]),
        storage.use(tmp_path / "cache", "package-cache"),
        storage.use(tmp_path / "build", "package-build"),
    ):
        report = admit(storage, [("concurrent", tmp_path, 100)])
        assert report["allowed"]
        assert report["storage"]["knownLogicalBytes"] == 100
        assert report["storage"]["reservedGrowthBytes"] == 120
        assert len(report["storage"]["estimatedObjects"]) == 2
        assert report["storage"]["unknownObjects"] == []
        assert report["space"]["volumes"][0]["reservedBytes"] == 120
        assert report["cleanup"] is None


def test_running_without_reservation_remains_unknown_and_protected(storage, tmp_path):
    object_with_bytes(storage, tmp_path, "idle")
    with storage.use(tmp_path / "running", "dev"):
        with pytest.raises(RuntimeError, match="build admission blocked"):
            admit(storage, [("build", tmp_path, 1)])
        assert (tmp_path / "idle/data").exists()
        assert (tmp_path / "running" / MARKER).exists()


def test_reservation_growth_cannot_silently_exceed_budget(storage, tmp_path, monkeypatch):
    monkeypatch.setattr("build_storage.policy.volume", lambda _: ("volume", 1000))
    monkeypatch.setattr("build_storage.store.volume", lambda _: ("volume", 1000))
    storage.configure({"budgetBytes": 100})
    with (
        reserve(storage, [("running", tmp_path, 120)]),
        storage.use(tmp_path / "cache", "dev"),
        pytest.raises(RuntimeError, match="build admission blocked"),
    ):
        admit(storage, [("build", tmp_path, 1)])


def test_admission_reports_unreserved_space_shortfall(storage, tmp_path, monkeypatch):
    monkeypatch.setattr("build_storage.policy.volume", lambda _: ("volume", 100))
    with reserve(storage, [("running", tmp_path, 60)]):
        preview = plan(storage, needs=[("next", tmp_path, 60)])
        assert not preview["allowed"]
        assert preview["space"]["volumes"][0]["shortfallBytes"] == 20
        with pytest.raises(RuntimeError, match="build admission blocked"):
            admit(storage, [("next", tmp_path, 60)])
