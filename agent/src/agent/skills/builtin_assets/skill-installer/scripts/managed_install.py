#!/usr/bin/env python3
# mypy: ignore-errors
"""Deterministic state and filesystem operations for managed Skill installs."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

LOCK_SCHEMA_VERSION = 2
LOCK_FILE_NAME = ".agent-friend-skill-lock.json"
MUTEX_FILE_NAME = ".agent-friend-skill-lock.lock"
MAX_SKILL_BYTES = 256 * 1024
MAX_OPENAI_YAML_BYTES = 64 * 1024
MAX_LOCK_BYTES = 4 * 1024 * 1024
MAX_LOCK_RECORDS = 2_000
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPOSITORY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_PLAIN_NON_STRING_RE = re.compile(
    r"^(?:null|~|true|false|yes|no|on|off|[-+]?(?:\d[\d_]*)"
    r"(?:\.\d[\d_]*)?(?:[eE][-+]?\d+)?|\d{4}-\d{1,2}-\d{1,2})$",
    re.IGNORECASE,
)


class ManagedInstallError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class InstallRecord:
    repository: str
    path: str
    ref: str
    content_sha256: str
    source_kind: str = "legacy"
    installed_commit: str | None = None
    pinned_commit: str | None = None

    def same_source(self, other: InstallRecord) -> bool:
        return self.repository.casefold() == other.repository.casefold() and self.path == other.path

    def to_json(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "path": self.path,
            "ref": self.ref,
            "content_sha256": self.content_sha256,
            "source_kind": self.source_kind,
            "installed_commit": self.installed_commit,
            "pinned_commit": self.pinned_commit,
        }


@dataclass(frozen=True)
class CommitResult:
    status: str
    previous_sha256: str | None
    cleanup_warning: str | None = None


def validate_skill_name(name: str) -> str:
    if not isinstance(name, str) or not (1 <= len(name) <= 64) or not _NAME_RE.fullmatch(name):
        raise ManagedInstallError(
            "invalid_skill_name",
            "Skill name must be 1-64 lowercase letters, digits, or single hyphens.",
        )
    return name


def normalize_repository(repository: str) -> str:
    value = repository.strip()
    if value.startswith("https://github.com/"):
        value = value.removeprefix("https://github.com/")
    elif value.startswith("http://github.com/"):
        value = value.removeprefix("http://github.com/")
    elif value.startswith("git@github.com:"):
        value = value.removeprefix("git@github.com:")
    value = value.strip("/")
    if value.casefold().endswith(".git"):
        value = value[:-4]
    parts = value.split("/")
    if (
        len(parts) != 2
        or not _OWNER_RE.fullmatch(parts[0])
        or not _REPOSITORY_NAME_RE.fullmatch(parts[1])
        or parts[1] in {".", ".."}
    ):
        raise ManagedInstallError("invalid_source", "GitHub repository must be in owner/repo form.")
    owner, repo = parts
    return f"{owner.casefold()}/{repo.casefold()}"


def normalize_source_path(path: str) -> str:
    value = path.replace("\\", "/").strip()
    if not value or "\x00" in value:
        raise ManagedInstallError("invalid_source", "Skill source path cannot be empty.")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ManagedInstallError(
            "invalid_source", "Skill source path must be relative to the repo."
        )
    parts: list[str] = []
    for part in value.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ManagedInstallError("invalid_source", "Skill source path cannot contain '..'.")
        parts.append(part)
    return "/".join(parts) if parts else "."


def make_record(
    *,
    repository: str,
    path: str,
    ref: str,
    content_sha256: str,
    source_kind: str = "legacy",
    installed_commit: str | None = None,
    pinned_commit: str | None = None,
) -> InstallRecord:
    if not all(isinstance(value, str) for value in (repository, path, ref, content_sha256)):
        raise ManagedInstallError("invalid_source", "Source fields must be strings.")
    normalized_ref = ref.strip()
    if (
        (not normalized_ref and source_kind != "none")
        or len(normalized_ref) > 1_024
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized_ref)
    ):
        raise ManagedInstallError("invalid_source", "Git ref must be a non-empty string.")
    if not _DIGEST_RE.fullmatch(content_sha256):
        raise ManagedInstallError("invalid_digest", "Skill content digest must be SHA-256.")
    if source_kind not in {"branch", "legacy", "none"}:
        raise ManagedInstallError("invalid_source", "Unknown update source kind.")
    for commit in (installed_commit, pinned_commit):
        if commit is not None and (not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit)):
            raise ManagedInstallError("invalid_source", "Commit must be a full lowercase Git SHA.")
    if source_kind != "legacy" and installed_commit is None:
        raise ManagedInstallError("invalid_source", "Explicit policy requires an installed commit.")
    if pinned_commit is not None and pinned_commit != installed_commit:
        raise ManagedInstallError("invalid_source", "Pinned and installed commits must match.")
    if source_kind == "none" and (normalized_ref or pinned_commit is None):
        raise ManagedInstallError("invalid_source", "A source-free record must be pinned.")
    if source_kind == "branch":
        validate_branch(normalized_ref)
    return InstallRecord(
        repository=normalize_repository(repository),
        path=normalize_source_path(path),
        ref=normalized_ref,
        content_sha256=content_sha256,
        source_kind=source_kind,
        installed_commit=installed_commit,
        pinned_commit=pinned_commit,
    )


def validate_branch(value: str) -> str:
    if (
        not value
        or value.startswith(("-", "/"))
        or value.endswith(("/", "."))
        or any(ord(c) <= 32 or c in "~^:?*[\\" or ord(c) == 127 for c in value)
        or any(token in value for token in ("..", "@{", "//"))
        or value == "@"
        or any(part.startswith(".") or part.endswith(".lock") for part in value.split("/"))
    ):
        raise ManagedInstallError("invalid_source", "Invalid branch name.")
    return value


def load_records(root: Path) -> dict[str, InstallRecord]:
    lock_path = root / LOCK_FILE_NAME
    if not os.path.lexists(lock_path):
        return {}
    if not lock_path.is_file() or _is_link(lock_path):
        raise ManagedInstallError("invalid_lock", f"{LOCK_FILE_NAME} must be a regular file.")
    try:
        raw = lock_path.read_bytes()
        if len(raw) > MAX_LOCK_BYTES:
            raise ManagedInstallError("invalid_lock", f"{LOCK_FILE_NAME} is too large.")
        if raw.startswith(b"\xef\xbb\xbf"):
            raise UnicodeDecodeError("utf-8", raw, 0, 3, "UTF-8 BOM is not allowed")
        parsed = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagedInstallError("invalid_lock", f"Cannot read {LOCK_FILE_NAME}: {exc}") from exc
    if not isinstance(parsed, dict) or set(parsed) != {"schema_version", "skills"}:
        raise ManagedInstallError(
            "invalid_lock", "Skill lock must contain schema_version and skills."
        )
    schema = parsed["schema_version"]
    if (
        type(schema) is not int
        or schema not in {1, LOCK_SCHEMA_VERSION}
        or not isinstance(parsed["skills"], dict)
    ):
        raise ManagedInstallError("invalid_lock", "Unsupported or invalid Skill lock schema.")
    if len(parsed["skills"]) > MAX_LOCK_RECORDS:
        raise ManagedInstallError("invalid_lock", "Skill lock contains too many records.")
    records: dict[str, InstallRecord] = {}
    for name, value in parsed["skills"].items():
        try:
            validate_skill_name(name)
            keys = {
                "repository",
                "path",
                "ref",
                "content_sha256",
            }
            if schema == 2:
                keys |= {"source_kind", "installed_commit", "pinned_commit"}
            if not isinstance(value, dict) or set(value) != keys:
                raise ManagedInstallError("invalid_lock", f"Invalid record for {name!r}.")
            if not all(
                isinstance(value[key], str) for key in keys - {"installed_commit", "pinned_commit"}
            ):
                raise ManagedInstallError("invalid_lock", f"Invalid record values for {name!r}.")
            record = make_record(
                repository=value["repository"],
                path=value["path"],
                ref=value["ref"],
                content_sha256=value["content_sha256"],
                source_kind=value.get("source_kind", "legacy"),
                installed_commit=value.get("installed_commit"),
                pinned_commit=value.get("pinned_commit"),
            )
            if {key: item for key, item in record.to_json().items() if key in keys} != value:
                raise ManagedInstallError("invalid_lock", f"Record for {name!r} is not normalized.")
            records[name] = record
        except ManagedInstallError as exc:
            if exc.code == "invalid_lock":
                raise
            raise ManagedInstallError(
                "invalid_lock", f"Invalid record for {name!r}: {exc}"
            ) from exc
    return records


def package_digest(package_root: Path) -> str:
    files = _collect_package_files(package_root)
    digest = hashlib.sha256()
    for relative, path in files:
        encoded = relative.encode("utf-8")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ManagedInstallError(
                "skill_read_failed", f"Cannot read package file {relative!r}: {exc}"
            ) from exc
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def validate_skill_package(package_root: Path, expected_name: str) -> None:
    validate_skill_name(expected_name)
    _collect_package_files(package_root)
    skill_md = package_root / "SKILL.md"
    frontmatter = _read_yaml_mapping(
        skill_md,
        max_bytes=MAX_SKILL_BYTES,
        missing_code="missing_skill_entry",
    )
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not (1 <= len(name) <= 64) or not _NAME_RE.fullmatch(name):
        raise ManagedInstallError("invalid_skill", "SKILL.md name is invalid.")
    if name != expected_name:
        raise ManagedInstallError(
            "skill_name_mismatch",
            f"SKILL.md name {name!r} does not match destination {expected_name!r}.",
        )
    if not isinstance(description, str) or not (1 <= len(description) <= 1_024):
        raise ManagedInstallError("invalid_skill", "SKILL.md description is invalid.")
    for key in ("license", "compatibility", "allowed-tools"):
        value = frontmatter.get(key)
        if value is not None and not isinstance(value, str):
            raise ManagedInstallError("invalid_skill", f"SKILL.md {key} must be a string.")
    metadata = frontmatter.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ManagedInstallError("invalid_skill", "SKILL.md metadata must be a mapping.")

    openai_yaml = package_root / "agents" / "openai.yaml"
    if openai_yaml.exists():
        policy_doc = _read_yaml_mapping(
            openai_yaml,
            max_bytes=MAX_OPENAI_YAML_BYTES,
            missing_code="invalid_openai_policy",
        )
        policy = policy_doc.get("policy")
        if policy is not None and not isinstance(policy, dict):
            raise ManagedInstallError(
                "invalid_openai_policy", "agents/openai.yaml policy must be a mapping."
            )
        if isinstance(policy, dict):
            implicit = policy.get("allow_implicit_invocation")
            if implicit is not None and not isinstance(implicit, bool):
                raise ManagedInstallError(
                    "invalid_openai_policy",
                    "policy.allow_implicit_invocation must be a boolean.",
                )


def stage_candidate(source: Path, root: Path, skill_name: str) -> tuple[Path, str]:
    validate_skill_package(source, skill_name)
    root.mkdir(parents=True, exist_ok=True)
    candidate = root / f".agent-friend-update-{skill_name}-{uuid.uuid4().hex}"
    try:
        _copy_package(source, candidate)
        validate_skill_package(candidate, skill_name)
        digest = package_digest(candidate)
        return candidate, digest
    except Exception:
        _remove_tree(candidate)
        raise


def commit_candidate(
    *,
    root: Path,
    skill_name: str,
    candidate: Path,
    record: InstallRecord,
    expected_mode: str,
    expected_record: InstallRecord | None,
    expected_current_sha256: str | None,
) -> CommitResult:
    validate_skill_name(skill_name)
    if expected_mode not in {"absent", "managed", "unmanaged"}:
        raise ValueError(f"Unsupported commit mode: {expected_mode}")
    if candidate.parent != root or not candidate.name.startswith(
        f".agent-friend-update-{skill_name}-"
    ):
        raise ManagedInstallError(
            "invalid_candidate", "Candidate is not owned by this transaction."
        )
    candidate_sha256 = package_digest(candidate)
    if candidate_sha256 != record.content_sha256:
        raise ManagedInstallError("candidate_changed", "Candidate changed before commit.")

    root.mkdir(parents=True, exist_ok=True)
    with _commit_lock(root):
        records = load_records(root)
        current_record = records.get(skill_name)
        destination = root / skill_name
        current_exists = os.path.lexists(destination)
        current_sha256 = package_digest(destination) if current_exists else None

        _verify_expected_state(
            expected_mode=expected_mode,
            expected_record=expected_record,
            expected_current_sha256=expected_current_sha256,
            current_record=current_record,
            current_exists=current_exists,
            current_sha256=current_sha256,
        )

        if current_record is not None and not current_record.same_source(record):
            raise ManagedInstallError(
                "source_conflict", "Cannot change a managed Skill's repository or path."
            )
        if current_record == record and current_sha256 == candidate_sha256:
            return CommitResult("already_up_to_date", current_sha256)
        if current_record is not None and current_sha256 == candidate_sha256:
            next_records = dict(records)
            next_records[skill_name] = record
            _write_records(root, next_records)
            return CommitResult("metadata_updated", current_sha256)

        nonce = uuid.uuid4().hex
        backup = root / f".agent-friend-backup-{skill_name}-{nonce}"
        moved_old = False
        moved_candidate = False
        try:
            if current_exists:
                destination.rename(backup)
                moved_old = True
            candidate.rename(destination)
            moved_candidate = True
            next_records = dict(records)
            next_records[skill_name] = record
            _write_records(root, next_records)
        except Exception as exc:
            rollback_errors: list[str] = []
            if moved_candidate and destination.exists():
                try:
                    destination.rename(candidate)
                except OSError as rollback_exc:
                    rollback_errors.append(f"cannot withdraw candidate: {rollback_exc}")
            if moved_old and backup.exists():
                try:
                    backup.rename(destination)
                except OSError as rollback_exc:
                    rollback_errors.append(f"cannot restore previous directory: {rollback_exc}")
            if rollback_errors:
                raise ManagedInstallError(
                    "rollback_failed",
                    f"Commit failed ({exc}); {'; '.join(rollback_errors)}. "
                    f"Preserved transaction directory: {backup.name}",
                ) from exc
            if isinstance(exc, ManagedInstallError):
                raise
            raise ManagedInstallError(
                "commit_failed", f"Cannot commit Skill update: {exc}"
            ) from exc

        cleanup_warning = None
        if backup.exists():
            try:
                _remove_tree(backup)
            except ManagedInstallError as exc:
                cleanup_warning = (
                    f"Could not remove internal transaction directory {backup.name}: {exc}"
                )
        return CommitResult("committed", current_sha256, cleanup_warning)


def remove_candidate(candidate: Path) -> None:
    if candidate.exists():
        _remove_tree(candidate)


def verify_pinned(root: Path, skill_name: str, record: InstallRecord) -> None:
    """Recheck a read-only pinned result under the same transaction lock."""
    with _commit_lock(root):
        destination = root / skill_name
        exists = os.path.lexists(destination)
        _verify_expected_state(
            expected_mode="managed",
            expected_record=record,
            expected_current_sha256=record.content_sha256,
            current_record=load_records(root).get(skill_name),
            current_exists=exists,
            current_sha256=package_digest(destination) if exists else None,
        )


def _verify_expected_state(
    *,
    expected_mode: str,
    expected_record: InstallRecord | None,
    expected_current_sha256: str | None,
    current_record: InstallRecord | None,
    current_exists: bool,
    current_sha256: str | None,
) -> None:
    matches = False
    if expected_mode == "absent":
        matches = not current_exists and current_record is None
    elif expected_mode == "managed":
        matches = (
            current_exists
            and current_record == expected_record
            and current_sha256 == expected_current_sha256
        )
    elif expected_mode == "unmanaged":
        matches = (
            current_exists and current_record is None and current_sha256 == expected_current_sha256
        )
    if not matches:
        raise ManagedInstallError(
            "concurrent_update",
            "Skill content or management state changed while the candidate was prepared; retry from the new state.",
        )


def _write_records(root: Path, records: dict[str, InstallRecord]) -> None:
    payload = {
        "schema_version": LOCK_SCHEMA_VERSION,
        "skills": {name: records[name].to_json() for name in sorted(records)},
    }
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temporary = root / f".{LOCK_FILE_NAME}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / LOCK_FILE_NAME)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise ManagedInstallError(
            "lock_write_failed", f"Cannot write {LOCK_FILE_NAME}: {exc}"
        ) from exc


@contextlib.contextmanager
def _commit_lock(root: Path, timeout_seconds: float = 30.0) -> Iterator[None]:
    lock_path = root / MUTEX_FILE_NAME
    if os.path.lexists(lock_path) and (_is_link(lock_path) or not lock_path.is_file()):
        raise ManagedInstallError("lock_failed", "Commit lock must be a regular file.")
    try:
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise ManagedInstallError("lock_failed", f"Cannot open commit lock: {exc}") from exc
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + timeout_seconds
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ManagedInstallError(
                            "lock_timeout", "Timed out waiting for another Skill update."
                        ) from exc
                    time.sleep(0.05)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise ManagedInstallError(
                            "lock_timeout", "Timed out waiting for another Skill update."
                        ) from exc
                    time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _collect_package_files(package_root: Path) -> list[tuple[str, Path]]:
    if not package_root.is_dir() or _is_link(package_root):
        raise ManagedInstallError("unsafe_package", "Skill package must be a regular directory.")
    collected: list[tuple[str, Path]] = []

    def visit(directory: Path, relative_parent: Path) -> None:
        try:
            with os.scandir(directory) as scanner:
                entries = sorted(scanner, key=lambda item: item.name.encode("utf-8"))
        except OSError as exc:
            raise ManagedInstallError(
                "skill_read_failed", f"Cannot scan Skill package: {exc}"
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            relative = relative_parent / entry.name
            if relative_parent == Path() and entry.name == ".git":
                continue
            if _is_link(path):
                raise ManagedInstallError(
                    "unsafe_package", f"Skill package contains a link: {relative.as_posix()}"
                )
            try:
                if entry.is_dir(follow_symlinks=False):
                    visit(path, relative)
                elif entry.is_file(follow_symlinks=False):
                    collected.append((relative.as_posix(), path))
                else:
                    raise ManagedInstallError(
                        "unsafe_package",
                        f"Skill package contains a non-regular entry: {relative.as_posix()}",
                    )
            except OSError as exc:
                raise ManagedInstallError(
                    "skill_read_failed", f"Cannot inspect {relative.as_posix()}: {exc}"
                ) from exc

    visit(package_root, Path())
    collected.sort(key=lambda item: item[0].encode("utf-8"))
    return collected


def _copy_package(source: Path, destination: Path) -> None:
    if destination.exists():
        raise ManagedInstallError("invalid_candidate", "Candidate destination already exists.")
    files = _collect_package_files(source)
    destination.mkdir(parents=False)
    try:
        for relative, source_file in files:
            destination_file = destination / Path(relative)
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, destination_file)
    except Exception:
        _remove_tree(destination)
        raise


def _remove_tree(path: Path) -> None:
    if _is_link(path):
        raise ManagedInstallError(
            "unsafe_package", f"Refusing to remove linked path {path.name!r}."
        )
    if path.exists():
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise ManagedInstallError(
                "cleanup_failed", f"Cannot remove {path.name!r}: {exc}"
            ) from exc


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(os.path, "isjunction", lambda _path: False)(path))


def _read_yaml_mapping(path: Path, *, max_bytes: int, missing_code: str) -> dict[str, object]:
    if not path.is_file() or _is_link(path):
        raise ManagedInstallError(missing_code, f"Required file is missing: {path.name}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManagedInstallError("skill_read_failed", f"Cannot read {path.name}: {exc}") from exc
    if len(raw) > max_bytes:
        raise ManagedInstallError("invalid_skill", f"{path.name} exceeds {max_bytes} bytes.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManagedInstallError("invalid_utf8", f"{path.name} must be UTF-8.") from exc
    if path.name == "SKILL.md":
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ManagedInstallError("invalid_skill", "SKILL.md must start with YAML frontmatter.")
        try:
            end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
        except StopIteration as exc:
            raise ManagedInstallError(
                "invalid_skill", "SKILL.md frontmatter is not closed."
            ) from exc
        text = "\n".join(lines[1:end])
    try:
        return _parse_simple_yaml_mapping(text)
    except ValueError as exc:
        raise ManagedInstallError(
            "unsupported_yaml", f"Unsupported YAML in {path.name}: {exc}"
        ) from exc


def _parse_simple_yaml_mapping(text: str) -> dict[str, object]:
    lines = text.splitlines()

    def next_content(index: int) -> int:
        while index < len(lines):
            stripped = lines[index].strip()
            if stripped and not stripped.startswith("#"):
                return index
            index += 1
        return index

    def parse_mapping(index: int, indent: int) -> tuple[dict[str, object], int]:
        result: dict[str, object] = {}
        while True:
            index = next_content(index)
            if index >= len(lines):
                break
            raw_line = lines[index]
            if "\t" in raw_line[: len(raw_line) - len(raw_line.lstrip())]:
                raise ValueError("tabs are not supported for indentation")
            current_indent = len(raw_line) - len(raw_line.lstrip(" "))
            if current_indent < indent:
                break
            if current_indent > indent:
                raise ValueError(f"unexpected indentation on line {index + 1}")
            content = raw_line[current_indent:]
            if content.startswith("-"):
                raise ValueError("sequences are not supported")
            key, separator, raw_value = content.partition(":")
            key = key.strip()
            if not separator or not key or key.startswith(("'", '"')):
                raise ValueError(f"expected a plain mapping key on line {index + 1}")
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            raw_value = raw_value.strip()
            index += 1
            if raw_value in {"|", "|-", "|+", ">", ">-", ">+"}:
                block_lines: list[str] = []
                while index < len(lines):
                    candidate = lines[index]
                    candidate_indent = len(candidate) - len(candidate.lstrip(" "))
                    if candidate.strip() and candidate_indent <= current_indent:
                        break
                    block_lines.append(candidate)
                    index += 1
                nonempty_indents = [
                    len(line) - len(line.lstrip(" ")) for line in block_lines if line.strip()
                ]
                if not nonempty_indents:
                    result[key] = ""
                    continue
                block_indent = min(nonempty_indents)
                values = [line[block_indent:] if line.strip() else "" for line in block_lines]
                if raw_value.startswith(">"):
                    result[key] = " ".join(value.strip() for value in values).strip()
                else:
                    result[key] = "\n".join(values)
                continue
            if raw_value == "":
                child_index = next_content(index)
                if child_index >= len(lines):
                    result[key] = None
                    index = child_index
                    continue
                child_line = lines[child_index]
                child_indent = len(child_line) - len(child_line.lstrip(" "))
                if child_indent <= current_indent:
                    result[key] = None
                    continue
                child, index = parse_mapping(child_index, child_indent)
                result[key] = child
                continue
            result[key] = _parse_simple_yaml_scalar(raw_value)
        return result, index

    parsed, final_index = parse_mapping(0, 0)
    if next_content(final_index) != len(lines):
        raise ValueError("trailing unsupported content")
    return parsed


def _parse_simple_yaml_scalar(raw: str) -> object:
    value = _strip_inline_comment(raw).strip()
    if not value:
        return None
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid double-quoted scalar") from exc
        if not isinstance(parsed, str):
            raise ValueError("double-quoted scalar must be a string")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ValueError("invalid single-quoted scalar")
        return value[1:-1].replace("''", "'")
    lowered = value.casefold()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "~"}:
        return None
    if value.startswith(("[", "{", "&", "*", "!")) or _PLAIN_NON_STRING_RE.fullmatch(value):
        raise ValueError(f"ambiguous or unsupported scalar {value!r}")
    return value


def _strip_inline_comment(raw: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            continue
        if character == "#" and quote is None and (index == 0 or raw[index - 1].isspace()):
            return raw[:index]
    return raw
