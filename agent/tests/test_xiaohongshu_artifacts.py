from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from agent.tools.builtin.social_platforms.connectors.xiaohongshu_artifacts import (
    XIAOHONGSHU_ARTIFACT_DIR_ENV,
    XIAOHONGSHU_VERSION,
    XiaohongshuArtifact,
    current_xiaohongshu_artifacts,
    default_xiaohongshu_artifact_dir,
    verify_xiaohongshu_artifact,
)


def test_manifest_has_two_locked_artifacts_for_each_supported_platform() -> None:
    for system, machine in (
        ("windows", "amd64"),
        ("linux", "x86_64"),
        ("darwin", "aarch64"),
    ):
        artifacts = current_xiaohongshu_artifacts(system=system, machine=machine)
        assert {item.role for item in artifacts} == {"server", "login"}
        assert all(len(item.sha256) == 64 for item in artifacts)
        if system == "windows":
            assert {item.source for item in artifacts} == {"source-build"}
        else:
            assert all(item.source == "release" for item in artifacts)
            assert all(XIAOHONGSHU_VERSION in item.url for item in artifacts)


def test_source_build_artifact_has_no_release_download_url() -> None:
    artifact = current_xiaohongshu_artifacts(system="windows", machine="amd64")[0]

    with pytest.raises(RuntimeError, match="locked source build"):
        _ = artifact.url


def test_artifact_verification_checks_size_and_sha256(tmp_path: Path) -> None:
    payload = b"locked artifact"
    artifact = XiaohongshuArtifact(
        role="server",
        system="test",
        machine="test",
        filename="connector",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    path = tmp_path / artifact.filename
    path.write_bytes(payload)

    assert verify_xiaohongshu_artifact(path, artifact) is True

    path.write_bytes(b"tampered")
    assert verify_xiaohongshu_artifact(path, artifact) is False


def test_artifact_directory_override_is_explicit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(XIAOHONGSHU_ARTIFACT_DIR_ENV, str(tmp_path))

    assert default_xiaohongshu_artifact_dir() == tmp_path


def test_locked_compatibility_patch_has_lf_checkout_policy() -> None:
    repository_root = Path(__file__).parents[2]
    attributes = (repository_root / ".gitattributes").read_text(encoding="utf-8")

    assert (
        "scripts/_lib/xiaohongshu_connector/windows-disable-leakless.patch text eol=lf"
        in attributes.splitlines()
    )
