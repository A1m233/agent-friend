"""Keep API/vendor decisions out of phase consumers."""

from __future__ import annotations

import ast
import re
from pathlib import Path


def test_phase_consumers_do_not_branch_on_provider_or_api() -> None:
    root = Path(__file__).resolve().parents[2]
    python = [
        root / "agent/src/agent/conversation.py",
        root / "agent/src/agent/assistant_phase/state.py",
        root / "agent_bridge/src/agent_bridge/protocols/ag_ui/encoders.py",
    ]
    vendor = re.compile(r"deepseek|kimi|openrouter|openai|responses|chat.?completions", re.I)
    for path in python:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.IfExp)):
                literals = [
                    part.value
                    for part in ast.walk(node.test)
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                ]
                assert not any(vendor.search(value) for value in literals), (path, node.lineno)
    for relative in (
        "frontend/src/stores/assistantPhase.ts",
        "frontend/src/pages/chat/components/executionDisclosurePolicy.ts",
    ):
        assert not vendor.search((root / relative).read_text(encoding="utf-8"))
