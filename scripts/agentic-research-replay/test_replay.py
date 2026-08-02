from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _module() -> ModuleType:
    path = Path(__file__).with_name("run.py")
    name = "agent_friend_agentic_research_replay"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_fixture_replay_keeps_single_user_turn_and_creator_identity() -> None:
    result = _module().fixture()

    assert result["status"] == "passed"
    assert result["user_message_count"] == 1
    assert result["tool_sequence"] == ["social_search", "social_read"]
    assert result["result_summaries"] == [
        {
            "tool": "social_search",
            "is_error": False,
            "status": "ok",
            "content_type": None,
            "view": None,
            "result_count": 1,
            "works_count": 0,
        },
        {
            "tool": "social_read",
            "is_error": False,
            "status": "ok",
            "content_type": "creator",
            "view": "works",
            "result_count": 0,
            "works_count": 2,
        },
    ]
    assert result["creator_uids"] == [8741868]
    assert len(result["work_urls"]) == 2
    assert "final_answer" not in result
