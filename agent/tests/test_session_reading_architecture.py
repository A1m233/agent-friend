"""Ordinary consumers must select a scope; full reads have named owners."""

from __future__ import annotations

import ast
from pathlib import Path

FULL_HELPERS = {"load_session", "load_business_session", "query_session", "query_session_recall"}
OWNERS = {
    # Legacy compatibility and native persistence. Ordinary imports are banned below.
    "agent/src/agent/sessions/query.py",
    "agent/src/agent/sessions/store.py",
    "agent/src/agent/sessions/recall/snapshot.py",
    # Full reads require a declared purpose at this boundary.
    "agent/src/agent/sessions/reading/maintenance.py",
}


def full_calls(source: str) -> list[tuple[str, int]]:
    tree = ast.parse(source)
    aliases = {
        item.asname or item.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for item in node.names
        if item.name in FULL_HELPERS
    }
    uses_sessions = any(
        isinstance(node, ast.ImportFrom)
        and (
            "sessions" in (node.module or "")
            or any(item.name in {"SessionStore", "SessionManager"} for item in node.names)
        )
        for node in ast.walk(tree)
    )
    found: list[tuple[str, int]] = []

    class Visitor(ast.NodeVisitor):
        scope = ""

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            before, self.scope = self.scope, node.name
            self.generic_visit(node)
            self.scope = before

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            helper = isinstance(func, ast.Name) and func.id in aliases
            if helper:
                found.append((self.scope, node.lineno))
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            # Loading callbacks are just as costly as direct method calls.
            if uses_sessions and node.attr in {
                "load",
                "load_business",
                "query",
                "open",
                "iter_events",
                "_read_records",
                "_query_snapshot",
            }:
                receiver = ast.unparse(node.value)
                method = any(
                    part
                    in {
                        "_store",
                        "store",
                        "_sessions",
                        "sessions",
                        "_session_store",
                        "persistent_store",
                        "persistent_session_manager",
                        "mgr",
                        "manager",
                        "editor()",
                    }
                    for part in receiver.split(".")
                )
                if method:
                    found.append((self.scope, node.lineno))
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def test_recognizes_aliased_helpers_and_session_methods() -> None:
    source = "from agent.sessions import SessionStore, query_session as all_history\ndef preview(store):\n all_history(store, 's')\n store.load('s')\n register(store.load)\n"
    assert len(full_calls(source)) == 3


def test_ordinary_consumers_cannot_reintroduce_full_session_reads() -> None:
    root = Path(__file__).resolve().parents[2]
    violations = []
    for package in (
        "agent",
        "agent_bridge",
        "voice_bridge",
        "tools",
        "memory",
        "memory_eval",
        "agent_eval",
    ):
        for path in (root / package / "src").rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            if relative in OWNERS:
                continue
            for scope, line in full_calls(path.read_text(encoding="utf-8")):
                # Kept for external compatibility and execution-store adapters.
                if relative == "agent/src/agent/sessions/manager.py" and scope in {
                    "open",
                    "query",
                    "open_for_execution",
                    "_load_for_preparation",
                }:
                    continue
                # Unpaged REST is an explicit legacy full-history contract.
                if (
                    relative == "agent_bridge/src/agent_bridge/routes/meta.py"
                    and scope == "current"
                ):
                    continue
                violations.append(f"{relative}:{line} ({scope})")
    assert not violations, "Use sessions.reading or declare a full-read owner:\n" + "\n".join(
        violations
    )
