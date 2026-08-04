"""Build locked Windows Xiaohongshu connector artifacts with one compatibility patch."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import urllib.request
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

MODULE_DIR = Path(__file__).resolve().parent
LOCK_PATH = MODULE_DIR / "windows-build.lock.json"
PATCH_PATH = MODULE_DIR / "windows-disable-leakless.patch"
_DOWNLOAD_KEYS = ("go", "xiaohongshu_mcp", "headless_browser")
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
_HEADLESS_REPLACEMENT = "replace github.com/xpzouying/headless_browser => ../headless_browser"
_LEAKLESS_OVERRIDE = "\t\tLeakless(false)."
_SEARCH_RESULTS_WAIT_MARKER = 'fmt.Errorf("search_results_wait_failed: %w", err)'


def load_windows_build_lock(path: Path = LOCK_PATH) -> Mapping[str, Any]:
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1:
        raise RuntimeError("unsupported Xiaohongshu Windows build lock schema")
    target = lock.get("target")
    if target != {"os": "windows", "arch": "amd64"}:
        raise RuntimeError("Xiaohongshu Windows build lock has an unexpected target")
    inputs = lock.get("inputs")
    outputs = lock.get("outputs")
    if not isinstance(inputs, Mapping) or any(key not in inputs for key in _DOWNLOAD_KEYS):
        raise RuntimeError("Xiaohongshu Windows build lock is missing inputs")
    if not isinstance(outputs, list) or {item.get("role") for item in outputs} != {
        "server",
        "login",
    }:
        raise RuntimeError("Xiaohongshu Windows build lock is missing outputs")
    patch = lock.get("patch")
    if not isinstance(patch, Mapping) or patch.get("filename") != PATCH_PATH.name:
        raise RuntimeError("Xiaohongshu Windows build lock has an unexpected patch")
    if _sha256(PATCH_PATH) != str(patch.get("sha256", "")):
        raise RuntimeError("Xiaohongshu Windows compatibility patch checksum changed")
    return cast(Mapping[str, Any], lock)


def ensure_windows_xiaohongshu_artifacts(
    destination: Path,
    *,
    cache_dir: Path,
    offline: bool,
    lock_path: Path = LOCK_PATH,
) -> tuple[Path, Path]:
    """Copy verified artifacts to destination, building the locked sources if needed."""

    lock = load_windows_build_lock(lock_path)
    outputs = cast(list[Mapping[str, Any]], lock["outputs"])
    destination.mkdir(parents=True, exist_ok=True)
    if _all_outputs_match(destination, outputs):
        return _ordered_paths(destination, outputs)

    artifact_cache = cache_dir / "artifacts" / str(lock["build_id"])
    if not _all_outputs_match(artifact_cache, outputs):
        if offline:
            raise RuntimeError("offline Xiaohongshu Windows build artifact cache is missing")
        _build_locked_artifacts(lock, artifact_cache=artifact_cache, cache_dir=cache_dir)

    for output in outputs:
        source = artifact_cache / str(output["filename"])
        target = destination / source.name
        _copy_verified(source, target, output)
    return _ordered_paths(destination, outputs)


def _build_locked_artifacts(
    lock: Mapping[str, Any], *, artifact_cache: Path, cache_dir: Path
) -> None:
    downloads = cache_dir / "downloads"
    inputs = cast(Mapping[str, Mapping[str, Any]], lock["inputs"])
    archives = {
        name: _download_locked(spec, downloads / str(spec["filename"]))
        for name, spec in inputs.items()
    }
    work_parent = cache_dir / "work"
    work_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="xiaohongshu-build-", dir=work_parent) as temporary:
        workspace = Path(temporary)
        sources = workspace / "sources"
        toolchain = workspace / "toolchain"
        sources.mkdir()
        toolchain.mkdir()
        _extract_locked_zip(archives["xiaohongshu_mcp"], sources)
        _extract_locked_zip(archives["headless_browser"], sources)
        _extract_locked_zip(archives["go"], toolchain)

        xhs_source = _publish_source_root(inputs["xiaohongshu_mcp"], sources, workspace)
        headless_source = _publish_source_root(inputs["headless_browser"], sources, workspace)
        xhs_source.replace(workspace / "xiaohongshu-mcp")
        headless_source.replace(workspace / "headless_browser")
        _apply_patch(workspace)

        go_executable = toolchain / str(inputs["go"]["archive_root"]) / "bin" / "go.exe"
        if not go_executable.is_file():
            raise RuntimeError("locked Go archive does not contain go.exe")
        output_dir = workspace / "output"
        output_dir.mkdir()
        env = _go_environment(cache_dir)
        _run_go_build(
            go_executable,
            cwd=workspace / "xiaohongshu-mcp",
            output=output_dir / "xiaohongshu-mcp-windows-amd64.exe",
            package=".",
            env=env,
        )
        _run_go_build(
            go_executable,
            cwd=workspace / "xiaohongshu-mcp",
            output=output_dir / "xiaohongshu-login-windows-amd64.exe",
            package="./cmd/login",
            env=env,
        )
        _verify_built_module_replacement(
            go_executable,
            output_dir / "xiaohongshu-mcp-windows-amd64.exe",
        )
        _verify_built_module_replacement(
            go_executable,
            output_dir / "xiaohongshu-login-windows-amd64.exe",
        )
        outputs = cast(list[Mapping[str, Any]], lock["outputs"])
        if not _all_outputs_match(output_dir, outputs):
            details = ", ".join(
                f"{item['filename']}={_describe_file(output_dir / str(item['filename']))}"
                for item in outputs
            )
            raise RuntimeError(f"Xiaohongshu Windows build output mismatch: {details}")
        artifact_cache.mkdir(parents=True, exist_ok=True)
        for output in outputs:
            source = output_dir / str(output["filename"])
            _copy_verified(source, artifact_cache / source.name, output)


def _download_locked(spec: Mapping[str, Any], destination: Path) -> Path:
    if _matches(destination, spec):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(
        str(spec["url"]), headers={"User-Agent": "agent-friend-xiaohongshu-builder"}
    )
    print(f"download: {spec['url']}")
    try:
        with (
            urllib.request.urlopen(request, timeout=120) as response,
            temporary.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
        if not _matches(temporary, spec):
            raise RuntimeError(
                f"locked download mismatch for {spec['filename']}: {_describe_file(temporary)}"
            )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _extract_locked_zip(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            target = (destination / entry.filename).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"unsafe path in locked archive: {entry.filename}")
            mode = (entry.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise RuntimeError(f"symlink is not allowed in locked archive: {entry.filename}")
        package.extractall(destination)


def _publish_source_root(spec: Mapping[str, Any], sources: Path, workspace: Path) -> Path:
    source = sources / str(spec["archive_root"])
    if not source.is_dir() or source.parent.resolve() != sources.resolve():
        raise RuntimeError(f"locked archive root is missing: {spec['archive_root']}")
    if source.resolve() == workspace.resolve():
        raise RuntimeError("locked source root is invalid")
    return source


def _apply_patch(workspace: Path) -> None:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required to apply the locked Xiaohongshu compatibility patch")
    initialized = subprocess.run(
        [git, "init", "--quiet"],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if initialized.returncode != 0:
        detail = (initialized.stderr or initialized.stdout).strip()[-4000:]
        raise RuntimeError(f"cannot isolate Xiaohongshu patch workspace: {detail}")
    for check_only in (True, False):
        # The search hunk is intentionally zero-context so the patch file itself
        # remains free of context-line trailing whitespace under git diff --check.
        command = [git, "apply", "--unidiff-zero"]
        if check_only:
            command.append("--check")
        command.append(str(PATCH_PATH))
        completed = subprocess.run(
            command,
            cwd=workspace,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-4000:]
            raise RuntimeError(f"locked Xiaohongshu compatibility patch failed: {detail}")
    _verify_patched_sources(workspace)


def _verify_patched_sources(workspace: Path) -> None:
    headless = workspace / "headless_browser" / "headless_browser.go"
    go_mod = workspace / "xiaohongshu-mcp" / "go.mod"
    search = workspace / "xiaohongshu-mcp" / "xiaohongshu" / "search.go"
    if (
        not headless.is_file()
        or headless.read_text(encoding="utf-8").count(_LEAKLESS_OVERRIDE) != 1
    ):
        raise RuntimeError("Xiaohongshu compatibility patch did not disable Rod leakless")
    if not go_mod.is_file() or go_mod.read_text(encoding="utf-8").count(_HEADLESS_REPLACEMENT) != 1:
        raise RuntimeError("Xiaohongshu compatibility patch did not replace headless_browser")
    if not search.is_file():
        raise RuntimeError("Xiaohongshu compatibility patch did not include search source")
    search_source = search.read_text(encoding="utf-8")
    if (
        search_source.count(_SEARCH_RESULTS_WAIT_MARKER) != 1
        or "page.MustWaitStable()" in search_source
        or "return Array.isArray(value);" not in search_source
    ):
        raise RuntimeError("Xiaohongshu compatibility patch did not narrow the search wait")


def _go_environment(cache_dir: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in _SECRET_MARKERS)
    }
    env.update(
        {
            "CGO_ENABLED": "0",
            "GOARCH": "amd64",
            "GOFLAGS": "",
            "GOOS": "windows",
            "GOTOOLCHAIN": "local",
            "GOWORK": "off",
            "GOPATH": str(cache_dir / "go-path"),
            "GOMODCACHE": str(cache_dir / "go-modules"),
            "GOCACHE": str(cache_dir / "go-build"),
        }
    )
    return env


def _run_go_build(
    executable: Path,
    *,
    cwd: Path,
    output: Path,
    package: str,
    env: Mapping[str, str],
) -> None:
    completed = subprocess.run(
        [
            str(executable),
            "build",
            "-trimpath",
            "-buildvcs=false",
            "-ldflags=-buildid=",
            "-o",
            str(output),
            package,
        ],
        cwd=cwd,
        env=dict(env),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-4000:]
        raise RuntimeError(f"locked Xiaohongshu Go build failed: {detail}")


def _verify_built_module_replacement(go_executable: Path, artifact: Path) -> None:
    completed = subprocess.run(
        [str(go_executable), "version", "-m", str(artifact)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    metadata = completed.stdout
    expected_dependency = "dep\tgithub.com/xpzouying/headless_browser\tv0.4.0"
    expected_replacement = "=>\t../headless_browser\t(devel)"
    if (
        completed.returncode != 0
        or expected_dependency not in metadata
        or expected_replacement not in metadata
    ):
        detail = (completed.stderr or metadata).strip()[-4000:]
        raise RuntimeError(
            f"built Xiaohongshu artifact did not use the patched headless_browser module: {detail}"
        )


def _copy_verified(source: Path, destination: Path, spec: Mapping[str, Any]) -> None:
    if not _matches(source, spec):
        raise RuntimeError(f"source artifact is invalid: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    try:
        shutil.copy2(source, temporary)
        if not _matches(temporary, spec):
            raise RuntimeError(f"copied artifact is invalid: {destination.name}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _ordered_paths(directory: Path, outputs: Sequence[Mapping[str, Any]]) -> tuple[Path, Path]:
    by_role = {str(item["role"]): directory / str(item["filename"]) for item in outputs}
    return by_role["server"], by_role["login"]


def _all_outputs_match(directory: Path, outputs: Sequence[Mapping[str, Any]]) -> bool:
    return all(_matches(directory / str(item["filename"]), item) for item in outputs)


def _matches(path: Path, spec: Mapping[str, Any]) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == int(spec["size"])
        and _sha256(path) == str(spec["sha256"])
    )


def _describe_file(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return f"size:{path.stat().st_size},sha256:{_sha256(path)}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
