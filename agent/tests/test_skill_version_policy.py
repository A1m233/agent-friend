from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.parse
from email.message import Message
from pathlib import Path
from types import ModuleType

import pytest

from .test_builtin_skill_installer_script import _fixture_archive, _load_installer

A = "a" * 40
B = "b" * 40
C = "c" * 40
NAME = "fixture-skill"
INSTALL = ["--repo", "fixture/repo", "--path", f"skills/{NAME}", "--method", "download"]
UPDATE = ["--update", NAME, "--method", "download"]


class Remote:
    def __init__(self) -> None:
        self.refs = {"heads/main": A, "heads/next": B, "tags/v1": A, A: A, B: B, C: C, A[:7]: A}
        self.packages = {
            A: _fixture_archive(script="a\n"),
            B: _fixture_archive(script="b\n"),
            C: _fixture_archive(script="c\n"),
        }
        self.requests: list[str] = []

    def request(self, url: str) -> bytes:
        self.requests.append(url)
        if "/commits/" in url:
            ref = urllib.parse.unquote(url.split("/commits/", 1)[1])
            if ref not in self.refs:
                raise urllib.error.HTTPError(url, 404, "Unknown ref", Message(), None)
            return json.dumps({"sha": self.refs[ref]}).encode()
        return self.packages[url.rsplit("/", 1)[1]]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, Path, Remote]:
    module = _load_installer(mock_api=False)
    root = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    remote = Remote()
    monkeypatch.setenv("AGENT_FRIEND_PERSONAL_SKILLS_DIR", str(root))
    monkeypatch.delenv("AGENT_FRIEND_SKILL_UPDATE_RECEIPT", raising=False)
    monkeypatch.delenv("AGENT_FRIEND_SKILL_UPDATE_NONCE", raising=False)
    monkeypatch.setattr(module, "_tmp_root", lambda: str(downloads))
    monkeypatch.setattr(module, "_request", remote.request)
    return module, root, remote


def read_record(root: Path) -> dict[str, object]:
    return json.loads((root / ".agent-friend-skill-lock.json").read_text("utf-8"))["skills"][NAME]  # type: ignore[no-any-return]


def test_branch_updates_continue_after_one_time_revision(
    env: tuple[ModuleType, Path, Remote],
) -> None:
    module, root, remote = env
    assert module.main(INSTALL) == 0
    assert module.main([*UPDATE, "--revision", B]) == 0
    assert read_record(root)["ref"] == "main"
    assert read_record(root)["installed_commit"] == B
    remote.refs["heads/main"] = C
    assert module.main(UPDATE) == 0
    assert read_record(root)["installed_commit"] == C
    assert (root / NAME / "scripts/run.py").read_text() == "c\n"
    assert all(url.rsplit("/", 1)[-1] in {A, B, C} for url in remote.requests if "/zip/" in url)


def test_pin_stays_immutable_and_unpin_resumes_branch(
    env: tuple[ModuleType, Path, Remote], capsys: pytest.CaptureFixture[str]
) -> None:
    module, root, remote = env
    assert module.main(INSTALL) == 0
    assert module.main([*UPDATE, "--pin", "v1"]) == 0
    remote.refs.update({"tags/v1": B, "heads/main": C})
    remote.requests.clear()
    before = read_record(root)
    assert module.main(UPDATE) == 0
    assert not remote.requests
    assert read_record(root) == before
    output = capsys.readouterr().out
    assert f"Pinned at {A}" in output and "remote update check skipped" in output
    assert "Already up to date" not in output
    assert module.main([*UPDATE, "--revision", B]) == 1
    assert module.main([*UPDATE, "--unpin"]) == 0
    assert read_record(root)["pinned_commit"] is None
    assert read_record(root)["installed_commit"] == C


def test_first_install_pin_does_not_invent_a_branch(env: tuple[ModuleType, Path, Remote]) -> None:
    module, root, _remote = env
    assert module.main([*INSTALL, "--pin", A]) == 0
    record = read_record(root)
    assert record["source_kind"] == "none" and record["ref"] == ""
    assert module.main([*UPDATE, "--unpin"]) == 1
    assert read_record(root) == record
    assert module.main([*UPDATE, "--branch", "main"]) == 0
    assert read_record(root)["source_kind"] == "branch"


def test_metadata_only_updates_preserve_directory_and_true_noop_preserves_lock(
    env: tuple[ModuleType, Path, Remote],
) -> None:
    module, root, remote = env
    remote.packages[B] = remote.packages[A]
    assert module.main(INSTALL) == 0
    entry = root / NAME / "SKILL.md"
    lock = root / ".agent-friend-skill-lock.json"
    entry_mtime = entry.stat().st_mtime_ns
    assert module.main([*UPDATE, "--branch", "next"]) == 0
    assert read_record(root)["ref"] == "next"
    assert read_record(root)["installed_commit"] == B
    assert entry.stat().st_mtime_ns == entry_mtime
    before = lock.read_bytes(), lock.stat().st_mtime_ns
    assert module.main(UPDATE) == 0
    assert (lock.read_bytes(), lock.stat().st_mtime_ns) == before
    remote.refs["heads/next"] = C
    remote.packages[C] = remote.packages[A]
    assert module.main(UPDATE) == 0
    assert read_record(root)["installed_commit"] == C
    assert entry.stat().st_mtime_ns == entry_mtime


@pytest.mark.parametrize("ref", [A[:7], "main", "v1"])
def test_legacy_refs_keep_intent_unknown(
    env: tuple[ModuleType, Path, Remote], ref: str, capsys: pytest.CaptureFixture[str]
) -> None:
    module, root, remote = env
    assert module.main(INSTALL) == 0
    record = read_record(root)
    old = {key: record[key] for key in ("repository", "path", "ref", "content_sha256")}
    old["ref"] = ref
    lock = root / ".agent-friend-skill-lock.json"
    lock.write_text(json.dumps({"schema_version": 1, "skills": {NAME: old}}), encoding="utf-8")
    before = lock.read_bytes()
    assert module.load_records(root)[NAME].installed_commit is None
    assert lock.read_bytes() == before
    remote.refs[ref] = A
    remote.refs["heads/main"] = B
    remote.requests.clear()
    assert module.main(UPDATE) == 0
    assert read_record(root)["ref"] == ref
    assert read_record(root)["source_kind"] == "legacy"
    assert read_record(root)["pinned_commit"] is None
    assert read_record(root)["installed_commit"] == A
    assert "intent unknown" in capsys.readouterr().out
    assert not any("heads%2Fmain" in url for url in remote.requests)
    assert module.main([*UPDATE, "--branch", "main"]) == 0
    assert read_record(root)["installed_commit"] == B


@pytest.mark.parametrize(
    "options",
    [
        ["--ref", A],
        ["--branch", "main", "--pin", A],
        ["--pin", A, "--unpin"],
        ["--revision", B, "--unpin"],
        ["--expect-commit", "main"],
        ["--branch", "-oops"],
    ],
)
def test_invalid_options_do_not_change_state(
    env: tuple[ModuleType, Path, Remote], options: list[str]
) -> None:
    module, root, remote = env
    assert module.main(INSTALL) == 0
    before = read_record(root)
    remote.requests.clear()
    # Use = for option-looking values so validation, not argparse, owns the error.
    if options == ["--branch", "-oops"]:
        options = ["--branch=-oops"]
    assert module.main([*UPDATE, *options]) == 1
    assert read_record(root) == before and not remote.requests


def test_verification_is_exact_and_does_not_change_policy(
    env: tuple[ModuleType, Path, Remote],
) -> None:
    module, root, remote = env
    assert module.main([*INSTALL, "--expect-commit", A[:7]]) == 0
    before = read_record(root)
    remote.refs["heads/main"] = B
    assert module.main([*UPDATE, "--expect-commit", A]) == 1
    assert read_record(root) == before
    assert module.main([*UPDATE, "--expect-commit", B]) == 0
    assert read_record(root)["ref"] == "main"
    assert read_record(root)["pinned_commit"] is None


def test_metadata_write_failure_preserves_original(
    env: tuple[ModuleType, Path, Remote], monkeypatch: pytest.MonkeyPatch
) -> None:
    module, root, remote = env
    assert module.main(INSTALL) == 0
    remote.packages[B] = remote.packages[A]
    before = (root / ".agent-friend-skill-lock.json").read_bytes()
    managed = sys.modules[module.commit_candidate.__module__]

    def fail(*_args: object) -> None:
        raise managed.ManagedInstallError("lock_write_failed", "injected")

    monkeypatch.setattr(managed, "_write_records", fail)
    assert module.main([*UPDATE, "--branch", "next"]) == 1
    assert (root / ".agent-friend-skill-lock.json").read_bytes() == before
    assert (root / NAME / "scripts/run.py").read_text() == "a\n"


def test_same_content_does_not_hide_concurrent_policy_change(
    env: tuple[ModuleType, Path, Remote],
) -> None:
    module, root, _remote = env
    assert module.main(INSTALL) == 0
    managed = sys.modules[module.commit_candidate.__module__]
    before = managed.load_records(root)[NAME]
    candidate, digest = managed.stage_candidate(root / NAME, root, NAME)
    assert module.main([*UPDATE, "--pin", A]) == 0
    try:
        with pytest.raises(managed.ManagedInstallError, match="changed while"):
            managed.commit_candidate(
                root=root,
                skill_name=NAME,
                candidate=candidate,
                record=before,
                expected_mode="managed",
                expected_record=before,
                expected_current_sha256=digest,
            )
        assert read_record(root)["pinned_commit"] == A
    finally:
        managed.remove_candidate(candidate)


def test_pinned_replacement_keeps_the_fixed_commit(env: tuple[ModuleType, Path, Remote]) -> None:
    module, root, remote = env
    assert module.main([*INSTALL, "--pin", "v1"]) == 0
    (root / NAME / "scripts/run.py").write_text("local\n")
    assert module.main(UPDATE) == 1
    remote.refs["tags/v1"] = B
    assert module.main([*UPDATE, "--replace-local-changes"]) == 0
    assert read_record(root)["pinned_commit"] == A
    assert (root / NAME / "scripts/run.py").read_text() == "a\n"


@pytest.mark.parametrize("payload", [b"not json", b'{"sha":"short"}', b"[]"])
def test_bad_commit_evidence_never_installs(
    env: tuple[ModuleType, Path, Remote], monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    module, root, _remote = env
    monkeypatch.setattr(module, "_request", lambda _url: payload)
    assert module.main(INSTALL) == 1
    assert not root.exists()


def test_sha_shaped_reference_cannot_resolve_to_an_unrelated_commit(
    env: tuple[ModuleType, Path, Remote],
) -> None:
    module, root, remote = env
    remote.refs[A[:7]] = B
    assert module.main([*INSTALL, "--pin", A[:7]]) == 1
    assert not root.exists()


@pytest.mark.parametrize("options", [["--branch", "next"], ["--pin", "v1"]])
def test_local_changes_block_policy_changes(
    env: tuple[ModuleType, Path, Remote], options: list[str]
) -> None:
    module, root, remote = env
    assert module.main(INSTALL) == 0
    before = read_record(root)
    (root / NAME / "local.txt").write_text("Keep this local change.")
    remote.requests.clear()
    assert module.main([*UPDATE, *options]) == 1
    assert read_record(root) == before and not remote.requests
    assert (root / NAME / "local.txt").exists()


@pytest.mark.parametrize("policy", [[], ["--branch", "next"], ["--pin", "main"]])
def test_url_ref_requires_matching_explicit_intent(
    env: tuple[ModuleType, Path, Remote], policy: list[str]
) -> None:
    module, root, _remote = env
    args = [
        "--url",
        f"https://github.com/fixture/repo/tree/main/skills/{NAME}",
        "--method",
        "download",
    ]
    assert module.main([*args, *policy]) == 1
    assert not root.exists()
    assert module.main([*args, "--branch", "main"]) == 0
    assert read_record(root)["source_kind"] == "branch"


def test_abbreviated_and_duplicate_options_rejected_before_writes(
    env: tuple[ModuleType, Path, Remote],
) -> None:
    module, root, remote = env
    for options in (["--bra", "main"], ["--branch", "main", "--branch=next"]):
        with pytest.raises(SystemExit) as raised:
            module.main([*INSTALL, *options])
        assert raised.value.code == 2
    assert not root.exists() and not remote.requests


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def test_git_resolves_old_short_sha_and_keeps_branch_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer(mock_api=False)
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    package = repo / "skills" / NAME
    package.mkdir(parents=True)
    entry = package / "SKILL.md"
    entry.write_text(
        f"---\nname: {NAME}\ndescription: Local Git fixture.\n---\nA\n", encoding="utf-8"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "a")
    first = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "v1")
    entry.write_text(entry.read_text().replace("\nA\n", "\nB\n"), encoding="utf-8")
    git(repo, "commit", "-am", "b")
    second = git(repo, "rev-parse", "HEAD")
    root = tmp_path / "installed"
    monkeypatch.setenv("AGENT_FRIEND_PERSONAL_SKILLS_DIR", str(root))
    monkeypatch.setattr(module, "_tmp_root", lambda: str(tmp_path))
    monkeypatch.setattr(module, "_build_repo_url", lambda *_: str(repo))
    monkeypatch.setattr(module, "_build_repo_ssh", lambda *_: str(repo))
    assert (
        module.main(
            [
                *INSTALL[:-1],
                "git",
                "--branch",
                "main",
                "--revision",
                first[:7],
                "--expect-commit",
                first[:7],
            ]
        )
        == 0
    )
    assert read_record(root)["installed_commit"] == first
    assert module.main([*UPDATE[:-1], "git"]) == 0
    assert read_record(root)["installed_commit"] == second
    assert module.main([*UPDATE[:-1], "git", "--pin", "main"]) == 1
    assert module.main([*UPDATE[:-1], "git", "--pin", "v1"]) == 0
    assert read_record(root)["pinned_commit"] == first
    # Even pinned short-SHA verification must honor git-only authentication.
    assert module.main([*UPDATE[:-1], "git", "--expect-commit", first[:7]]) == 0
    assert read_record(root)["pinned_commit"] == first


def test_download_fallback_keeps_already_resolved_commit(
    env: tuple[ModuleType, Path, Remote], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module, _root, remote = env
    source = module.Source(
        "fixture",
        "repo",
        "main",
        [f"skills/{NAME}"],
        source_kind="branch",
        update_ref="main",
        target_kind="branch",
    )

    def fail_download(*_args: object) -> str:
        remote.refs["heads/main"] = B
        raise module.InstallError("injected network failure", code="download_failed")

    def checkout(_url: str, selected: object, _directory: str) -> str:
        assert vars(selected)["resolved_commit"] == A
        return "fixture-checkout"

    monkeypatch.setattr(module, "_download_repo_zip", fail_download)
    monkeypatch.setattr(module, "_git_sparse_checkout", checkout)
    assert module._prepare_repo(source, "auto", str(tmp_path)) == "fixture-checkout"


@pytest.mark.parametrize(
    "fields",
    [
        {"source_kind": "surprise"},
        {"installed_commit": "short"},
        {"pinned_commit": B},
        {"source_kind": "none"},
        {"extra": "field"},
    ],
)
def test_invalid_v2_record_fails_closed(
    env: tuple[ModuleType, Path, Remote], fields: dict[str, str]
) -> None:
    module, root, remote = env
    assert module.main(INSTALL) == 0
    lock = root / ".agent-friend-skill-lock.json"
    payload = json.loads(lock.read_text())
    payload["skills"][NAME].update(fields)
    lock.write_text(json.dumps(payload), encoding="utf-8")
    before = lock.read_bytes()
    remote.requests.clear()
    assert module.main(UPDATE) == 1
    assert lock.read_bytes() == before and not remote.requests
