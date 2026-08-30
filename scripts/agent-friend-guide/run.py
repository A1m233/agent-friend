from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from agent.skills import SkillLoader, SkillRoot

SCHEMA_VERSION = 1
SKILL_NAME = "agent-friend-guide"
SOURCE_REPOSITORY = "https://github.com/A1m233/agent-friend-guide"
DEFAULT_BRANCH = "main"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ROUTED_REFERENCE_RE = re.compile(r"`(references/[a-z0-9-]+\.md)`")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
EXPECTED_REFERENCES = {
    "arknights-avatar-models.md",
    "data-and-privacy.md",
    "getting-started.md",
    "model-credentials.md",
    "nearby-place-credentials.md",
    "product-overview.md",
    "settings-reference.md",
    "troubleshooting.md",
    "using-agent-friend.md",
    "voice-credentials.md",
    "weather-credentials.md",
    "web-search-credentials.md",
}
ROUTED_REFERENCES = EXPECTED_REFERENCES - {"getting-started.md"}
RELEASE_MANIFEST_ASSET = f"{SKILL_NAME}.manifest.json"
RELEASE_PACKAGE_ASSET = f"{SKILL_NAME}.zip"
RELEASE_TAG_RE = re.compile(rf"^{re.escape(SKILL_NAME)}-r([1-9][0-9]*)$")
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
REQUIRED_CONTENT = {
    "arknights-avatar-models.md": (
        "https://github.com/isHarryh/Ark-Models",
        "Spine 3.8",
        "非商用",
        "scripts/ark-models.py",
    ),
    "model-credentials.md": (
        "DeepSeek",
        "Kimi",
        "https://platform.deepseek.com/api_keys",
        "https://platform.kimi.ai/console/api-keys",
    ),
    "web-search-credentials.md": ("Tavily", "https://app.tavily.com/home"),
    "nearby-place-credentials.md": ("高德", "Web 服务"),
    "weather-credentials.md": ("和风天气", "API Key", "API Host"),
    "voice-credentials.md": (
        "语音输入",
        "语音通话",
        "APP Key",
        "APP ID",
        "Access Token",
        "Access Key",
        "Secret Key",
        "RTC AppID",
        "RTC AppKey",
    ),
}


class GuideError(RuntimeError):
    pass


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_guide_repo() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise GuideError("LOCALAPPDATA is required for the managed guide checkout")
        root = Path(base)
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "agent-friend" / "maintenance" / SKILL_NAME


def package_root(guide_repo: Path) -> Path:
    return guide_repo / "skills" / SKILL_NAME


def builtin_root(product_repo: Path) -> Path:
    return product_repo / "agent" / "src" / "agent" / "skills" / "builtin_assets"


def git(repo: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise GuideError(f"git {' '.join(args)} failed in {repo}: {detail}")
    return completed.stdout.strip()


def git_succeeds(repo: Path, *args: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode == 0


def normalized_repository_url(value: str) -> str:
    normalized = value.strip().replace("\\", "/").rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized.lower()


def prepare_checkout(
    guide_repo: Path, *, source_repository: str = SOURCE_REPOSITORY
) -> dict[str, Any]:
    target = guide_repo.resolve()
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                "git",
                "clone",
                "--branch",
                DEFAULT_BRANCH,
                "--single-branch",
                source_repository,
                str(target),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise GuideError(f"failed to clone managed guide checkout: {detail}")
        action = "cloned"
    else:
        if not target.is_dir() or not git_succeeds(target, "rev-parse", "--git-dir"):
            raise GuideError(f"managed guide checkout is not a Git repository: {target}")
        action = "unchanged"

    origin = git(target, "remote", "get-url", "origin")
    if normalized_repository_url(origin) != normalized_repository_url(source_repository):
        raise GuideError(
            f"managed guide checkout origin differs from {source_repository}: {origin}"
        )
    if git_changes(target):
        raise GuideError("managed guide checkout must be clean before prepare")
    branch = git(target, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch != DEFAULT_BRANCH:
        raise GuideError(
            f"managed guide checkout must be on {DEFAULT_BRANCH}, found {branch or 'detached HEAD'}"
        )

    before = git_head(target)
    git(target, "fetch", "--prune", "origin", DEFAULT_BRANCH)
    remote_ref = f"refs/remotes/origin/{DEFAULT_BRANCH}"
    remote_head = git(target, "rev-parse", remote_ref)
    if not COMMIT_RE.fullmatch(remote_head):
        raise GuideError(f"origin/{DEFAULT_BRANCH} is not a full commit")
    if not git_succeeds(target, "merge-base", "--is-ancestor", before, remote_head):
        raise GuideError(
            f"managed guide checkout cannot fast-forward: local {before}, origin {remote_head}"
        )
    if before != remote_head:
        git(target, "merge", "--ff-only", remote_ref)
        action = "fast-forwarded"
    if git_changes(target):
        raise GuideError("managed guide checkout became dirty during prepare")
    return {
        "path": str(target),
        "action": action,
        "head": git_head(target),
        "origin": origin,
        "branch": branch,
    }


def git_head(repo: Path) -> str:
    head = git(repo, "rev-parse", "HEAD")
    if not COMMIT_RE.fullmatch(head):
        raise GuideError(f"repository HEAD is not a full commit: {repo}")
    return head


def git_changes(repo: Path) -> list[str]:
    output = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    return [line for line in output.splitlines() if line]


def origin_contains_exact_ref(repo: Path, commit: str) -> bool:
    output = git(
        repo,
        "ls-remote",
        "--heads",
        "--tags",
        "origin",
        check=False,
    )
    return any(line.split(maxsplit=1)[0] == commit for line in output.splitlines() if line)


def regular_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise GuideError(f"Skill package not found: {root}")
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if is_link(path):
            raise GuideError(f"links are not allowed in the Skill package: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise GuideError(f"non-regular package entry: {path}")
        files.append(path)
    return files


def is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(os.path, "isjunction", lambda _: False)(path))


def package_digest(root: Path, *, exclude_source_metadata: bool) -> str:
    digest = hashlib.sha256()
    for path in regular_files(root):
        relative = path.relative_to(root).as_posix()
        if exclude_source_metadata and relative == "metadata/source.json":
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuideError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GuideError(f"JSON root must be an object: {path}")
    return value


def load_source_metadata(root: Path) -> dict[str, Any]:
    path = root / "metadata" / "source.json"
    value = load_json(path)
    required = {
        "schema_version",
        "product_repository",
        "reviewed_product_commit",
        "reviewed_at",
        "product_version",
        "content_sha256",
    }
    if set(value) != required:
        raise GuideError(f"source.json fields differ from schema: {sorted(value)}")
    if value["schema_version"] != 1:
        raise GuideError("source.json schema_version must be 1")
    commit = value["reviewed_product_commit"]
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise GuideError("reviewed_product_commit must be a 40-character lowercase commit")
    digest = value["content_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise GuideError("content_sha256 must be a lowercase SHA-256 digest")
    actual = package_digest(root, exclude_source_metadata=True)
    if digest != actual:
        raise GuideError(f"content_sha256 mismatch: expected {digest}, actual {actual}")
    return value


def frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GuideError(f"invalid UTF-8 file {path}: {exc}") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise GuideError(f"missing YAML frontmatter: {path}")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise GuideError(f"unterminated YAML frontmatter: {path}") from exc
    try:
        value = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise GuideError(f"invalid YAML frontmatter {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GuideError(f"frontmatter root must be a mapping: {path}")
    return value


def ensure_relative_links(root: Path, files: Iterable[Path]) -> None:
    canonical_root = root.resolve()
    for path in files:
        if path.suffix.lower() != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK_RE.findall(text):
            target = raw_target.strip().split("#", 1)[0]
            if not target or re.match(r"^(?:https?|mailto):", target):
                continue
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(canonical_root)
            except ValueError as exc:
                raise GuideError(f"relative link escapes package in {path}: {raw_target}") from exc
            if not resolved.exists():
                raise GuideError(f"relative link target missing in {path}: {raw_target}")


def verify_package(root: Path) -> dict[str, Any]:
    files = regular_files(root)
    for path in files:
        if path.suffix.lower() in {".md", ".yaml", ".yml", ".json", ".txt"}:
            try:
                path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise GuideError(f"package text is not UTF-8: {path}: {exc}") from exc

    skill = root / "SKILL.md"
    metadata = frontmatter(skill)
    if metadata.get("name") != SKILL_NAME:
        raise GuideError(f"SKILL.md frontmatter name must be {SKILL_NAME}")
    if root.name != SKILL_NAME:
        raise GuideError("Skill directory and frontmatter name differ")
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip():
        raise GuideError("SKILL.md description is required")
    catalog = SkillLoader().load((SkillRoot("builtin", root),), generation=0)
    records = [record for record in catalog.entries if record.name == SKILL_NAME]
    if len(records) != 1 or catalog.issues:
        issue_codes = ",".join(issue.code for issue in catalog.issues) or "missing_skill"
        raise GuideError(f"Skill loader validation failed: {issue_codes}")

    references = root / "references"
    actual_references = {path.name for path in references.glob("*.md") if path.is_file()}
    missing_references = EXPECTED_REFERENCES - actual_references
    if missing_references:
        raise GuideError(
            f"required reference set is incomplete: missing={sorted(missing_references)}"
        )
    if (references / "getting-started.md").stat().st_size == 0:
        raise GuideError("getting-started.md must be a non-empty placeholder")

    skill_text = skill.read_text(encoding="utf-8")
    routed = {
        Path(match).name
        for match in ROUTED_REFERENCE_RE.findall(skill_text)
        if Path(match).name != "getting-started.md"
    }
    missing_routes = ROUTED_REFERENCES - routed
    if missing_routes:
        raise GuideError(
            f"SKILL.md required routes are incomplete: missing={sorted(missing_routes)}"
        )
    for reference, required_terms in REQUIRED_CONTENT.items():
        text = (references / reference).read_text(encoding="utf-8")
        missing = [term for term in required_terms if term not in text]
        if missing:
            raise GuideError(f"{reference} is missing required content: {missing}")

    openai_path = root / "agents" / "openai.yaml"
    try:
        openai = yaml.safe_load(openai_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise GuideError(f"invalid agents/openai.yaml: {exc}") from exc
    if not isinstance(openai, dict):
        raise GuideError("agents/openai.yaml root must be a mapping")
    policy = openai.get("policy")
    if not isinstance(policy, dict) or policy.get("allow_implicit_invocation") is not True:
        raise GuideError("public Skill must allow implicit invocation")

    source = load_source_metadata(root)
    ensure_relative_links(root, files)
    return {
        "source": source,
        "contentSha256": source["content_sha256"],
        "snapshotSha256": package_digest(root, exclude_source_metadata=False),
        "fileCount": len(files),
    }


def load_manifest(product_repo: Path) -> dict[str, Any]:
    return load_json(builtin_root(product_repo) / "manifest.json")


def builtin_state(product_repo: Path) -> dict[str, Any]:
    root = builtin_root(product_repo)
    manifest = load_manifest(product_repo)
    skills = manifest.get("skills") if manifest.get("schema_version") == 2 else None
    guide = skills.get(SKILL_NAME) if isinstance(skills, dict) else None
    package = root / SKILL_NAME
    if not isinstance(guide, dict) or not package.is_dir():
        return {
            "present": False,
            "sourceCommit": None,
            "snapshotSha256": None,
        }
    actual = package_digest(package, exclude_source_metadata=False)
    return {
        "present": True,
        "sourceCommit": guide.get("source_commit"),
        "snapshotSha256": guide.get("snapshot_sha256"),
        "actualSnapshotSha256": actual,
        "valid": guide.get("snapshot_sha256") == actual,
    }


def status_payload(product_repo: Path, guide_repo: Path) -> dict[str, Any]:
    guide_head = git_head(guide_repo)
    guide_changes = git_changes(guide_repo)
    product_changes = git_changes(product_repo)
    package = package_root(guide_repo)
    try:
        verified = verify_package(package)
        verify_error = None
    except GuideError as exc:
        verified = None
        verify_error = str(exc)
    builtin = builtin_state(product_repo)
    matches = bool(
        verified
        and builtin.get("valid")
        and builtin.get("sourceCommit") == guide_head
        and builtin.get("snapshotSha256") == verified["snapshotSha256"]
    )
    remote_visible = origin_contains_exact_ref(guide_repo, guide_head)
    if verify_error is not None:
        phase = "guide-package-invalid"
    elif guide_changes:
        phase = "guide-working-tree-dirty"
    elif not remote_visible:
        phase = "guide-commit-not-visible-on-origin"
    elif matches:
        phase = "guide-and-baseline-aligned"
    else:
        phase = "guide-commit-ready"
    return {
        "schemaVersion": SCHEMA_VERSION,
        "product": {
            "head": git_head(product_repo),
            "dirty": bool(product_changes),
            "changes": product_changes,
        },
        "guide": {
            "path": str(guide_repo.resolve()),
            "head": guide_head,
            "dirty": bool(guide_changes),
            "changes": guide_changes,
            "originHasExactRef": remote_visible,
            "verified": verified is not None,
            "verificationError": verify_error,
            "source": verified["source"] if verified else None,
            "snapshotSha256": verified["snapshotSha256"] if verified else None,
        },
        "builtin": builtin,
        "baselineMatchesGuide": matches,
        "matches": matches,
        "phase": phase,
    }


def updated_manifest(
    current: dict[str, Any], *, guide_commit: str, snapshot_sha256: str
) -> dict[str, Any]:
    if current.get("schema_version") != 2 or not isinstance(current.get("skills"), dict):
        raise GuideError("builtin manifest must use schema_version 2 before sync")
    skills = dict(current["skills"])
    if "skill-creator" not in skills:
        raise GuideError("builtin manifest is missing skill-creator provenance")
    skills[SKILL_NAME] = {
        "source_repository": SOURCE_REPOSITORY,
        "source_commit": guide_commit,
        "source_path": f"skills/{SKILL_NAME}",
        "license": "Apache-2.0",
        "snapshot_sha256": snapshot_sha256,
    }
    return {"schema_version": 2, "skills": skills}


def replace_snapshot(product_repo: Path, source: Path, manifest_value: dict[str, Any]) -> None:
    root = builtin_root(product_repo)
    target = root / SKILL_NAME
    staging = root / f".{SKILL_NAME}.staging-{os.getpid()}-{uuid4().hex}"
    backup = root / f".{SKILL_NAME}.backup-{os.getpid()}-{uuid4().hex}"
    manifest = root / "manifest.json"
    manifest_staging = root / f".manifest.staging-{os.getpid()}-{uuid4().hex}.json"
    moved_old = False
    installed_new = False
    try:
        shutil.copytree(source, staging, symlinks=False)
        manifest_staging.write_text(
            json.dumps(manifest_value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if target.exists():
            os.replace(target, backup)
            moved_old = True
        os.replace(staging, target)
        installed_new = True
        os.replace(manifest_staging, manifest)
    except Exception:
        if installed_new and target.exists():
            shutil.rmtree(target)
        if moved_old and backup.exists():
            os.replace(backup, target)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if manifest_staging.exists():
            manifest_staging.unlink()
        if backup.exists():
            shutil.rmtree(backup)


def verify_builtin(product_repo: Path, guide_commit: str, expected_digest: str) -> None:
    state = builtin_state(product_repo)
    if not state.get("present") or not state.get("valid"):
        raise GuideError("generated builtin snapshot failed manifest verification")
    if state.get("sourceCommit") != guide_commit:
        raise GuideError("generated builtin source commit differs from requested commit")
    if state.get("snapshotSha256") != expected_digest:
        raise GuideError("generated builtin snapshot digest differs from public package")
    verify_package(builtin_root(product_repo) / SKILL_NAME)


def parse_semver(value: str, *, field: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise GuideError(f"{field} must use MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def build_release_assets(
    guide_repo: Path,
    *,
    release_sequence: int,
    min_product_version: str,
    max_product_version_exclusive: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Build the two deterministic assets consumed by the runtime updater."""

    if release_sequence < 1:
        raise GuideError("--release-sequence must be a positive integer")
    minimum = parse_semver(min_product_version, field="--min-product-version")
    maximum = parse_semver(
        max_product_version_exclusive,
        field="--max-product-version-exclusive",
    )
    if maximum <= minimum:
        raise GuideError("release product compatibility range must be non-empty")
    if git_changes(guide_repo):
        raise GuideError("guide repository must be clean before building release assets")
    source_commit = git_head(guide_repo)
    if not origin_contains_exact_ref(guide_repo, source_commit):
        raise GuideError("guide release commit is not visible on an origin branch or tag")
    package = package_root(guide_repo)
    verified = verify_package(package)
    tag = f"{SKILL_NAME}-r{release_sequence}"
    output_dir.mkdir(parents=True, exist_ok=True)
    package_target = output_dir / RELEASE_PACKAGE_ASSET
    manifest_target = output_dir / RELEASE_MANIFEST_ASSET
    temporary_package = output_dir / f".{RELEASE_PACKAGE_ASSET}.{uuid4().hex}.tmp"
    temporary_manifest = output_dir / f".{RELEASE_MANIFEST_ASSET}.{uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(temporary_package, "w", compression=zipfile.ZIP_STORED) as bundle:
            for path in regular_files(package):
                relative = path.relative_to(package).as_posix()
                info = zipfile.ZipInfo(relative, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                bundle.writestr(info, path.read_bytes())
        package_bytes = temporary_package.read_bytes()
        manifest = {
            "schema_version": 1,
            "skill_name": SKILL_NAME,
            "release_sequence": release_sequence,
            "release_tag": tag,
            "source_commit": source_commit,
            "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
            "package_snapshot_sha256": verified["snapshotSha256"],
            "package_size": len(package_bytes),
            "min_product_version": min_product_version,
            "max_product_version_exclusive": max_product_version_exclusive,
        }
        temporary_manifest.write_bytes(
            (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        )
        os.replace(temporary_package, package_target)
        os.replace(temporary_manifest, manifest_target)
    finally:
        temporary_package.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)
    return {
        "releaseSequence": release_sequence,
        "releaseTag": tag,
        "sourceCommit": source_commit,
        "manifest": str(manifest_target.resolve()),
        "package": str(package_target.resolve()),
        "packageSha256": manifest["package_sha256"],
        "packageSnapshotSha256": manifest["package_snapshot_sha256"],
        "packageSize": manifest["package_size"],
        "minProductVersion": min_product_version,
        "maxProductVersionExclusive": max_product_version_exclusive,
    }


def gh(*args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise GuideError(f"gh {' '.join(args)} failed: {detail}")
    return completed.stdout.strip()


def publish_release(
    guide_repo: Path,
    *,
    release_sequence: int,
    min_product_version: str,
    max_product_version_exclusive: str,
) -> dict[str, Any]:
    """Create a draft with both assets, publish it, then require immutability."""

    origin = git(guide_repo, "remote", "get-url", "origin")
    if normalized_repository_url(origin) != normalized_repository_url(SOURCE_REPOSITORY):
        raise GuideError(f"guide release origin differs from {SOURCE_REPOSITORY}: {origin}")
    tag = f"{SKILL_NAME}-r{release_sequence}"
    raw_immutability = gh(
        "api",
        f"repos/A1m233/{SKILL_NAME}/immutable-releases",
        "--header",
        "X-GitHub-Api-Version: 2026-03-10",
    )
    try:
        immutability = json.loads(raw_immutability)
    except json.JSONDecodeError as exc:
        raise GuideError("release immutability response is invalid JSON") from exc
    if not isinstance(immutability, dict) or immutability.get("enabled") is not True:
        raise GuideError(
            "repository release immutability must be enabled before creating a release"
        )
    existing = gh(
        "release",
        "view",
        tag,
        "--repo",
        f"A1m233/{SKILL_NAME}",
        "--json",
        "tagName",
        check=False,
    )
    if existing:
        raise GuideError(f"release tag already exists: {tag}")
    with tempfile.TemporaryDirectory(prefix=f"{SKILL_NAME}-release-") as temporary:
        built = build_release_assets(
            guide_repo,
            release_sequence=release_sequence,
            min_product_version=min_product_version,
            max_product_version_exclusive=max_product_version_exclusive,
            output_dir=Path(temporary),
        )
        gh(
            "release",
            "create",
            tag,
            str(built["manifest"]),
            str(built["package"]),
            "--repo",
            f"A1m233/{SKILL_NAME}",
            "--target",
            str(built["sourceCommit"]),
            "--title",
            tag,
            "--notes",
            f"Managed first-party Skill release {tag}.",
            "--draft",
        )
        gh(
            "release",
            "edit",
            tag,
            "--repo",
            f"A1m233/{SKILL_NAME}",
            "--draft=false",
        )
        raw_release = gh(
            "api",
            f"repos/A1m233/{SKILL_NAME}/releases/tags/{tag}",
            "--header",
            "X-GitHub-Api-Version: 2026-03-10",
        )
        try:
            release = json.loads(raw_release)
        except json.JSONDecodeError as exc:
            raise GuideError("published release response is invalid JSON") from exc
        if not isinstance(release, dict) or release.get("immutable") is not True:
            raise GuideError(
                "release was published but is not immutable; enable repository release "
                "immutability before publishing another sequence"
            )
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise GuideError("published release assets are missing")
        digests = {
            asset.get("name"): asset.get("digest") for asset in assets if isinstance(asset, dict)
        }
        expected_package_digest = f"sha256:{built['packageSha256']}"
        if digests.get(RELEASE_PACKAGE_ASSET) != expected_package_digest:
            raise GuideError("published package asset digest differs from local release asset")
        manifest_digest = (
            "sha256:" + hashlib.sha256(Path(str(built["manifest"])).read_bytes()).hexdigest()
        )
        if digests.get(RELEASE_MANIFEST_ASSET) != manifest_digest:
            raise GuideError("published manifest asset digest differs from local release asset")
    return {
        **built,
        "immutable": True,
        "releaseUrl": release.get("html_url"),
    }


def sync(product_repo: Path, guide_repo: Path, guide_commit: str) -> dict[str, Any]:
    if not COMMIT_RE.fullmatch(guide_commit):
        raise GuideError("--guide-commit must be a 40-character lowercase commit")
    if git_changes(guide_repo):
        raise GuideError("guide repository must be clean before sync")
    head = git_head(guide_repo)
    if head != guide_commit:
        raise GuideError(f"guide HEAD {head} differs from --guide-commit {guide_commit}")
    if not origin_contains_exact_ref(guide_repo, guide_commit):
        raise GuideError("guide commit is not the exact target of an origin branch or tag")
    verified = verify_package(package_root(guide_repo))
    manifest = updated_manifest(
        load_manifest(product_repo),
        guide_commit=guide_commit,
        snapshot_sha256=verified["snapshotSha256"],
    )
    replace_snapshot(product_repo, package_root(guide_repo), manifest)
    verify_builtin(product_repo, guide_commit, verified["snapshotSha256"])
    return {
        "guideCommit": guide_commit,
        "snapshotSha256": verified["snapshotSha256"],
        "target": str((builtin_root(product_repo) / SKILL_NAME).resolve()),
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Maintain the agent-friend guide snapshot.")
    subparsers = value.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--guide-repo", type=Path)
    prepare_parser.add_argument("--json", action="store_true")
    for command in ("status", "verify"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--guide-repo", type=Path)
        sub.add_argument("--json", action="store_true")
    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument("--guide-repo", type=Path)
    sync_parser.add_argument("--guide-commit", required=True)
    sync_parser.add_argument("--json", action="store_true")
    build_parser = subparsers.add_parser("build-release")
    build_parser.add_argument("--guide-repo", type=Path)
    build_parser.add_argument("--release-sequence", type=int, required=True)
    build_parser.add_argument("--min-product-version", required=True)
    build_parser.add_argument("--max-product-version-exclusive", required=True)
    build_parser.add_argument("--output-dir", type=Path, required=True)
    build_parser.add_argument("--json", action="store_true")
    publish_parser = subparsers.add_parser("publish-release")
    publish_parser.add_argument("--guide-repo", type=Path)
    publish_parser.add_argument("--release-sequence", type=int, required=True)
    publish_parser.add_argument("--min-product-version", required=True)
    publish_parser.add_argument("--max-product-version-exclusive", required=True)
    publish_parser.add_argument("--json", action="store_true")
    return value


def print_result(command: str, payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if command == "status":
        print(f"phase: {payload['phase']}")
        print(f"product: {payload['product']['head']} dirty={payload['product']['dirty']}")
        print(
            f"guide: {payload['guide']['head']} dirty={payload['guide']['dirty']} "
            f"origin={payload['guide']['originHasExactRef']} verified={payload['guide']['verified']}"
        )
        print(
            f"builtin baseline: present={payload['builtin']['present']} "
            f"matches={payload['baselineMatchesGuide']}"
        )
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    product_repo = repository_root()
    guide_repo = (args.guide_repo or default_guide_repo()).resolve()
    try:
        if args.command == "prepare":
            payload = prepare_checkout(guide_repo)
        elif args.command == "status":
            payload = status_payload(product_repo, guide_repo)
        elif args.command == "verify":
            payload = verify_package(package_root(guide_repo))
        elif args.command == "sync":
            payload = sync(product_repo, guide_repo, args.guide_commit)
        elif args.command == "build-release":
            payload = build_release_assets(
                guide_repo,
                release_sequence=args.release_sequence,
                min_product_version=args.min_product_version,
                max_product_version_exclusive=args.max_product_version_exclusive,
                output_dir=args.output_dir.resolve(),
            )
        else:
            payload = publish_release(
                guide_repo,
                release_sequence=args.release_sequence,
                min_product_version=args.min_product_version,
                max_product_version_exclusive=args.max_product_version_exclusive,
            )
    except GuideError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    print_result(args.command, {"ok": True, **payload}, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
