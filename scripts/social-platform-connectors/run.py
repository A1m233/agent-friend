"""Manage locked local social-platform connector artifacts and low-frequency smoke."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import sys
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import cast

from agent.tools.builtin.social_platforms import (
    SocialContentType,
    SocialPlatform,
    SocialPlatformError,
    SocialSearchRequest,
)
from agent.tools.builtin.social_platforms.connectors import (
    BilibiliConnector,
    XiaohongshuConnector,
)
from agent.tools.builtin.social_platforms.connectors.xiaohongshu_artifacts import (
    XIAOHONGSHU_COMMIT,
    XIAOHONGSHU_VERSION,
    XiaohongshuArtifact,
    current_xiaohongshu_artifacts,
    default_xiaohongshu_artifact_dir,
    verify_xiaohongshu_artifact,
)
from agent.tools.factory import make_default_social_platform_service

REPO_ROOT = Path(__file__).resolve().parents[2]
XIAOHONGSHU_BUILDER_PATH = REPO_ROOT / "scripts" / "_lib" / "xiaohongshu_connector" / "build.py"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(artifact: XiaohongshuArtifact, destination: Path, *, offline: bool) -> Path:
    if verify_xiaohongshu_artifact(destination, artifact):
        return destination
    if offline:
        raise RuntimeError(f"offline artifact missing or invalid: {artifact.filename}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(
        artifact.url,
        headers={"User-Agent": "agent-friend-social-connector-setup"},
    )
    print(f"download: {artifact.url}")
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            temporary.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
        if not verify_xiaohongshu_artifact(temporary, artifact):
            actual = _sha256(temporary)
            raise RuntimeError(
                f"checksum mismatch for {artifact.filename}: "
                f"expected {artifact.sha256}, got {actual}"
            )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    if os.name != "nt":
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return destination


def _load_xiaohongshu_builder() -> ModuleType:
    name = "agent_friend_xiaohongshu_connector_builder"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, XIAOHONGSHU_BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the Xiaohongshu Windows connector builder")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _install_artifacts(
    destination: Path, artifacts: tuple[XiaohongshuArtifact, XiaohongshuArtifact], *, offline: bool
) -> list[Path]:
    sources = {item.source for item in artifacts}
    if sources == {"release"}:
        return [_download(item, destination / item.filename, offline=offline) for item in artifacts]
    if sources != {"source-build"}:
        raise RuntimeError("mixed Xiaohongshu connector artifact sources are not supported")
    builder = _load_xiaohongshu_builder()
    installed = builder.ensure_windows_xiaohongshu_artifacts(
        destination,
        cache_dir=destination.parent / "windows-source-build-cache",
        offline=offline,
    )
    shutil.copy2(builder.LOCK_PATH, destination / "BUILD-LOCK.json")
    shutil.copy2(builder.PATCH_PATH, destination / "COMPATIBILITY.patch")
    return list(installed)


def _setup(*, offline: bool) -> dict[str, object]:
    destination = default_xiaohongshu_artifact_dir()
    artifacts = current_xiaohongshu_artifacts()
    installed = _install_artifacts(destination, artifacts, offline=offline)
    return {
        "status": "ok",
        "connector": "xiaohongshu-mcp",
        "version": XIAOHONGSHU_VERSION,
        "commit": XIAOHONGSHU_COMMIT,
        "artifact_source": artifacts[0].source,
        "artifact_dir": str(destination),
        "artifacts": [path.name for path in installed],
    }


def _status(*, platform: SocialPlatform) -> dict[str, object]:
    service = make_default_social_platform_service()
    try:
        auth = service.auth_status(platform)
    finally:
        service.close()
    result: dict[str, object] = {
        "status": "ok",
        "platform": platform,
        "connector": (
            XiaohongshuConnector.name if platform == "xiaohongshu" else BilibiliConnector.name
        ),
        "auth_mode": auth.mode,
        "auth_state": auth.state,
        "login_material_present": auth.material_present,
    }
    if auth.error is not None:
        result["auth_error"] = auth.error
    if platform == "bilibili":
        return result
    directory = default_xiaohongshu_artifact_dir()
    artifacts = current_xiaohongshu_artifacts()
    installed = {
        item.role: verify_xiaohongshu_artifact(directory / item.filename, item)
        for item in artifacts
    }
    result.update(
        {
            "status": "ok" if all(installed.values()) else "not_installed",
            "version": XIAOHONGSHU_VERSION,
            "commit": XIAOHONGSHU_COMMIT,
            "artifact_source": artifacts[0].source,
            "artifact_dir": str(directory),
            "artifacts": installed,
        }
    )
    return result


def _login(*, platform: SocialPlatform, offline: bool) -> dict[str, object]:
    if platform == "xiaohongshu":
        _setup(offline=offline)
    service = make_default_social_platform_service()
    try:
        auth = service.login(platform)
    finally:
        service.close()
    return {
        "status": "ok",
        "platform": platform,
        "connector": (
            XiaohongshuConnector.name if platform == "xiaohongshu" else BilibiliConnector.name
        ),
        "auth_mode": auth.mode,
        "auth_state": auth.state,
        "login_material_present": auth.material_present,
    }


def _smoke(
    *,
    platform: str,
    queries: list[str],
    requested_content_types: list[str],
    read_first: bool,
    read_works: bool,
    allow_platform_network: bool,
) -> dict[str, object]:
    if not allow_platform_network:
        raise RuntimeError("smoke requires --allow-platform-network")
    if not 1 <= len(queries) <= 2:
        raise RuntimeError("smoke requires one or two --query values")
    service = make_default_social_platform_service()
    if platform == "xiaohongshu":
        if requested_content_types and requested_content_types != ["note"]:
            raise RuntimeError("Xiaohongshu smoke only accepts note")
        content_types: tuple[SocialContentType, ...] = ("note",)
    else:
        if requested_content_types:
            if any(
                value not in {"video", "article", "creator"} for value in requested_content_types
            ):
                raise RuntimeError("Bilibili smoke only accepts video, article, or creator")
            content_types = tuple(
                cast(SocialContentType, value) for value in requested_content_types
            )
        else:
            content_types = ("video", "article")
    if read_works and (platform != "bilibili" or content_types != ("creator",)):
        raise RuntimeError("--read-works requires Bilibili --content-type creator")
    counts: list[int] = []
    read_status = "not_requested"
    works_count = 0
    works_relationship_counts = {"direct_upload": 0, "collaboration": 0}
    works_listing_scope: str | None = None
    failure_stage = "search"
    creator_uids: set[int] = set()
    connector_name = (
        XiaohongshuConnector.name if platform == "xiaohongshu" else BilibiliConnector.name
    )
    try:
        first_ref: str | None = None
        for query in queries:
            result = service.search(
                session_id="social-platform-connector-smoke",
                request=SocialSearchRequest(
                    platform=cast(SocialPlatform, platform),
                    query=query,
                    content_types=content_types,
                    sort="relevance",
                ),
            )
            connector_name = result.connector_name
            counts.append(len(result.items))
            for item in result.items:
                uid = item.identity.get("uid") if item.content_type == "creator" else None
                if isinstance(uid, int):
                    creator_uids.add(uid)
            if first_ref is None and result.items:
                first_ref = result.items[0].resource_ref
        if read_first:
            failure_stage = "read_first"
            if first_ref is None:
                read_status = "no_candidate"
            else:
                detail = service.read(
                    session_id="social-platform-connector-smoke",
                    resource_ref=first_ref,
                ).response
                read_status = (
                    "partial" if detail.unavailable_parts or detail.unavailable_reasons else "ok"
                )
        if read_works:
            failure_stage = "read_works"
            if first_ref is None:
                read_status = "no_candidate"
            else:
                works = service.creator_works(
                    session_id="social-platform-connector-smoke",
                    resource_ref=first_ref,
                    sort="popular",
                )
                works_count = len(works.works)
                works_listing_scope = works.listing_scope
                for item in works.works:
                    works_relationship_counts[item.relationship] += 1
    except SocialPlatformError as exc:
        return {
            "status": exc.status,
            "connector": connector_name,
            "query_count": len(queries),
            "result_counts": counts,
            "read_status": read_status,
            "works_count": works_count,
            "works_relationship_counts": works_relationship_counts,
            "works_listing_scope": works_listing_scope,
            "creator_uids": sorted(creator_uids),
            "failure_stage": failure_stage,
        }
    finally:
        service.close()
    return {
        "status": "ok",
        "connector": connector_name,
        "query_count": len(queries),
        "result_counts": counts,
        "read_status": read_status,
        "works_count": works_count,
        "works_relationship_counts": works_relationship_counts,
        "works_listing_scope": works_listing_scope,
        "creator_uids": sorted(creator_uids),
        "failure_stage": None,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    setup = subparsers.add_parser("setup", help="download and verify locked artifacts")
    setup.add_argument("--offline", action="store_true")
    status = subparsers.add_parser("status", help="show local platform login-material status")
    status.add_argument("--platform", choices=("xiaohongshu", "bilibili"), default="xiaohongshu")
    login = subparsers.add_parser("login", help="start an explicit local platform login")
    login.add_argument("--platform", choices=("xiaohongshu", "bilibili"), default="xiaohongshu")
    login.add_argument("--offline", action="store_true")
    smoke = subparsers.add_parser("smoke", help="run a bounded real-platform read-only smoke")
    smoke.add_argument("--platform", choices=("xiaohongshu", "bilibili"), default="xiaohongshu")
    smoke.add_argument("--query", action="append", default=[])
    smoke.add_argument("--content-type", action="append", default=[])
    smoke.add_argument("--read-first", action="store_true")
    smoke.add_argument("--read-works", action="store_true")
    smoke.add_argument("--allow-platform-network", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "setup":
        result = _setup(offline=args.offline)
    elif args.command == "status":
        result = _status(platform=cast(SocialPlatform, args.platform))
    elif args.command == "login":
        result = _login(
            platform=cast(SocialPlatform, args.platform),
            offline=args.offline,
        )
    else:
        result = _smoke(
            platform=args.platform,
            queries=args.query,
            requested_content_types=args.content_type,
            read_first=args.read_first,
            read_works=args.read_works,
            allow_platform_network=args.allow_platform_network,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SocialPlatformError as exc:
        print(json.dumps({"status": exc.status}, ensure_ascii=False))
        raise SystemExit(1) from exc
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
