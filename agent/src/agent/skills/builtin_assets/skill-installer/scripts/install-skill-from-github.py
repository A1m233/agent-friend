#!/usr/bin/env python3
# mypy: ignore-errors
"""Install a skill from a GitHub repo path into the host's personal skills root."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path

from github_utils import github_request
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
    validate_skill_name,
)

DEFAULT_REF = "main"


@dataclass
class Args:
    url: str | None = None
    repo: str | None = None
    path: list[str] | None = None
    ref: str | None = None
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


class InstallError(Exception):
    def __init__(self, message: str, *, code: str = "install_failed") -> None:
        super().__init__(message)
        self.code = code


def _tmp_root() -> str:
    base = os.path.join(tempfile.gettempdir(), "agent-friend")
    os.makedirs(base, exist_ok=True)
    return base


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
    except (urllib.error.URLError, OSError) as exc:
        raise InstallError(f"Download failed: {exc}", code="download_failed") from exc
    try:
        with open(zip_path, "wb") as file_handle:
            file_handle.write(payload)
        with zipfile.ZipFile(zip_path, "r") as zip_file:
            _safe_extract_zip(zip_file, dest_dir)
            top_levels = {name.split("/")[0] for name in zip_file.namelist() if name}
    except (OSError, zipfile.BadZipFile) as exc:
        raise InstallError(f"Downloaded archive is invalid: {exc}", code="archive_invalid") from exc
    if not top_levels:
        raise InstallError("Downloaded archive was empty.")
    if len(top_levels) != 1:
        raise InstallError("Unexpected archive layout.")
    return os.path.join(dest_dir, next(iter(top_levels)))


def _run_git(args: list[str]) -> None:
    try:
        result = subprocess.run(args, capture_output=True, text=True)
    except OSError as exc:
        raise InstallError(f"Cannot run git: {exc}", code="git_failed") from exc
    if result.returncode != 0:
        raise InstallError(result.stderr.strip() or "Git command failed.", code="git_failed")


def _safe_extract_zip(zip_file: zipfile.ZipFile, dest_dir: str) -> None:
    dest_root = os.path.realpath(dest_dir)
    for info in zip_file.infolist():
        extracted_path = os.path.realpath(os.path.join(dest_dir, info.filename))
        if extracted_path == dest_root or extracted_path.startswith(dest_root + os.sep):
            continue
        raise InstallError("Archive contains files outside the destination.")
    zip_file.extractall(dest_dir)


def _validate_relative_path(path: str) -> None:
    if os.path.isabs(path) or os.path.normpath(path).startswith(".."):
        raise InstallError("Skill path must be a relative path inside the repo.")


def _git_sparse_checkout(repo_url: str, ref: str, paths: list[str], dest_dir: str) -> str:
    repo_dir = os.path.join(dest_dir, "repo")
    clone_cmd = [
        "git",
        "clone",
        "--filter=blob:none",
        "--depth",
        "1",
        "--sparse",
        "--single-branch",
        "--branch",
        ref,
        repo_url,
        repo_dir,
    ]
    try:
        _run_git(clone_cmd)
    except InstallError:
        _run_git(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--depth",
                "1",
                "--sparse",
                "--single-branch",
                repo_url,
                repo_dir,
            ]
        )
    _run_git(["git", "-C", repo_dir, "sparse-checkout", "set", *paths])
    _run_git(["git", "-C", repo_dir, "checkout", ref])
    return repo_dir


def _build_repo_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}.git"


def _build_repo_ssh(owner: str, repo: str) -> str:
    return f"git@github.com:{owner}/{repo}.git"


def _prepare_repo(source: Source, method: str, tmp_dir: str) -> str:
    if method in ("download", "auto"):
        try:
            return _download_repo_zip(source.owner, source.repo, source.ref, tmp_dir)
        except InstallError as exc:
            if method == "download":
                raise
            if exc.code == "download_failed" or str(exc).startswith("Download failed: HTTP "):
                pass
            else:
                raise
    if method in ("git", "auto"):
        repo_url = source.repo_url or _build_repo_url(source.owner, source.repo)
        try:
            return _git_sparse_checkout(repo_url, source.ref, source.paths, tmp_dir)
        except InstallError as https_exc:
            repo_url = _build_repo_ssh(source.owner, source.repo)
            try:
                return _git_sparse_checkout(repo_url, source.ref, source.paths, tmp_dir)
            except InstallError as ssh_exc:
                raise InstallError(
                    f"HTTPS git clone failed: {https_exc}\nSSH git clone failed: {ssh_exc}"
                ) from ssh_exc
    raise InstallError("Unsupported method.")


def _resolve_source(args: Args) -> Source:
    if args.url:
        owner, repo, ref, url_path = _parse_github_url(args.url, args.ref or DEFAULT_REF)
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
        return _resolve_source(Args(url=args.repo, repo=None, path=args.path, ref=args.ref))

    repo_parts = [p for p in args.repo.split("/") if p]
    if len(repo_parts) != 2:
        raise InstallError("--repo must be in owner/repo format.")
    if not args.path:
        raise InstallError("Missing --path for --repo.")
    paths = list(args.path)
    return Source(
        owner=repo_parts[0],
        repo=repo_parts[1],
        ref=args.ref or DEFAULT_REF,
        paths=paths,
    )


def _default_dest() -> str:
    configured = os.environ.get("AGENT_FRIEND_PERSONAL_SKILLS_DIR")
    if configured:
        return configured
    codex_home = os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))
    return os.path.join(codex_home, "skills")


def _parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Install or update a Skill from GitHub.")
    parser.add_argument("--repo", help="owner/repo")
    parser.add_argument("--url", help="https://github.com/owner/repo[/tree/ref/path]")
    parser.add_argument(
        "--path",
        nargs="+",
        help="Path(s) to skill(s) inside repo",
    )
    parser.add_argument("--ref")
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
    return parser.parse_args(argv, namespace=Args())


def _validate_mode(args: Args) -> None:
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
    targets = _normalize_install_targets(source, args)
    planned = _preflight_install(
        root=root,
        source=source,
        targets=targets,
        replace_unmanaged=args.replace_unmanaged,
    )
    temporary = tempfile.mkdtemp(prefix="skill-install-", dir=_tmp_root())
    try:
        repo_root = _prepare_repo(source, args.method, temporary)
        for source_path, skill_name in targets:
            candidate: Path | None = None
            try:
                candidate, content_sha256 = stage_candidate(
                    _source_path(repo_root, source_path), root, skill_name
                )
                record = _record_for_source(source, source_path, content_sha256)
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
                if result.status == "already_up_to_date":
                    actual = load_records(root)[skill_name]
                    print(
                        f"Already up to date: {skill_name} from "
                        f"{actual.repository}:{actual.path}@{actual.ref} "
                        f"sha256={actual.content_sha256}"
                    )
                else:
                    action = "Adopted" if mode == "unmanaged" else "Installed"
                    print(
                        f"{action} {skill_name} from {record.repository}:{record.path}@{record.ref} "
                        f"sha256={record.content_sha256} to {root / skill_name}"
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
            f"{record.repository}:{record.path}@{record.ref}; recorded "
            f"sha256={record.content_sha256}; current sha256={current_sha256}. "
            "Continuing will discard those changes. Retry in this tool loop with "
            f"this option order: --update {skill_name} --replace-local-changes. Keep the Skill "
            "name, source, both digests, and loss warning in the command reason; the exact "
            "replacement follows the current session approval mode. If the original command "
            "included --ref, keep the same ref between the skill name and replacement flag.",
            code="local_changes_detected",
        )

    owner, repo = record.repository.split("/", 1)
    requested_ref = args.ref or record.ref
    source = Source(owner=owner, repo=repo, ref=requested_ref, paths=[record.path])
    temporary = tempfile.mkdtemp(prefix="skill-update-", dir=_tmp_root())
    candidate: Path | None = None
    try:
        repo_root = _prepare_repo(source, args.method, temporary)
        candidate, candidate_sha256 = stage_candidate(
            _source_path(repo_root, record.path), root, skill_name
        )
        next_record = _record_for_source(source, record.path, candidate_sha256, ref=requested_ref)
        result = commit_candidate(
            root=root,
            skill_name=skill_name,
            candidate=candidate,
            record=next_record,
            expected_mode="managed",
            expected_record=record,
            expected_current_sha256=current_sha256,
        )
        receipt_path = os.environ.get("AGENT_FRIEND_SKILL_UPDATE_RECEIPT")
        nonce = os.environ.get("AGENT_FRIEND_SKILL_UPDATE_NONCE")
        if receipt_path and nonce:
            receipt = Path(receipt_path)
            payload = {
                "schema_version": 1,
                "nonce": nonce,
                "root": str(root),
                "skill_name": skill_name,
                "status": result.status,
                "old_sha256": result.previous_sha256,
                "new_sha256": next_record.content_sha256,
            }
            temporary_receipt = receipt.with_suffix(".tmp")
            temporary_receipt.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(temporary_receipt, receipt)
        if result.status == "already_up_to_date":
            actual = load_records(root)[skill_name]
            print(
                f"Already up to date: {skill_name} from "
                f"{actual.repository}:{actual.path}@{actual.ref} "
                f"sha256={actual.content_sha256}"
            )
        else:
            print(
                f"Updated {skill_name} from {next_record.repository}:{next_record.path}"
                f"@{next_record.ref} old_sha256={result.previous_sha256} "
                f"new_sha256={next_record.content_sha256}"
            )
        if result.cleanup_warning:
            print(f"Warning: {result.cleanup_warning}", file=sys.stderr)
    finally:
        if candidate is not None:
            remove_candidate(candidate)
        if os.path.isdir(temporary):
            shutil.rmtree(temporary, ignore_errors=True)


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
