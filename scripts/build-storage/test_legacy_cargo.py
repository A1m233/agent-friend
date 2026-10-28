from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "_lib"))

from build_storage import Storage
from build_storage.execution import adopt_legacy_package
from build_storage.files import MARKER, read_json
from build_storage.legacy_cargo import cargo_evidence, legacy_identity
from build_storage.locks import Busy


@pytest.fixture
def legacy(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    storage = Storage(repo, control=tmp_path / "control")
    source = tmp_path / "removed worktree" / "agent-friend"
    target = (
        tmp_path / "packaging/work" / storage.repo_id[:12] / legacy_identity(source) / "build-cache"
    )
    cargo = target / "cargo-target"
    (cargo / "debug/deps").mkdir(parents=True)
    (cargo / "CACHEDIR.TAG").write_text(
        "Signature: 8a477f597d28d172789f06886806bc55\n", encoding="utf-8"
    )
    (cargo / ".rustc_info.json").write_text('{"rustc_fingerprint":123}', encoding="utf-8")
    dep = cargo / "debug/deps/app_lib-test.d"
    # Actual rustc env-dep shape, including doubled backslashes on Windows.
    manifest = str(source / "frontend/src-tauri").replace("\\", "\\\\")
    dep.write_text(
        f"library: src/lib.rs\n# env-dep:CARGO_MANIFEST_DIR={manifest}\n", encoding="utf-8"
    )
    return storage, target, source, dep


def test_explicit_cargo_adoption_records_proof_and_pins_without_fabricating_receipt(legacy):
    storage, target, source, dep = legacy
    with pytest.raises(ValueError, match="receipt missing"):
        adopt_legacy_package(storage, target)
    preview = adopt_legacy_package(storage, target, allow_cargo_evidence=True)
    assert preview["evidence"]["repoRoot"] == str(source.resolve())
    assert preview["source"] == str(dep)
    assert not (target / MARKER).exists()
    result = adopt_legacy_package(storage, target, allow_cargo_evidence=True, apply=True)
    record = read_json(storage.record_path(result["objectId"]))
    assert record["pin"] and record["adoptionEvidence"]["witnesses"]
    assert not (target.parent / "workspace-state.json").exists()
    proof = storage.control / "adoptions" / storage.repo_id / f"{record['id']}.json"
    assert read_json(proof)["evidence"] == preview["evidence"]
    with pytest.raises(Busy):
        storage.delete(record)


@pytest.mark.parametrize(
    "fault",
    [
        "missing-dep",
        "wrong-source",
        "ambiguous",
        "bad-tag",
        "extra-content",
        "wrong-repo",
        "malformed-receipt",
        "oversize",
    ],
)
def test_unproven_cargo_cache_is_not_adopted(legacy, fault):
    storage, target, source, dep = legacy
    if fault == "missing-dep":
        dep.unlink()
    elif fault == "wrong-source":
        dep.write_text(
            f"# env-dep:CARGO_MANIFEST_DIR={source.parent / 'different'}/frontend/src-tauri\n",
            encoding="utf-8",
        )
    elif fault == "ambiguous":
        with dep.open("a", encoding="utf-8") as f:
            f.write(
                f"# env-dep:CARGO_MANIFEST_DIR={source.parent / 'different'}/frontend/src-tauri\n"
            )
    elif fault == "bad-tag":
        (target / "cargo-target/CACHEDIR.TAG").write_text("not Cargo", encoding="utf-8")
    elif fault == "extra-content":
        (target / "keep.txt").write_text("user file", encoding="utf-8")
    elif fault == "wrong-repo":
        storage.repo_id = "0" * 24
    elif fault == "malformed-receipt":
        (target.parent / "workspace-state.json").write_text("{}", encoding="utf-8")
    elif fault == "oversize":
        dep.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    with pytest.raises((ValueError, OSError)):
        adopt_legacy_package(storage, target, allow_cargo_evidence=True, apply=True)
    assert not (target / MARKER).exists()


def test_cargo_adoption_honors_package_lock(legacy):
    storage, target, _, _ = legacy
    library = Path(__file__).resolve().parents[1] / "_lib"
    key = storage.lock_path("package", storage.repo_id[:12])
    code = f"import sys;sys.path.insert(0,{str(library)!r});from pathlib import Path;from build_storage.locks import lock\nwith lock(Path({str(key)!r})): print('ready',flush=True);input()"
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(Busy):
            adopt_legacy_package(storage, target, allow_cargo_evidence=True, apply=True)
    finally:
        child.communicate("\n", timeout=10)
    assert not (target / MARKER).exists()


def test_evidence_changed_before_lock_is_rejected(legacy, monkeypatch):
    storage, target, _, dep = legacy
    import build_storage.legacy_cargo as module

    original = module.cargo_evidence
    count = 0

    def changing(*args):
        nonlocal count
        count += 1
        if count == 2:
            with dep.open("a", encoding="utf-8") as f:
                f.write("\n# changed\n")
        return original(*args)

    monkeypatch.setattr(module, "cargo_evidence", changing)
    with pytest.raises(Busy, match="changed"):
        adopt_legacy_package(storage, target, allow_cargo_evidence=True, apply=True)
    assert not (target / MARKER).exists()


def test_cargo_evidence_does_not_follow_junction(legacy, tmp_path):
    storage, target, _, dep = legacy
    deps = dep.parent
    dep.unlink()
    deps.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(deps), str(outside)], capture_output=True
        )
        if created.returncode:
            pytest.skip("junction creation unavailable")
    else:
        deps.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="link"):
            cargo_evidence(target, storage.repo_id[:12])
    finally:
        deps.rmdir() if os.name == "nt" else deps.unlink()


def test_gc_cli_only_deletes_the_explicit_object(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    if sys.platform == "darwin":
        pytest.skip("CLI control-root isolation requires a separate macOS home")
    storage = Storage(repo)
    for name in ("selected", "other"):
        with storage.use(tmp_path / name, "package-cache"):
            (tmp_path / name / "data").write_bytes(b"x" * 50)
    storage.configure({"budgetBytes": 1, "lowWaterBytes": 1})
    selected = next(r for r in storage.inventory()["objects"] if Path(r["path"]).name == "selected")
    command = [
        sys.executable,
        str(repo / "scripts/build-storage/run.py"),
        "gc",
        "--only-object-id",
        selected["id"],
    ]
    preview = subprocess.run(command, capture_output=True, text=True)
    assert preview.returncode == 0, preview.stderr
    assert (tmp_path / "selected/data").exists()
    result = subprocess.run([*command, "--apply"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "selected").exists()
    assert (tmp_path / "other/data").exists()
