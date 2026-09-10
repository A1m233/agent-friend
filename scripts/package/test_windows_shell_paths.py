import pytest
import windows_shell_paths as paths


def setup_folders(monkeypatch, tmp_path):
    folders = {name: tmp_path / name for name in ("local", "roaming", "programs", "desktop")}
    for p in folders.values():
        p.mkdir()
    monkeypatch.setattr(paths, "shell_paths", lambda: folders)
    monkeypatch.setenv("LOCALAPPDATA", str(folders["local"]))
    monkeypatch.setenv("APPDATA", str(folders["roaming"]))
    return folders


def test_normal_environment_leaves_no_probes(monkeypatch, tmp_path):
    folders = setup_folders(monkeypatch, tmp_path)
    paths.verify_smoke_environment()
    assert all(not list(p.iterdir()) for p in folders.values())


def test_override_is_rejected_before_any_probe(monkeypatch, tmp_path):
    folders = setup_folders(monkeypatch, tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "override"))
    with pytest.raises(RuntimeError, match="environment mismatch"):
        paths.verify_smoke_environment()
    assert all(not list(p.iterdir()) for p in folders.values())


def test_redirected_probe_is_removed_without_touching_existing_state(monkeypatch, tmp_path):
    folders = setup_folders(monkeypatch, tmp_path)
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    sentinel = folders["local"] / "existing"
    sentinel.write_text("keep")

    def redirect_probe(*, prefix, dir):
        probe = redirected / (prefix + "owned")
        probe.mkdir()
        return str(probe)

    monkeypatch.setattr(paths.tempfile, "mkdtemp", redirect_probe)
    with pytest.raises(RuntimeError, match="filesystem redirection"):
        paths.verify_smoke_environment()
    assert sentinel.read_text() == "keep"
    assert not list(redirected.iterdir())
