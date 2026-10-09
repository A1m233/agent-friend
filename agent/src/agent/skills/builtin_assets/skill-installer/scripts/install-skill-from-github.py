#!/usr/bin/env python3
# mypy: ignore-errors
"""Install a skill from a GitHub repo path into the host's personal skills root."""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path

from github_utils import GitHubTransportError, github_request
from managed_install import (
    InstallRecord,
    ManagedInstallError,
    commit_candidate,
    load_records,
    make_record,
    normalize_source_path,
    package_digest,
    remove_candidate,
    stage_candidate,
    validate_branch,
    validate_skill_name,
    verify_pinned,
)

DEFAULT_REF = "main"


@dataclass
class Args:
    url: str | None = None
    repo: str | None = None
    path: list[str] | None = None
    ref: str | None = None
    branch: str | None = None
    pin: str | None = None
    unpin: bool = False
    revision: str | None = None
    expect_commit: str | None = None
    dest: str | None = None
    name: str | None = None
    method: str = "auto"
    update: str | None = None
    replace_local_changes: bool = False
    replace_unmanaged: bool = False


@dataclass
class Source:
    owner: str
    repo: str
    ref: str
    paths: list[str]
    repo_url: str | None = None
    source_kind: str = "legacy"
    update_ref: str = ""
    target_kind: str = "legacy"
    pinned: bool = False
    resolved_commit: str | None = None
    expect_commit: str | None = None
    check_branch: bool = False


class InstallError(Exception):
    def __init__(self, message: str, *, code: str = "install_failed") -> None:
        super().__init__(message)
        self.code = code


def _tmp_root() -> str:
    # Stay inside the host-authorized TEMP root without adding another layer.
    return tempfile.gettempdir()


def _request(url: str) -> bytes:
    return github_request(url, "codex-skill-install")


def _parse_github_url(url: str, default_ref: str) -> tuple[str, str, str, str | None]:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc != "github.com":
        raise InstallError("Only GitHub URLs are supported for download mode.")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise InstallError("Invalid GitHub URL.")
    owner, repo = parts[0], parts[1]
    ref = default_ref
    subpath = ""
    if len(parts) > 2:
        if parts[2] in ("tree", "blob"):
            if len(parts) < 4:
                raise InstallError("GitHub URL missing ref or path.")
            ref = parts[3]
            subpath = "/".join(parts[4:])
        else:
            subpath = "/".join(parts[2:])
    return owner, repo, ref, subpath or None


def _download_repo_zip(owner: str, repo: str, ref: str, dest_dir: str) -> str:
    zip_url = f"https://codeload.github.com/{owner}/{repo}/zip/{ref}"
    zip_path = os.path.join(dest_dir, "repo.zip")
    try:
        payload = _request(zip_url)
    except urllib.error.HTTPError as exc:
        raise InstallError(f"Download failed: HTTP {exc.code}", code="download_failed") from exc
    except GitHubTransportError as exc:
        raise InstallError(f"Download failed: {exc}", code="download_failed") from exc
    try:
        with open(zip_path, "wb") as file_handle:
            file_handle.write(payload)
        with zipfile.ZipFile(zip_path, "r") as zip_file:
            return _safe_extract_zip(zip_file, dest_dir)
    except zipfile.BadZipFile as exc:
        raise InstallError(f"Downloaded archive is invalid: {exc}", code="archive_invalid") from exc
    except OSError as exc:
        if getattr(exc, "winerror", None) == 206 or exc.errno == errno.ENAMETOOLONG:
            raise InstallError(
                "Downloaded archive could not be extracted: path too long. "
                "Use a shorter authorized temporary root before retrying; "
                "the installed Skill and source record were not changed.",
                code="archive_path_too_long",
            ) from exc
        raise InstallError(
            f"Cannot write or extract downloaded archive: {exc}", code="archive_io_failed"
        ) from exc


def _run_git(args: list[str]) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True)
    except OSError as exc:
        raise InstallError(f"Cannot run git: {exc}", code="git_failed") from exc
    if result.returncode != 0:
        original_error = result.stderr.strip() or "Git command failed."
        if (
            sys.platform == "win32"
            and "schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS" in original_error
        ):
            # A bounded process-local compatibility retry, not a global Git setting.
            # Do not retry authentication/certificate failures or disable verification.
            try:
                retry = subprocess.run(
                    [
                        args[0],
                        "-c",
                        "http.sslBackend=openssl",
                        "-c",
                        "http.sslVerify=true",
                        *args[1:],
                    ],
                    capture_output=True,
                    text=True,
                )
            except OSError as exc:
                raise InstallError(
                    f"Git Schannel failed: {original_error}\nOpenSSL retry could not start: {exc}",
                    code="git_failed",
                ) from exc
            if retry.returncode == 0:
                return retry.stdout.strip()
            raise InstallError(
                f"Git Schannel failed: {original_error}\n"
                f"OpenSSL retry failed: {retry.stderr.strip() or 'Git command failed.'}",
                code="git_failed",
            )
        raise InstallError(original_error, code="git_failed")
    return result.stdout.strip()


def _safe_extract_zip(zip_file: zipfile.ZipFile, dest_dir: str) -> str:
    # Like pip's leading-directory flattening, omit GitHub's repo-<40-char-SHA>
    # wrapper. Validate every member before writing anything; never normalize an
    # unsafe member into an apparently safe relative path.
    dest_root = os.path.realpath(os.path.join(dest_dir, "repo"))
    members = []
    top_levels = set()
    for info in zip_file.infolist():
        name = info.filename.rstrip("/")
        parts = name.split("/")
        if (
            not name
            or "\\" in name
            or any(part in {"", ".", ".."} or ":" in part for part in parts)
            or stat.S_ISLNK(info.external_attr >> 16)
        ):
            raise InstallError("Archive contains an unsafe member.", code="archive_invalid")
        top_levels.add(parts[0])
        if len(parts) == 1:
            if not info.is_dir():
                raise InstallError("Unexpected archive layout.", code="archive_invalid")
            continue
        target = os.path.realpath(os.path.join(dest_root, *parts[1:]))
        if not target.startswith(dest_root + os.sep):
            raise InstallError(
                "Archive contains files outside the destination.", code="archive_invalid"
            )
        members.append((info, target))
    if len(top_levels) != 1 or not members:
        raise InstallError(
            "Archive must contain one nonempty root directory.", code="archive_invalid"
        )
    for info, target in members:
        if info.is_dir():
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zip_file.open(info) as source, open(target, "xb") as destination:
                shutil.copyfileobj(source, destination)
    return dest_root


def _validate_relative_path(path: str) -> None:
    if os.path.isabs(path) or os.path.normpath(path).startswith(".."):
        raise InstallError("Skill path must be a relative path inside the repo.")


def _full_commit(value: object, *, requested: str | None = None) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise InstallError("Source did not resolve to a full Git commit.", code="invalid_commit")
    if (
        requested
        and re.fullmatch(r"[0-9a-fA-F]{7,40}", requested)
        and not value.startswith(requested.lower())
    ):
        raise InstallError(
            "Resolved commit does not match the requested SHA.", code="invalid_commit"
        )
    return value


def _api_commit(source: Source, ref: str, kind: str) -> str:
    # Explicit namespaces prevent a same-named tag from masquerading as a branch.
    if kind == "branch":
        ref = f"heads/{ref}"
    elif kind == "version" and not re.fullmatch(r"[0-9a-fA-F]{7,40}", ref):
        ref = f"tags/{ref}"
    url = f"https://api.github.com/repos/{source.owner}/{source.repo}/commits/{urllib.parse.quote(ref, safe='')}"
    try:
        payload = json.loads(_request(url))
    except (GitHubTransportError, urllib.error.HTTPError) as exc:
        raise InstallError(f"Cannot resolve Git ref: {exc}", code="resolve_failed") from exc
    except (ValueError, UnicodeError) as exc:
        raise InstallError("Invalid commit response.", code="invalid_commit") from exc
    return _full_commit(payload.get("sha") if isinstance(payload, dict) else None, requested=ref)


def _verify_commit(actual: str, expected: str | None) -> None:
    if expected is not None and actual != expected:
        raise InstallError(
            f"Expected commit {expected}, resolved {actual}; no installation changes made.",
            code="commit_mismatch",
        )


def _git_resolve(repo_dir: str, ref: str, kind: str) -> str:
    git = ["git", "-C", repo_dir]
    if kind == "branch":
        target = f"refs/heads/{ref}"
    elif kind == "version" and not re.fullmatch(r"[0-9a-fA-F]{7,40}", ref):
        target = f"refs/tags/{ref}"
    else:
        target = ref
    if re.fullmatch(r"[0-9a-fA-F]{7,39}", target):
        # A short object ID cannot reliably be fetched. Fetch advertised history
        # into this disposable repository and let rev-parse reject ambiguity.
        unshallow = ["--unshallow"] if (Path(repo_dir) / ".git" / "shallow").exists() else []
        _run_git(
            [
                *git,
                "fetch",
                "--filter=blob:none",
                *unshallow,
                "origin",
                "+refs/heads/*:refs/remotes/origin/*",
                "+refs/tags/*:refs/tags/*",
            ]
        )
        value = _run_git([*git, "rev-parse", "--verify", f"{target}^{{commit}}"])
    else:
        _run_git([*git, "fetch", "--depth", "1", "--filter=blob:none", "origin", target])
        value = _run_git([*git, "rev-parse", "--verify", "FETCH_HEAD^{commit}"])
    return _full_commit(value, requested=target)


def _git_sparse_checkout(repo_url: str, source: Source, dest_dir: str) -> str:
    # Each transport attempt owns a fresh directory, including after a partial failure.
    repo_dir = tempfile.mkdtemp(prefix="git-", dir=dest_dir)
    git = ["git", "-C", repo_dir]
    _run_git([*git, "init", "--quiet"])
    _run_git([*git, "remote", "add", "origin", repo_url])
    # --revision may select an older commit but still needs a real branch source.
    if source.check_branch:
        _git_resolve(repo_dir, source.update_ref, "branch")
    commit = _git_resolve(
        repo_dir,
        source.resolved_commit or source.ref,
        "version" if source.resolved_commit else source.target_kind,
    )
    if source.resolved_commit:
        _verify_commit(commit, source.resolved_commit)
    source.resolved_commit = commit
    expected = None
    if source.expect_commit:
        expected = _git_resolve(repo_dir, source.expect_commit, "version")
    _verify_commit(commit, expected)
    if source.paths != ["."]:
        _run_git([*git, "sparse-checkout", "set", "--cone", "--", *source.paths])
    _run_git([*git, "checkout", "--detach", commit, "--"])
    _verify_commit(_full_commit(_run_git([*git, "rev-parse", "HEAD"])), commit)
    return repo_dir


def _build_repo_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}.git"


def _build_repo_ssh(owner: str, repo: str) -> str:
    return f"git@github.com:{owner}/{repo}.git"


def _prepare_repo(source: Source, method: str, tmp_dir: str) -> str:
    download_error: InstallError | None = None
    if method in ("download", "auto"):
        try:
            if source.check_branch:
                _api_commit(source, source.update_ref, "branch")
            source.resolved_commit = _api_commit(source, source.ref, source.target_kind)
            expected = (
                _api_commit(source, source.expect_commit, "version")
                if source.expect_commit
                else None
            )
            _verify_commit(source.resolved_commit, expected)
            return _download_repo_zip(source.owner, source.repo, source.resolved_commit, tmp_dir)
        except InstallError as exc:
            if method == "download":
                raise
            if exc.code in {"download_failed", "resolve_failed"} or str(exc).startswith(
                "Download failed: HTTP "
            ):
                download_error = exc
            else:
                raise
    if method in ("git", "auto"):
        repo_url = source.repo_url or _build_repo_url(source.owner, source.repo)
        try:
            return _git_sparse_checkout(repo_url, source, tmp_dir)
        except InstallError as https_exc:
            if https_exc.code in {"commit_mismatch", "invalid_commit"}:
                raise
            repo_url = _build_repo_ssh(source.owner, source.repo)
            try:
                return _git_sparse_checkout(repo_url, source, tmp_dir)
            except InstallError as ssh_exc:
                raise InstallError(
                    (
                        f"API/ZIP download failed [{download_error.code}]: {download_error}\n"
                        if download_error is not None
                        else ""
                    )
                    + f"HTTPS git clone failed: {https_exc}\nSSH git clone failed: {ssh_exc}"
                ) from ssh_exc
    raise InstallError("Unsupported method.")


def _resolve_source(args: Args) -> Source:
    if args.url:
        owner, repo, ref, url_path = _parse_github_url(args.url, DEFAULT_REF)
        parts = urllib.parse.urlparse(args.url).path.split("/")
        if len(parts) > 3 and parts[3] in {"tree", "blob"}:
            explicit = args.revision or args.pin or args.branch
            if explicit is None or explicit != ref:
                raise InstallError(
                    "A tree/blob URL requires a matching --branch, --pin, or --revision. "
                    "For slash-containing refs use --repo and --path.",
                    code="ambiguous_ref",
                )
        if args.path is not None:
            paths = list(args.path)
        elif url_path:
            paths = [url_path]
        else:
            paths = []
        if not paths:
            raise InstallError("Missing --path for GitHub URL.")
        return Source(owner=owner, repo=repo, ref=ref, paths=paths)

    if not args.repo:
        raise InstallError("Provide --repo or --url.")
    if "://" in args.repo:
        return _resolve_source(replace(args, url=args.repo, repo=None))

    repo_parts = [p for p in args.repo.split("/") if p]
    if len(repo_parts) != 2:
        raise InstallError("--repo must be in owner/repo format.")
    if not args.path:
        raise InstallError("Missing --path for --repo.")
    paths = list(args.path)
    return Source(
        owner=repo_parts[0],
        repo=repo_parts[1],
        ref=DEFAULT_REF,
        paths=paths,
    )


def _default_dest() -> str:
    configured = os.environ.get("AGENT_FRIEND_PERSONAL_SKILLS_DIR")
    if configured:
        return configured
    codex_home = os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))
    return os.path.join(codex_home, "skills")


def _parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(
        description="Install or update a Skill from GitHub.", allow_abbrev=False
    )
    parser.add_argument("--repo", help="owner/repo")
    parser.add_argument("--url", help="https://github.com/owner/repo[/tree/ref/path]")
    parser.add_argument(
        "--path",
        nargs="+",
        help="Path(s) to skill(s) inside repo",
    )
    parser.add_argument(
        "--ref", help="Retired ambiguous option; use --branch, --pin, or --revision"
    )
    parser.add_argument("--branch", help="Persist a branch source and clear any pin")
    parser.add_argument("--pin", help="Install and persistently pin a commit or tag")
    parser.add_argument("--unpin", action="store_true", help="Resume the saved branch source")
    parser.add_argument(
        "--revision", help="Install a commit or tag once, keeping the branch source"
    )
    parser.add_argument(
        "--expect-commit", help="Require an exact commit (7-40 hex characters); no policy change"
    )
    parser.add_argument("--dest", help="Destination skills directory")
    parser.add_argument("--name", help="Destination skill name (defaults to basename of path)")
    parser.add_argument(
        "--method",
        choices=["auto", "download", "git"],
        default="auto",
    )
    parser.add_argument("--update", metavar="SKILL_NAME", help="Update one managed Skill")
    parser.add_argument(
        "--replace-local-changes",
        action="store_true",
        help="Confirm replacement of local changes during a managed update",
    )
    parser.add_argument(
        "--replace-unmanaged",
        action="store_true",
        help="Confirm replacement and adoption of one existing unmanaged Skill",
    )
    seen: set[str] = set()
    for argument in argv:
        if argument.startswith("--"):
            option = argument.split("=", 1)[0]
            if option in seen:
                parser.error(f"Duplicate option: {option}")
            seen.add(option)
    return parser.parse_args(argv, namespace=Args())


def _validate_mode(args: Args) -> None:
    if args.ref is not None:
        raise InstallError(
            "--ref is ambiguous and no longer changes installation state. Use --branch for "
            "future updates, --pin to freeze a version, --revision for a one-time version, "
            "or --expect-commit for verification only.",
            code="ambiguous_ref",
        )
    if sum((args.branch is not None, args.pin is not None, args.unpin)) > 1:
        raise InstallError(
            "--branch, --pin, and --unpin are mutually exclusive.", code="invalid_mode"
        )
    if args.revision is not None and (args.pin is not None or args.unpin):
        raise InstallError(
            "--revision cannot be combined with --pin or --unpin.", code="invalid_mode"
        )
    if args.unpin and not args.update:
        raise InstallError("--unpin requires --update.", code="invalid_mode")
    for value in (args.branch, args.pin, args.revision):
        if value is not None:
            validate_branch(value)
    if args.expect_commit is not None and not re.fullmatch(
        r"[0-9a-fA-F]{7,40}", args.expect_commit
    ):
        raise InstallError(
            "--expect-commit requires a 7-40 character hexadecimal SHA.", code="invalid_mode"
        )
    if args.repo and args.url:
        raise InstallError("Use only one of --repo or --url.", code="invalid_mode")
    if args.update:
        if args.repo or args.url or args.path or args.name or args.replace_unmanaged:
            raise InstallError(
                "--update cannot be combined with --repo, --url, --path, --name, or --replace-unmanaged.",
                code="invalid_mode",
            )
        validate_skill_name(args.update)
        return
    if args.replace_local_changes:
        raise InstallError(
            "--replace-local-changes is valid only with --update.", code="invalid_mode"
        )


def _normalize_install_targets(source: Source, args: Args) -> list[tuple[str, str]]:
    if not source.paths:
        raise InstallError("No skill paths provided.", code="invalid_source")
    if len(source.paths) > 1 and args.name:
        raise InstallError("--name is valid only with one --path.", code="invalid_mode")
    if args.replace_unmanaged and len(source.paths) != 1:
        raise InstallError("--replace-unmanaged requires exactly one --path.", code="invalid_mode")
    if args.replace_unmanaged and not args.name:
        raise InstallError("--replace-unmanaged requires an explicit --name.", code="invalid_mode")
    targets: list[tuple[str, str]] = []
    seen_names: set[str] = set()
    for raw_path in source.paths:
        normalized_path = normalize_source_path(raw_path)
        derived = normalized_path.rsplit("/", 1)[-1] if normalized_path != "." else ""
        skill_name = args.name if len(source.paths) == 1 else None
        skill_name = skill_name or derived
        if not skill_name:
            raise InstallError(
                "Repository-root Skills require an explicit --name.", code="invalid_skill_name"
            )
        validate_skill_name(skill_name)
        if skill_name in seen_names:
            raise InstallError(
                f"Multiple source paths resolve to {skill_name!r}.", code="invalid_source"
            )
        seen_names.add(skill_name)
        targets.append((normalized_path, skill_name))
    source.paths = [path for path, _name in targets]
    return targets


def _record_for_source(
    source: Source, path: str, content_sha256: str, *, ref: str | None = None
) -> InstallRecord:
    return make_record(
        repository=f"{source.owner}/{source.repo}",
        path=path,
        ref=ref or source.ref,
        content_sha256=content_sha256,
    )


def _configure_source(source: Source, args: Args, previous: InstallRecord | None = None) -> None:
    source.source_kind = previous.source_kind if previous else ("none" if args.pin else "branch")
    source.update_ref = previous.ref if previous else ("" if args.pin else DEFAULT_REF)
    source.pinned = bool(previous and previous.pinned_commit)
    if args.branch is not None:
        source.source_kind, source.update_ref, source.pinned = "branch", args.branch, False
    elif args.unpin:
        if source.source_kind != "branch":
            raise InstallError(
                "No known branch to resume; use --branch <name>.", code="unknown_branch"
            )
        source.pinned = False
    if args.revision is not None and (
        source.pinned
        or source.source_kind != "branch"
        or (previous is None and args.branch is None)
    ):
        raise InstallError(
            "--revision requires a known unpinned branch; use --branch <name>.", code="invalid_mode"
        )
    if args.pin is not None:
        source.pinned = True
    if args.pin or args.revision:
        source.ref, source.target_kind = args.pin or args.revision, "version"
    elif source.pinned:
        source.ref, source.target_kind = previous.pinned_commit, "version"
    else:
        source.ref, source.target_kind = source.update_ref, source.source_kind
    # Legacy records are preserved, but unsafe command arguments are never executed.
    validate_branch(source.ref)
    source.expect_commit = args.expect_commit
    source.check_branch = args.branch is not None and source.target_kind != "branch"


def _resolved_record(source: Source, path: str, digest: str) -> InstallRecord:
    commit = _full_commit(source.resolved_commit)
    return make_record(
        repository=f"{source.owner}/{source.repo}",
        path=path,
        ref=source.update_ref,
        content_sha256=digest,
        source_kind=source.source_kind,
        installed_commit=commit,
        pinned_commit=commit if source.pinned else None,
    )


def _describe_record(record: InstallRecord) -> str:
    if record.source_kind == "branch":
        source = f"branch={record.ref}"
    elif record.source_kind == "legacy":
        source = f"legacy_ref={record.ref} (intent unknown; use --branch or --pin to clarify)"
    else:
        source = "branch=none"
    policy = f"Pinned at {record.pinned_commit}; " if record.pinned_commit else ""
    return (
        f"{record.repository}:{record.path} {policy}{source} "
        f"installed_commit={record.installed_commit or 'unknown'} sha256={record.content_sha256}"
    )


def _source_path(repo_root: str, relative_path: str) -> Path:
    root = Path(repo_root).resolve()
    joined = root if relative_path == "." else root.joinpath(*relative_path.split("/"))
    try:
        resolved = joined.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InstallError(
            f"Skill path not found: {relative_path}", code="skill_path_not_found"
        ) from exc
    try:
        if os.path.commonpath((str(root), str(resolved))) != str(root):
            raise InstallError(
                f"Skill path escapes the repository: {relative_path}", code="unsafe_path"
            )
    except ValueError as exc:
        raise InstallError(
            f"Skill path escapes the repository: {relative_path}", code="unsafe_path"
        ) from exc
    return resolved


def _preflight_install(
    *,
    root: Path,
    source: Source,
    targets: list[tuple[str, str]],
    replace_unmanaged: bool,
) -> dict[str, tuple[str, str | None]]:
    records = load_records(root)
    planned: dict[str, tuple[str, str | None]] = {}
    for source_path, skill_name in targets:
        destination = root / skill_name
        destination_exists = os.path.lexists(destination)
        existing_record = records.get(skill_name)
        proposed = _record_for_source(source, source_path, "0" * 64)
        if existing_record is not None:
            if not existing_record.same_source(proposed):
                raise InstallError(
                    f"Managed Skill {skill_name!r} comes from "
                    f"{existing_record.repository}:{existing_record.path}, not "
                    f"{proposed.repository}:{proposed.path}.",
                    code="source_conflict",
                )
            if not destination_exists:
                raise InstallError(
                    f"Managed Skill {skill_name!r} is missing its installed directory.",
                    code="managed_destination_missing",
                )
            raise InstallError(
                f"Managed Skill {skill_name!r} already exists; use --update {skill_name}.",
                code="managed_destination_exists",
            )
        if destination_exists:
            if not replace_unmanaged:
                raise InstallError(
                    f"Skill {skill_name!r} exists without a source record. Its origin and local "
                    f"changes cannot be verified. Proposed source: {proposed.repository}:"
                    f"{proposed.path}@{proposed.ref}. Continuing will replace the entire existing "
                    "Skill directory. Retry the same source in this tool loop with "
                    "--replace-unmanaged and an explicit --name, keep these facts in the command "
                    "reason, and let the exact replacement follow the current session approval "
                    "mode.",
                    code="unmanaged_destination_exists",
                )
            planned[skill_name] = ("unmanaged", package_digest(destination))
            continue
        if replace_unmanaged:
            raise InstallError(
                f"Skill {skill_name!r} does not exist and cannot be adopted.",
                code="unmanaged_destination_missing",
            )
        planned[skill_name] = ("absent", None)
    return planned


def _run_install(args: Args, root: Path) -> None:
    source = _resolve_source(args)
    _configure_source(source, args)
    targets = _normalize_install_targets(source, args)
    planned = _preflight_install(
        root=root,
        source=source,
        targets=targets,
        replace_unmanaged=args.replace_unmanaged,
    )
    temporary = tempfile.mkdtemp(prefix="si-", dir=_tmp_root())
    try:
        repo_root = _prepare_repo(source, args.method, temporary)
        for source_path, skill_name in targets:
            candidate: Path | None = None
            try:
                candidate, content_sha256 = stage_candidate(
                    _source_path(repo_root, source_path), root, skill_name
                )
                record = _resolved_record(source, source_path, content_sha256)
                mode, expected_sha256 = planned[skill_name]
                result = commit_candidate(
                    root=root,
                    skill_name=skill_name,
                    candidate=candidate,
                    record=record,
                    expected_mode=mode,
                    expected_record=None,
                    expected_current_sha256=expected_sha256,
                )
                action = "Adopted" if mode == "unmanaged" else "Installed"
                print(
                    f"{action} {skill_name}: {_describe_record(record)} "
                    f"checked_ref={source.ref} to {root / skill_name}"
                )
                if result.cleanup_warning:
                    print(f"Warning: {result.cleanup_warning}", file=sys.stderr)
            finally:
                if candidate is not None:
                    remove_candidate(candidate)
    finally:
        if os.path.isdir(temporary):
            shutil.rmtree(temporary, ignore_errors=True)


def _run_update(args: Args, root: Path) -> None:
    assert args.update is not None
    skill_name = validate_skill_name(args.update)
    records = load_records(root)
    record = records.get(skill_name)
    destination = root / skill_name
    if record is None:
        if os.path.lexists(destination):
            raise InstallError(
                f"Skill {skill_name!r} exists without a source record. Provide its GitHub source "
                "and use the unmanaged replacement flow in this tool loop. Keep the missing-source "
                "and full replacement risk in the command reason; the exact replacement follows "
                "the current session approval mode.",
                code="unmanaged_destination_exists",
            )
        raise InstallError(
            f"Skill {skill_name!r} is not a managed GitHub Skill.", code="not_managed"
        )
    if not os.path.lexists(destination):
        raise InstallError(
            f"Managed Skill {skill_name!r} is missing its installed directory.",
            code="managed_destination_missing",
        )
    current_sha256 = package_digest(destination)
    if current_sha256 != record.content_sha256 and not args.replace_local_changes:
        raise InstallError(
            f"Skill {skill_name!r} has local changes. Source: "
            f"{_describe_record(record)}; recorded "
            f"sha256={record.content_sha256}; current sha256={current_sha256}. "
            "Continuing will discard those changes. Retry in this tool loop with "
            f"this option order: --update {skill_name} --replace-local-changes. Keep the Skill "
            "name, source, both digests, and loss warning in the command reason; the exact "
            "replacement follows the current session approval mode. Preserve all original "
            "version and verification options on the retry.",
            code="local_changes_detected",
        )

    owner, repo = record.repository.split("/", 1)
    source = Source(owner=owner, repo=repo, ref=record.ref, paths=[record.path])
    _configure_source(source, args, record)
    if (
        record.pinned_commit
        and not (args.branch or args.pin or args.unpin or args.revision)
        and current_sha256 == record.content_sha256
        and (args.expect_commit is None or len(args.expect_commit) == 40)
    ):
        expected = args.expect_commit.lower() if args.expect_commit else None
        _verify_commit(record.pinned_commit, expected)
        verify_pinned(root, skill_name, record)
        _write_receipt(root, skill_name, "pinned", record.content_sha256, record)
        print(f"Pinned: {skill_name}: {_describe_record(record)}; remote update check skipped.")
        return
    temporary = tempfile.mkdtemp(prefix="su-", dir=_tmp_root())
    candidate: Path | None = None
    try:
        repo_root = _prepare_repo(source, args.method, temporary)
        candidate, candidate_sha256 = stage_candidate(
            _source_path(repo_root, record.path), root, skill_name
        )
        next_record = _resolved_record(source, record.path, candidate_sha256)
        result = commit_candidate(
            root=root,
            skill_name=skill_name,
            candidate=candidate,
            record=next_record,
            expected_mode="managed",
            expected_record=record,
            expected_current_sha256=current_sha256,
        )
        _write_receipt(root, skill_name, result.status, result.previous_sha256, next_record)
        action = {
            "already_up_to_date": "Content unchanged",
            "metadata_updated": "Metadata updated; content unchanged",
            "committed": "Updated",
        }[result.status]
        print(
            f"{action} {skill_name}: {_describe_record(next_record)} "
            f"checked_ref={source.ref} old_sha256={result.previous_sha256} "
            f"new_sha256={next_record.content_sha256}"
        )
        if result.cleanup_warning:
            print(f"Warning: {result.cleanup_warning}", file=sys.stderr)
    finally:
        if candidate is not None:
            remove_candidate(candidate)
        if os.path.isdir(temporary):
            shutil.rmtree(temporary, ignore_errors=True)


def _write_receipt(
    root: Path, name: str, status: str, previous: str | None, record: InstallRecord
) -> None:
    receipt_path = os.environ.get("AGENT_FRIEND_SKILL_UPDATE_RECEIPT")
    nonce = os.environ.get("AGENT_FRIEND_SKILL_UPDATE_NONCE")
    if receipt_path and nonce:
        receipt = Path(receipt_path)
        payload = {
            "schema_version": 1,
            "nonce": nonce,
            "root": str(root),
            "skill_name": name,
            "status": status,
            "old_sha256": previous,
            "new_sha256": record.content_sha256,
        }
        temporary_receipt = receipt.with_suffix(".tmp")
        temporary_receipt.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary_receipt, receipt)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        _validate_mode(args)
        root = Path(args.dest or _default_dest()).resolve()
        if args.update:
            _run_update(args, root)
        else:
            _run_install(args, root)
        return 0
    except (InstallError, ManagedInstallError) as exc:
        code = getattr(exc, "code", "install_failed")
        print(f"Error [{code}]: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Error [filesystem_error]: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
