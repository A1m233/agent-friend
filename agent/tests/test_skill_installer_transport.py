from __future__ import annotations

import http.client
import importlib.util
import io
import json
import sys
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from .test_builtin_skill_installer_script import _fixture_archive, _load_installer


def _github() -> ModuleType:
    _load_installer(mock_api=False)
    return sys.modules["github_utils"]


@pytest.mark.parametrize("phase", ["open", "read"])
@pytest.mark.parametrize(
    "error",
    [
        http.client.IncompleteRead(b"partial", 10),
        http.client.RemoteDisconnected("connection closed"),
        ConnectionResetError("connection reset"),
        TimeoutError("timed out"),
        urllib.error.URLError("connection failed"),
    ],
)
def test_transport_errors_are_normalized_without_retry(
    monkeypatch: pytest.MonkeyPatch, phase: str, error: Exception
) -> None:
    github = _github()
    response = io.BytesIO()
    calls = []

    def fail_read() -> bytes:
        raise error

    def open_response(*_args: object) -> io.BytesIO:
        calls.append("open")
        if phase == "open":
            raise error
        return response

    monkeypatch.setattr(response, "read", fail_read)
    monkeypatch.setattr(github.urllib.request, "urlopen", open_response)
    with pytest.raises(github.GitHubTransportError) as failure:
        github.github_request("https://api.github.com/fixture", "test")
    assert failure.value.phase == phase
    assert failure.value.error_type == type(error).__name__
    assert failure.value.__cause__ is error
    assert calls == ["open"]
    if phase == "read":
        assert response.closed


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_http_status_errors_keep_their_identity(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    github = _github()
    error = urllib.error.HTTPError(
        "https://api.github.com/fixture", status, "failed", Message(), None
    )

    def fail(*_args: object) -> None:
        raise error

    monkeypatch.setattr(github.urllib.request, "urlopen", fail)
    with pytest.raises(urllib.error.HTTPError) as failure:
        github.github_request(error.url, "test")
    assert failure.value is error


def test_fresh_install_transport_failure_creates_no_package_or_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_installer(mock_api=False)
    github = _github()
    root = tmp_path / "personal"
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    monkeypatch.setenv("AGENT_FRIEND_PERSONAL_SKILLS_DIR", str(root))
    monkeypatch.setattr(module, "_tmp_root", lambda: str(temporary))

    def fail_open(*_args: object) -> None:
        raise ConnectionResetError("connection reset")

    monkeypatch.setattr(github.urllib.request, "urlopen", fail_open)
    assert (
        module.main(
            ["--repo", "fixture/repo", "--path", "skills/fixture-skill", "--method", "download"]
        )
        == 1
    )
    error = capsys.readouterr().err
    assert "resolve_failed" in error and "open failed (ConnectionResetError)" in error
    assert not (root / "fixture-skill").exists()
    assert not (root / ".agent-friend-skill-lock.json").exists()
    assert not list(temporary.iterdir())


@pytest.mark.parametrize("stage", ["resolve", "download"])
@pytest.mark.parametrize("outcome", ["fallback_success", "fallback_failure", "download_only"])
def test_update_transport_failure_preserves_transaction_and_selected_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
    outcome: str,
) -> None:
    module = _load_installer(mock_api=False)
    github = _github()
    root = tmp_path / "personal"
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    monkeypatch.setenv("AGENT_FRIEND_PERSONAL_SKILLS_DIR", str(root))
    monkeypatch.setattr(module, "_tmp_root", lambda: str(temporary))
    old_commit, new_commit = "a" * 40, "b" * 40
    mode = "install"
    calls: list[str] = []

    def open_response(request: urllib.request.Request) -> io.BytesIO:
        url = request.full_url
        current_stage = "resolve" if url.startswith("https://api.github.com/") else "download"
        calls.append(current_stage)
        if mode == "update" and stage == current_stage:
            response = io.BytesIO()

            def fail_read() -> bytes:
                raise http.client.IncompleteRead(b"partial", 10)

            monkeypatch.setattr(response, "read", fail_read)
            return response
        if current_stage == "resolve":
            return io.BytesIO(json.dumps({"sha": old_commit}).encode())
        return io.BytesIO(_fixture_archive(script="old\n"))

    monkeypatch.setattr(github.urllib.request, "urlopen", open_response)
    assert module.main(["--repo", "fixture/repo", "--path", "skills/fixture-skill"]) == 0
    capsys.readouterr()
    lock = root / ".agent-friend-skill-lock.json"
    before = lock.read_bytes(), module.package_digest(root / "fixture-skill")
    receipt = tmp_path / "receipt.json"
    monkeypatch.setenv("AGENT_FRIEND_SKILL_UPDATE_RECEIPT", str(receipt))
    monkeypatch.setenv("AGENT_FRIEND_SKILL_UPDATE_NONCE", "test-nonce")
    mode = "update"
    calls.clear()
    git_calls = []

    # Prepare Git's independent candidate without another HTTP request.
    candidate = tmp_path / "git-candidate"
    (candidate / "skills/fixture-skill").mkdir(parents=True)
    (candidate / "skills/fixture-skill/SKILL.md").write_text(
        "---\nname: fixture-skill\ndescription: Updated fixture.\n---\nUpdated.\n", "utf-8"
    )

    def git_checkout(url: str, source: Any, _directory: str) -> str:
        git_calls.append(url)
        # A moved branch must not replace the commit resolved before a ZIP failure.
        selected = source.resolved_commit
        assert selected == (old_commit if stage == "download" else None)
        if outcome != "fallback_success":
            raise module.InstallError("git unavailable", code="git_failed")
        source.resolved_commit = selected or new_commit
        return str(candidate)

    monkeypatch.setattr(module, "_git_sparse_checkout", git_checkout)
    method = "download" if outcome == "download_only" else "auto"
    result = module.main(["--update", "fixture-skill", "--method", method])
    captured = capsys.readouterr()
    assert calls == (["resolve"] if stage == "resolve" else ["resolve", "download"])
    if outcome == "fallback_success":
        assert result == 0
        assert len(git_calls) == 1
        record = json.loads(lock.read_text("utf-8"))["skills"]["fixture-skill"]
        assert record["installed_commit"] == (old_commit if stage == "download" else new_commit)
        assert record["content_sha256"] == module.package_digest(root / "fixture-skill")
        assert json.loads(receipt.read_text("utf-8"))["status"] == "committed"
    else:
        assert result == 1
        assert len(git_calls) == (0 if outcome == "download_only" else 2)
        assert "IncompleteRead" in captured.err
        assert "read failed" in captured.err
        assert "Traceback" not in captured.err
        assert before == (lock.read_bytes(), module.package_digest(root / "fixture-skill"))
        assert not receipt.exists()
    assert not list(temporary.iterdir())


@pytest.mark.parametrize("kind", ["transport", "http404", "invalid_json"])
def test_listing_returns_an_actionable_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    github = _github()
    assert github.__file__ is not None
    path = Path(github.__file__).with_name("list-skills.py")
    spec = importlib.util.spec_from_file_location("skill_listing_transport_test", path)
    assert spec and spec.loader
    listing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(listing)

    def open_response(*_args: object) -> io.BytesIO:
        if kind == "http404":
            raise urllib.error.HTTPError(
                "https://api.github.com/fixture", 404, "missing", Message(), None
            )
        response = io.BytesIO(b"not json")
        if kind == "transport":

            def fail_read() -> bytes:
                raise http.client.IncompleteRead(b"partial", 10)

            monkeypatch.setattr(response, "read", fail_read)
        return response

    monkeypatch.setattr(github.urllib.request, "urlopen", open_response)
    assert listing.main([]) == 1
    error = capsys.readouterr().err
    expected = {
        "transport": "read failed (IncompleteRead)",
        "http404": "Skills path not found",
        "invalid_json": "Invalid skills listing response",
    }[kind]
    assert expected in error
    assert "Traceback" not in error
