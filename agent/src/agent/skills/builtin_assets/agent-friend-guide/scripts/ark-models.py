#!/usr/bin/env python3
"""Search and install one Ark-Models operator model for agent-friend."""

from __future__ import annotations

import argparse
import errno
import hashlib
import http.client
import json
import re
import shutil
import socket
import ssl
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any

REPOSITORY = "https://github.com/isHarryh/Ark-Models"
API_ROOT = "https://api.github.com/repos/isHarryh/Ark-Models"
RAW_ROOT = "https://raw.githubusercontent.com/isHarryh/Ark-Models"
DEFAULT_REF = "main"
USER_AGENT = "agent-friend-guide/ark-models-installer"
MAX_METADATA_BYTES = 20 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_MODEL_BYTES = 256 * 1024 * 1024
MAX_CONTENTS_BYTES = 100_000_000
MAX_REQUEST_ATTEMPTS = 3
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class InstallerError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _http_error(exc: urllib.error.HTTPError) -> InstallerError:
    headers = exc.headers or {}
    message = ""
    if exc.code == 403:
        try:
            payload = json.loads(exc.read(64 * 1024))
            if isinstance(payload, dict):
                message = str(payload.get("message", "")).lower()
        except (OSError, ValueError, http.client.HTTPException):
            pass
    limited = exc.code == 429 or (
        exc.code == 403
        and (
            headers.get("X-RateLimit-Remaining") == "0"
            or headers.get("Retry-After") is not None
            or "rate limit" in message
            or "abuse detection" in message
        )
    )
    if limited:
        detail = "GitHub request limit reached; retry later"
        retry_after = headers.get("Retry-After", "")
        reset = headers.get("X-RateLimit-Reset", "")
        if retry_after.isdigit():
            detail += f" (Retry-After: {retry_after} seconds)"
        if reset.isdigit():
            detail += f" (X-RateLimit-Reset: {reset} Unix seconds)"
        return InstallerError("github_rate_limited", detail)
    if exc.code == 404:
        return InstallerError(
            "not_found", "GitHub file or ref was not found at the requested revision"
        )
    return InstallerError("http_error", f"GitHub returned HTTP {exc.code}")


def _transient_network_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, BaseException) and _transient_network_error(exc.reason)
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            ssl.SSLEOFError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
        ),
    ):
        return True
    if isinstance(exc, socket.gaierror):
        return exc.errno == socket.EAI_AGAIN
    return isinstance(exc, OSError) and exc.errno in {
        errno.ECONNRESET,
        errno.ECONNABORTED,
        errno.ETIMEDOUT,
        errno.EPIPE,
    }


def _request_once(url: str, *, limit: int, raw_media: bool) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.raw+json"
            if raw_media
            else "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        if raw_media:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            media_parts = {
                part.strip().lower()
                for part in response.headers.get("X-GitHub-Media-Type", "").split(";")
            }
            if (
                content_type
                not in {
                    "application/vnd.github.raw",
                    "application/vnd.github.raw+json",
                    "application/vnd.github.v3.raw",
                    "application/vnd.github.v3.raw+json",
                }
                and "format=raw" not in media_parts
            ):
                raise InstallerError(
                    "invalid_response", "GitHub Contents API did not return raw file bytes"
                )
        declared = response.headers.get("Content-Length")
        expected = None
        if declared is not None:
            if not declared.isascii() or not declared.isdigit():
                raise InstallerError(
                    "invalid_response", "GitHub returned an invalid Content-Length"
                )
            expected = int(declared)
            if expected > limit:
                raise InstallerError("download_too_large", f"download exceeds {limit} bytes")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = response.read(min(1024 * 1024, limit - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise InstallerError("download_too_large", f"download exceeds {limit} bytes")
            chunks.append(chunk)
        if expected is not None and size < expected:
            raise http.client.IncompleteRead(b"", expected - size)
        if expected is not None and size > expected:
            raise InstallerError("invalid_response", "GitHub response exceeds its Content-Length")
        return b"".join(chunks)


def _request_bytes(url: str, *, limit: int, raw_media: bool = False) -> bytes:
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        try:
            return _request_once(url, limit=limit, raw_media=raw_media)
        except urllib.error.HTTPError as exc:
            try:
                error = _http_error(exc)
            finally:
                exc.close()
            raise error from exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            retryable = _transient_network_error(exc)
            if not retryable or attempt == MAX_REQUEST_ATTEMPTS - 1:
                raise InstallerError(
                    "network_error",
                    f"GitHub request failed: {exc}",
                    retryable=retryable,
                ) from exc
            time.sleep(2**attempt)
    raise AssertionError("request attempt limit must be positive")


def _request_file(commit: str, relative: PurePosixPath, *, limit: int) -> bytes:
    if not COMMIT_RE.fullmatch(commit):
        raise InstallerError("invalid_commit", "file downloads require a full commit SHA")
    relative = _safe_relative_path(relative.as_posix())
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in relative.parts)
    try:
        return _request_bytes(f"{RAW_ROOT}/{commit}/{encoded}", limit=limit)
    except InstallerError as exc:
        if exc.code != "network_error" or not exc.retryable:
            raise
    # Stay on the same repository/revision, and read bytes rather than download_url.
    url = f"{API_ROOT}/contents/{encoded}?ref={commit}"
    return _request_bytes(url, limit=min(limit, MAX_CONTENTS_BYTES), raw_media=True)


def _decode_json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallerError("invalid_upstream_json", "GitHub returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise InstallerError("invalid_upstream_json", "GitHub JSON root is not an object")
    return value


def _request_json(url: str, *, limit: int = MAX_METADATA_BYTES) -> dict[str, Any]:
    return _decode_json(_request_bytes(url, limit=limit))


def resolve_commit(ref: str) -> str:
    if COMMIT_RE.fullmatch(ref.lower()):
        return ref.lower()
    encoded = urllib.parse.quote(ref, safe="")
    payload = _request_json(f"{API_ROOT}/commits/{encoded}")
    commit = payload.get("sha")
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise InstallerError("invalid_commit", "GitHub did not return a full commit SHA")
    return commit


def load_catalog(commit: str) -> dict[str, Any]:
    raw = _request_file(commit, PurePosixPath("models_data.json"), limit=MAX_METADATA_BYTES)
    return _decode_json(raw)


def _operator_records(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    storage = catalog.get("storageDirectory")
    if not isinstance(storage, dict) or storage.get("Operator") != "models":
        raise InstallerError("unsupported_catalog", "Ark-Models operator storage is not models/")
    data = catalog.get("data")
    if not isinstance(data, dict):
        raise InstallerError("unsupported_catalog", "Ark-Models catalog has no data object")
    records: list[dict[str, Any]] = []
    for slug, raw in data.items():
        if not isinstance(slug, str) or not isinstance(raw, dict) or raw.get("type") != "Operator":
            continue
        assets = raw.get("assetList")
        if not isinstance(assets, dict):
            continue
        records.append(
            {
                "slug": slug,
                "name": str(raw.get("name") or slug),
                "appellation": str(raw.get("appellation") or ""),
                "skin": str(raw.get("skinGroupName") or ""),
                "style": str(raw.get("style") or ""),
                "assets": {str(key): str(value) for key, value in assets.items()},
            }
        )
    return records


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def search_records(
    records: list[dict[str, Any]], query: str, *, limit: int
) -> list[dict[str, Any]]:
    needle = _normalized(query)
    if not needle:
        raise InstallerError("empty_query", "search query cannot be empty")
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for record in records:
        fields = [
            _normalized(str(record.get("slug", ""))),
            _normalized(str(record.get("name", ""))),
            _normalized(str(record.get("appellation", ""))),
            _normalized(str(record.get("skin", ""))),
        ]
        if needle not in " ".join(fields):
            continue
        if needle in fields:
            score = 0
        elif any(field.startswith(needle) for field in fields):
            score = 1
        else:
            score = 2
        ranked.append((score, str(record["slug"]), record))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [record for _, _, record in ranked[:limit]]


def _record_for_slug(records: list[dict[str, Any]], slug: str) -> dict[str, Any]:
    matches = [record for record in records if record["slug"] == slug]
    if len(matches) != 1:
        raise InstallerError("model_not_found", f"operator model not found: {slug}")
    return matches[0]


def sanitize_folder_name(record: dict[str, Any]) -> str:
    parts = [str(record["name"])]
    skin = str(record.get("skin") or "")
    if skin and skin != "默认服装":
        parts.append(skin)
    parts.append(str(record["slug"]))
    value = "-".join(parts)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    value = value[:120].rstrip(" .")
    if not value or value.upper() in WINDOWS_RESERVED:
        value = f"ark-model-{record['slug']}"
    return value


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise InstallerError("unsafe_upstream_path", f"unsafe upstream path: {value}")
    return path


def atlas_texture_pages(raw: bytes) -> list[PurePosixPath]:
    try:
        lines = [line.strip() for line in raw.decode("utf-8-sig").splitlines()]
    except UnicodeDecodeError as exc:
        raise InstallerError("invalid_atlas", "atlas is not UTF-8 text") from exc
    pages: list[PurePosixPath] = []
    for current, following in pairwise(lines):
        if current and ":" not in current and following.startswith("size:"):
            pages.append(_safe_relative_path(current))
    if not pages:
        raise InstallerError("invalid_atlas", "atlas does not declare a texture page")
    return list(dict.fromkeys(pages))


def _read_binary_string(raw: bytes, offset: int) -> tuple[str, int]:
    value = 0
    shift = 0
    while shift < 35:
        if offset >= len(raw):
            raise InstallerError("invalid_skeleton", "truncated Spine binary header")
        byte = raw[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            if value <= 1:
                return "", offset
            length = value - 1
            end = offset + length
            if end > len(raw):
                raise InstallerError("invalid_skeleton", "truncated Spine version string")
            try:
                return raw[offset:end].decode("utf-8"), end
            except UnicodeDecodeError as exc:
                raise InstallerError("invalid_skeleton", "invalid Spine header text") from exc
        shift += 7
    raise InstallerError("invalid_skeleton", "invalid Spine binary varint")


def spine_version(raw: bytes) -> str:
    _, offset = _read_binary_string(raw, 0)
    version, _ = _read_binary_string(raw, offset)
    if not version.startswith("3.8.") or version == "3.8.75":
        raise InstallerError(
            "unsupported_spine_version",
            f"unsupported Spine runtime {version!r}; expected 3.8.x except 3.8.75",
        )
    return version


def _destination(models_dir: str, folder_name: str) -> tuple[Path, Path]:
    raw_root = Path(models_dir).expanduser()
    if not raw_root.is_absolute():
        raise InstallerError("models_dir_not_absolute", "--models-dir must be an absolute path")
    if not raw_root.is_dir():
        raise InstallerError(
            "models_dir_missing",
            "--models-dir must be the existing directory opened by agent-friend settings",
        )
    root = raw_root.resolve()
    if Path(folder_name).name != folder_name or folder_name in {".", ".."}:
        raise InstallerError("invalid_folder_name", "model folder name must be one path segment")
    if (
        folder_name != folder_name.strip(" .")
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', folder_name)
        or folder_name.upper() in WINDOWS_RESERVED
    ):
        raise InstallerError("invalid_folder_name", "model folder name is not cross-platform safe")
    destination = root / folder_name
    if destination.exists():
        raise InstallerError(
            "destination_exists", f"target model folder already exists: {destination}"
        )
    return root, destination


def _write_download(path: Path, data: bytes) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "path": path.as_posix(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def download_model(
    record: dict[str, Any], commit: str, target: Path
) -> tuple[str, list[dict[str, Any]]]:
    assets = record["assets"]
    skeleton_name = assets.get(".skel")
    atlas_name = assets.get(".atlas")
    if not skeleton_name or not atlas_name:
        raise InstallerError("incomplete_model", "model metadata lacks .skel or .atlas")
    skeleton_rel = _safe_relative_path(skeleton_name)
    atlas_rel = _safe_relative_path(atlas_name)
    if skeleton_rel.stem != atlas_rel.stem:
        raise InstallerError("incomplete_model", "skeleton and atlas do not have the same stem")

    model_path = PurePosixPath("models") / record["slug"]
    atlas_bytes = _request_file(commit, model_path / atlas_rel, limit=MAX_FILE_BYTES)
    pages = atlas_texture_pages(atlas_bytes)
    skeleton_bytes = _request_file(
        commit,
        model_path / skeleton_rel,
        limit=MAX_FILE_BYTES,
    )
    version = spine_version(skeleton_bytes)
    payloads: list[tuple[PurePosixPath, bytes]] = [
        (atlas_rel, atlas_bytes),
        (skeleton_rel, skeleton_bytes),
    ]
    total = len(atlas_bytes) + len(skeleton_bytes)
    for page in pages:
        texture_rel = atlas_rel.parent / page
        data = _request_file(
            commit,
            model_path / texture_rel,
            limit=MAX_FILE_BYTES,
        )
        total += len(data)
        if total > MAX_MODEL_BYTES:
            raise InstallerError("model_too_large", f"model exceeds {MAX_MODEL_BYTES} bytes")
        payloads.append((texture_rel, data))

    written: list[dict[str, Any]] = []
    for relative, data in payloads:
        info = _write_download(target.joinpath(*relative.parts), data)
        info["path"] = relative.as_posix()
        written.append(info)
    return version, written


def _source_metadata(
    record: dict[str, Any], commit: str, version: str, files: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "repository": REPOSITORY,
        "commit": commit,
        "sourcePath": f"models/{record['slug']}",
        "model": {
            "slug": record["slug"],
            "name": record["name"],
            "appellation": record["appellation"],
            "skin": record["skin"],
            "style": record["style"],
            "spineVersion": version,
        },
        "downloadedAt": datetime.now(UTC).isoformat(),
        "files": files,
    }


def _install(args: argparse.Namespace) -> dict[str, Any]:
    commit = resolve_commit(args.ref)
    records = _operator_records(load_catalog(commit))
    record = _record_for_slug(records, args.slug)
    folder_name = args.folder_name or sanitize_folder_name(record)
    root, destination = _destination(args.models_dir, folder_name)
    plan = {
        "repository": REPOSITORY,
        "resolvedCommit": commit,
        "model": {key: value for key, value in record.items() if key != "assets"},
        "modelsDir": str(root),
        "destination": str(destination),
        "willOverwrite": False,
    }
    if args.dry_run:
        return {"ok": True, "dryRun": True, **plan}
    if not args.acknowledge_noncommercial:
        raise InstallerError(
            "noncommercial_ack_required",
            "installation requires explicit acknowledgement of Ark-Models non-commercial terms",
        )

    with tempfile.TemporaryDirectory(prefix="agent-friend-ark-model-") as temporary:
        staged = Path(temporary) / folder_name
        staged.mkdir()
        version, files = download_model(record, commit, staged)
        source = _source_metadata(record, commit, version, files)
        (staged / "ARK_MODELS_SOURCE.json").write_text(
            json.dumps(source, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (staged / "ARK_MODELS_NOTICE.txt").write_text(
            "Source: https://github.com/isHarryh/Ark-Models\n"
            f"Commit: {commit}\n"
            f"Path: models/{record['slug']}\n\n"
            "The upstream repository states that these Arknights assets are copyrighted by "
            "Shanghai Hypergryph Network Technology Co., Ltd. They must not be used "
            "commercially or in a way that harms the rights holder. This local copy is not a "
            "redistribution grant.\n",
            encoding="utf-8",
        )
        created_destination = False
        try:
            destination.mkdir()
            created_destination = True
            for child in staged.iterdir():
                target = destination / child.name
                if child.is_dir():
                    shutil.copytree(child, target)
                else:
                    shutil.copy2(child, target)
        except Exception:
            if created_destination:
                shutil.rmtree(destination, ignore_errors=True)
            raise

    return {
        "ok": True,
        "dryRun": False,
        **plan,
        "spineVersion": version,
        "files": files,
        "runtimeValidation": "pending-agent-friend",
    }


def _search(args: argparse.Namespace) -> dict[str, Any]:
    commit = resolve_commit(args.ref)
    records = _operator_records(load_catalog(commit))
    matches = search_records(records, args.query, limit=args.limit)
    return {
        "ok": True,
        "repository": REPOSITORY,
        "resolvedCommit": commit,
        "query": args.query,
        "matches": [
            {key: value for key, value in record.items() if key != "assets"} for record in matches
        ],
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Search and install one Ark-Models operator model")
    subparsers = value.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="search operator and skin metadata")
    search.add_argument("--query", required=True)
    search.add_argument("--ref", default=DEFAULT_REF)
    search.add_argument("--limit", type=int, default=20, choices=range(1, 51), metavar="1..50")

    install = subparsers.add_parser("install", help="install one exact operator model")
    install.add_argument("--slug", required=True)
    install.add_argument("--ref", default=DEFAULT_REF)
    install.add_argument("--models-dir", required=True)
    install.add_argument("--folder-name")
    install.add_argument("--acknowledge-noncommercial", action="store_true")
    install.add_argument("--dry-run", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = _search(args) if args.command == "search" else _install(args)
    except InstallerError as exc:
        print(
            json.dumps(
                {"ok": False, "error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # Keep unexpected failures concise and machine-readable.
        print(
            json.dumps(
                {"ok": False, "error": {"code": "internal_error", "message": str(exc)}},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
