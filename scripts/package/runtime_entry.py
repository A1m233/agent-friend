"""Frozen production runtime entry point for the Windows desktop package."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
import time
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol

from runtime_config import (
    ALL_CREDENTIAL_NAMES,
    OPTIONAL_CREDENTIAL_NAMES,
    REQUIRED_CREDENTIAL_NAMES,
    CredentialSeedError,
    KeyringSource,
    decrypt_credential_seed,
    install_credential_seed,
)

try:
    from agent_friend_build_identity import BUILD_IDENTITY_JSON
except ImportError:
    BUILD_IDENTITY_JSON = ""


class _CredentialStore(Protocol):
    def get(self, name: str) -> Any: ...

    def status(self, name: str) -> Any: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _build_identity() -> dict[str, Any]:
    if not BUILD_IDENTITY_JSON:
        raise RuntimeError("frozen runtime build identity is unavailable")
    value = json.loads(BUILD_IDENTITY_JSON)
    if not isinstance(value, dict):
        raise RuntimeError("frozen runtime build identity is invalid")
    return value


def _print_build_identity() -> int:
    try:
        identity = _build_identity()
    except (RuntimeError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "identity": identity}, ensure_ascii=False))
    return 0


def _smoke_skill_catalog() -> dict[str, Any]:
    """Exercise packaged builtin resources and the native hot-reload watcher."""
    from agent.skills import SkillCatalogService, SkillSelection

    marker = "agent-friend frozen Skill watcher activation smoke"
    with tempfile.TemporaryDirectory(prefix="agent-friend-skill-smoke-") as temp_dir:
        root = Path(temp_dir)
        personal_root = root / "personal"
        service = SkillCatalogService(
            personal_root=personal_root,
            shared_root=root / "shared",
            watcher_enabled=True,
        )
        try:
            initial = service.initialize()
            builtin = next(
                (record for record in initial.entries if record.id == "builtin/skill-creator"),
                None,
            )
            if builtin is None:
                raise RuntimeError("packaged builtin skill-creator was not discovered")
            required_resources = (
                builtin.package_root / "SKILL.md",
                builtin.package_root / "license.txt",
                builtin.package_root / "scripts" / "quick_validate.py",
            )
            missing = [
                str(path.relative_to(builtin.package_root))
                for path in required_resources
                if not path.is_file()
            ]
            if missing:
                raise RuntimeError(
                    f"packaged builtin Skill resources are missing: {','.join(missing)}"
                )

            service.start()
            package = personal_root / "frozen-watcher-smoke"
            package.mkdir(parents=True)
            (package / "SKILL.md").write_text(
                "---\n"
                "name: frozen-watcher-smoke\n"
                "description: Validate the packaged Skill watcher.\n"
                "---\n\n"
                f"{marker}\n",
                encoding="utf-8",
            )

            deadline = time.monotonic() + 5
            live = None
            while time.monotonic() < deadline:
                live = next(
                    (
                        record
                        for record in service.snapshot.entries
                        if record.name == "frozen-watcher-smoke"
                    ),
                    None,
                )
                if live is not None:
                    break
                time.sleep(0.05)
            if live is None:
                raise RuntimeError("packaged Skill watcher did not refresh the catalog")

            turn = service.begin_turn((SkillSelection(live.id, live.version),))
            if (
                len(turn.activations) != 1
                or turn.activation_issues
                or marker not in turn.activations[0].contents
            ):
                raise RuntimeError("hot-loaded Skill could not be version-pinned and activated")
            return {
                "builtin": builtin.name,
                "builtin_resources": len(required_resources),
                "catalog_generation": turn.catalog.generation,
                "watcher_status": service.watcher_state.status,
                "activated": turn.activations[0].record.name,
            }
        finally:
            service.stop()


def _doctor() -> int:
    checks: dict[str, dict[str, Any]] = {}
    modules = (
        "agent",
        "agent_bridge",
        "llm_providers",
        "memory",
        "runtime_config",
        "voice_bridge",
        "keyring",
        "onnxruntime",
        "qqbot_agent_sdk",
        "sqlite_vec",
        "tokenizers",
    )
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:
            checks[f"module:{name}"] = {"ok": False, "error": str(exc)}
        else:
            checks[f"module:{name}"] = {"ok": True}

    resource_loaders = {
        "persona": lambda: files("agent.personas").joinpath("default.md"),
        "prompt": lambda: files("agent.prompt_sections").joinpath("project_identity.md"),
        "memory_prompt": lambda: (
            Path(importlib.import_module("memory").__file__).resolve().parent
            / "extraction"
            / "prompts"
            / "extract.md"
        ),
    }
    for name, load_resource in resource_loaders.items():
        try:
            resource = load_resource()
            content = resource.read_text(encoding="utf-8")
        except Exception as exc:
            checks[f"resource:{name}"] = {"ok": False, "error": str(exc)}
        else:
            checks[f"resource:{name}"] = {"ok": bool(content.strip())}

    try:
        from agent.tools.builtin.web_fetch.html import extract_static_html

        page = extract_static_html(
            """
            <html><body>
              <main><p>agent-friend frozen HTML extraction smoke</p></main>
              <a href="/evidence">Evidence link</a>
            </body></html>
            """,
            requested_url="https://example.com/start",
            final_url="https://example.com/start",
        )
        html_ok = (
            "frozen HTML extraction smoke" in page.text
            and len(page.links) == 1
            and page.links[0].url == "https://example.com/evidence"
        )
    except Exception as exc:
        checks["capability:web_fetch_static_html"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    else:
        checks["capability:web_fetch_static_html"] = {
            "ok": html_ok,
            "content_chars": len(page.text),
            "link_count": len(page.links),
        }

    try:
        skill_details = _smoke_skill_catalog()
    except Exception as exc:
        checks["capability:skill_catalog_hot_reload"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    else:
        checks["capability:skill_catalog_hot_reload"] = {"ok": True, **skill_details}

    model_dir = os.environ.get("AGENT_FRIEND_MEMORY_MODEL_DIR", "").strip()
    if model_dir:
        model_path = Path(model_dir)
        checks["memory_model"] = {
            "ok": model_path.is_dir() and any(model_path.iterdir()),
            "path": str(model_path),
        }
        try:
            from memory.search.vector import (
                OnnxEmbeddingProvider,
                SqliteVecIndex,
                VectorFilters,
                VectorRecord,
            )

            provider = OnnxEmbeddingProvider(
                model_path / "onnx" / "model_quantized.onnx",
                model_path / "tokenizer.json",
            )
            vector = provider.embed(["打包运行时记忆检索自检"])[0]
            with tempfile.TemporaryDirectory(prefix="agent-friend-memory-smoke-") as temp_dir:
                index = SqliteVecIndex(Path(temp_dir) / "index.db", dimension=len(vector))
                try:
                    index.upsert(
                        "doctor",
                        [
                            VectorRecord(
                                evidence_id="packaged-smoke",
                                vector=vector,
                                owner_user_id="doctor",
                                persona_id="default",
                                kind="semantic",
                            )
                        ],
                    )
                    hits = index.search(
                        vector,
                        filters=VectorFilters(generation_id="doctor", owner_user_id="doctor"),
                        limit=1,
                    )
                finally:
                    index.close()
            checks["memory_native_query"] = {
                "ok": bool(hits and hits[0].evidence_id == "packaged-smoke"),
                "dimension": len(vector),
            }
        except Exception as exc:
            checks["memory_native_query"] = {"ok": False, "error": str(exc)}
    else:
        checks["memory_model"] = {"ok": True, "status": "not-configured"}

    ok = all(item["ok"] for item in checks.values())
    print(
        json.dumps(
            {"ok": ok, "runtime_root": str(_runtime_root()), "checks": checks},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


def _seed_credentials(
    payload_path: Path,
    key_path: Path,
    *,
    store: _CredentialStore | None = None,
) -> int:
    credential_store = store or KeyringSource()
    try:
        credentials = decrypt_credential_seed(payload_path.read_bytes(), key_path.read_bytes())
        count = install_credential_seed(credentials, credential_store)
    except (CredentialSeedError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    cleared = sum(value is None for value in credentials.values())
    print(json.dumps({"ok": True, "installed": count, "cleared": cleared}, ensure_ascii=False))
    return 0


def _credential_status(
    *,
    require_operational: bool,
    store: _CredentialStore | None = None,
) -> int:
    credential_store = store or KeyringSource()
    missing: list[str] = []
    unreadable: list[str] = []
    for name in ALL_CREDENTIAL_NAMES:
        try:
            status = credential_store.status(name)
        except Exception:
            unreadable.append(name)
            continue
        if status.error and not status.configured:
            unreadable.append(name)
        elif not status.configured:
            missing.append(name)
    missing_required = [name for name in missing if name in REQUIRED_CREDENTIAL_NAMES]
    missing_optional = [name for name in missing if name in OPTIONAL_CREDENTIAL_NAMES]
    ok = not unreadable and (not require_operational or not missing_required)
    print(
        json.dumps(
            {
                "ok": ok,
                "configured": len(ALL_CREDENTIAL_NAMES) - len(missing) - len(unreadable),
                "total": len(ALL_CREDENTIAL_NAMES),
                "missing_required": missing_required,
                "missing_optional": missing_optional,
                "unreadable": unreadable,
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 2


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent-friend-runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("bridge", "voice"):
        subparsers.add_parser(command)
    subparsers.add_parser("doctor")
    subparsers.add_parser("build-identity")
    seed_parser = subparsers.add_parser("seed-credentials")
    seed_parser.add_argument("--payload", type=Path, required=True)
    seed_parser.add_argument("--key-file", type=Path, required=True)
    status_parser = subparsers.add_parser("credential-status")
    status_parser.add_argument("--require-operational", action="store_true")
    args, remaining = parser.parse_known_args(argv)
    if args.command not in {"bridge", "voice"} and remaining:
        parser.error(f"unrecognized arguments: {' '.join(remaining)}")
    args.args = remaining
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command == "doctor":
        return _doctor()
    if args.command == "build-identity":
        return _print_build_identity()
    if args.command == "seed-credentials":
        return _seed_credentials(args.payload, args.key_file)
    if args.command == "credential-status":
        return _credential_status(require_operational=args.require_operational)
    if BUILD_IDENTITY_JSON:
        os.environ["AGENT_FRIEND_BUILD_IDENTITY_JSON"] = BUILD_IDENTITY_JSON
    if args.command == "bridge":
        from agent_bridge.__main__ import main as bridge_main

        return bridge_main(args.args)
    if args.command == "voice":
        from voice_bridge.__main__ import main as voice_main

        return voice_main(args.args)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
