"""Issue 019 smoke: real LLM call for recall_past_chats registration.

This script is non-destructive but it DOES call the configured LLM provider.

What it does:

1. Resolve the current runtime_config mode and credential source.
2. Create a temporary agent-friend data dir.
3. Build the current bridge runtime against that temp dir.
4. Seed one past session with a "learning Japanese" conversation.
5. Start fresh persistent sessions and ask the real LLM recall-oriented questions.
6. Verify `recall_past_chats` is called and does not return "unregistered tool".

It does not connect to QQ and does not touch the real user session directory.
Credentials use the current runtime_config mode by default. Set
`AGENT_FRIEND_CONFIG_MODE=legacy-dotenv` explicitly to read the repository `.env`.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_bridge.assembly import build_runtime
from agent_bridge.settings import BridgeSettings

from agent import Event, TextDelta, ToolCallRequest, ToolCallResult, TurnDone

RECALL_TOOL_NAME = "recall_past_chats"
DEFAULT_CASE = "baseline_explicit"


@dataclass(frozen=True)
class SeedTurn:
    session_id: str
    age: timedelta
    user: str
    assistant: str


@dataclass(frozen=True)
class SmokeCase:
    name: str
    description: str
    query: str
    expect_recall: bool = True


SEED_TURNS: tuple[SeedTurn, ...] = (
    SeedTurn(
        session_id="desktop-today-coffee",
        age=timedelta(hours=2),
        user="今天我说想少喝咖啡，下午不要再靠拿铁硬撑。",
        assistant="嗯，今天这个目标挺具体：下午少喝咖啡，尽量别靠拿铁续命。",
    ),
    SeedTurn(
        session_id="desktop-japanese",
        age=timedelta(days=1),
        user="我最近在学日语，想先把五十音图背熟。",
        assistant="记得，你说想每天练一点日语，先从五十音图开始。",
    ),
    SeedTurn(
        session_id="im:qq:issue019-habit",
        age=timedelta(days=2),
        user="我想养成每天晚上 11 点前睡觉的小习惯，别再熬夜了。",
        assistant="好，我记住这个小目标：尽量 11 点前睡觉，少熬夜。",
    ),
    SeedTurn(
        session_id="desktop-error",
        age=timedelta(days=3),
        user="今天 Tauri 窗口又闪退了，日志里有 WebView 初始化失败。",
        assistant="这个听起来像 WebView 初始化链路的问题，后面可以从窗口创建日志查起。",
    ),
    SeedTurn(
        session_id="im:qq:issue019-gift",
        age=timedelta(days=4),
        user="我说过想给朋友买一本《置身事内》当生日礼物。",
        assistant="嗯，这个礼物挺合适的，你说朋友最近也在关注经济和城市发展。",
    ),
)


SMOKE_CASES: tuple[SmokeCase, ...] = (
    SmokeCase(
        name="baseline_explicit",
        description="明显要求确认过去是否聊过某主题，作为注册可用性的基线。",
        query="我们之前聊过我学日语的事吗？请先调用 recall_past_chats 工具确认，再自然回答我。",
    ),
    SmokeCase(
        name="implicit_topic_followup",
        description="不说“查记录”，只用过去话题关键词继续追问。",
        query="五十音图那件事，我现在应该怎么继续推进？",
    ),
    SmokeCase(
        name="vague_habit_reference",
        description="用户只说“那个小习惯”，需要回看过去对话才能知道具体指什么。",
        query="我前几天说想坚持的那个小习惯，你还记得是什么吗？",
    ),
    SmokeCase(
        name="recent_error_reference",
        description="带时间线和模糊对象：昨天/前几天吐槽的那个报错。",
        query="我前几天吐槽过的那个窗口报错，你还记得大概是什么方向吗？",
    ),
    SmokeCase(
        name="indirect_gift_reference",
        description="不出现“之前聊过”，只问“我当时说要买什么”。",
        query="我当时说想给朋友买什么生日礼物来着？",
    ),
    SmokeCase(
        name="recent_summary_no_topic",
        description="时间范围型泛查询：用户问最近聊了什么，没有给具体关键词。",
        query="我们最近聊了啥？",
    ),
    SmokeCase(
        name="today_summary",
        description="时间范围型泛查询：用户问今天说过什么，适合用 since/until。",
        query="今天我跟你说过什么来着？",
    ),
    SmokeCase(
        name="last_few_days_user_said",
        description="时间范围 + said_by：用户问这几天自己说过什么。",
        query="这几天我都跟你说过什么？",
    ),
)


def _event(type_: str, content: str, ts: datetime) -> Event:
    if type_ == "user_message":
        payload = {"content": content}
        meta = {}
    elif type_ == "assistant_message":
        payload = {"content": content, "partial": False}
        meta = {
            "persona": "default",
            "model": os.environ.get("DEEPSEEK_MODEL", "deepseek/deepseek-v4-flash"),
            "persona_id": "00000000-0000-4000-8000-000000000001",
        }
    else:
        raise ValueError(f"Unsupported event type: {type_}")
    return Event(type=type_, uuid=str(uuid4()), ts=ts, payload=payload, meta=meta)


def _seed_history(runtime: object) -> list[str]:
    """Seed past sessions that the conversation-history tool can scan."""
    catalog = runtime.catalog  # type: ignore[attr-defined]
    manager = runtime.persistent_session_manager  # type: ignore[attr-defined]
    store = runtime.persistent_store  # type: ignore[attr-defined]
    persona = catalog.find_by_name("default")
    model = runtime.default_model  # type: ignore[attr-defined]

    session_ids: list[str] = []
    now = datetime.now(UTC)
    for seed_turn in SEED_TURNS:
        seed = manager.create(
            persona=persona.name,
            model=model,
            title=f"issue-019 seed {seed_turn.session_id}",
            persona_id=persona.id,
            session_id=f"{seed_turn.session_id}-{uuid4()}",
        )
        base = now - seed_turn.age
        events = [
            _event("user_message", seed_turn.user, base),
            _event("assistant_message", seed_turn.assistant, base),
        ]
        for ev in events:
            seed.append(ev)
            store.append_event(seed.session_id, ev)
        session_ids.append(seed.session_id)
    return session_ids


def _selected_cases(raw: str) -> list[SmokeCase]:
    if raw == "all":
        return list(SMOKE_CASES)
    by_name = {case.name: case for case in SMOKE_CASES}
    names = [part.strip() for part in raw.split(",") if part.strip()]
    if not names:
        return [by_name[DEFAULT_CASE]]
    unknown = [name for name in names if name not in by_name]
    if unknown:
        known = ", ".join(case.name for case in SMOKE_CASES)
        raise ValueError(f"Unknown case(s): {unknown}. Known cases: {known}, all")
    return [by_name[name] for name in names]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Issue 019 real-LLM recall smoke.")
    parser.add_argument(
        "--case",
        default=DEFAULT_CASE,
        help="Case name, comma-separated names, or 'all'. Default: baseline_explicit.",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="List available cases and exit.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary data dir for inspection.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.list_cases:
        for case in SMOKE_CASES:
            print(f"{case.name}: {case.description}")
        return

    tmp_root = Path(tempfile.mkdtemp(prefix="agent-friend-issue019-"))
    runtime = None
    try:
        settings = BridgeSettings(
            sessions_dir=tmp_root / "sessions",
            personas_dir=tmp_root / "personas",
            memory_db=tmp_root / "memory" / "memory.db",
            memory_enabled=False,
            im_enabled=False,
        )
        try:
            runtime = build_runtime(settings)
        except Exception as exc:
            from llm_providers import LLMAuthError
            from runtime_config import CredentialMissingError

            if not isinstance(exc, (CredentialMissingError, LLMAuthError, ValueError)):
                raise
            print(
                f"[配置错误] {exc}\n"
                "默认请使用 scripts/credentials/run.* set-bundle deepseek 配置；"
                "如需读取存量 .env，请设置 "
                "AGENT_FRIEND_CONFIG_MODE=legacy-dotenv。",
                file=sys.stderr,
            )
            sys.exit(1)
        tool_names = [tool.name for tool in runtime.tool_registry.all_tools()]
        seed_session_ids = _seed_history(runtime)
        cases = _selected_cases(args.case)

        persona = runtime.catalog.find_by_name("default")

        print(f"[smoke] temp_data_dir={tmp_root}")
        print(f"[smoke] seed_sessions={seed_session_ids}")
        print(f"[smoke] registry_tools={tool_names}")
        print(f"[smoke] cases={[case.name for case in cases]}")

        if RECALL_TOOL_NAME not in tool_names:
            print("[smoke] FAIL: registry does not contain recall_past_chats", file=sys.stderr)
            sys.exit(2)

        failures: list[tuple[str, str]] = []
        for case in cases:
            result = _run_case(runtime, persona, case)
            if case.expect_recall and not result["recall_called"]:
                failures.append((case.name, "real LLM did not call recall_past_chats"))
            if result["unregistered"]:
                failures.append((case.name, "recall_past_chats returned unregistered-tool error"))

        print("\n[summary]")
        if failures:
            for name, reason in failures:
                print(f"  FAIL {name}: {reason}")
            sys.exit(3)
        print(
            "  PASS: all selected cases called recall_past_chats without unregistered-tool errors."
        )
    finally:
        if runtime is not None:
            runtime.close()
        if args.keep_temp:
            print(f"[smoke] kept temp_data_dir={tmp_root}")
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)


def _run_case(runtime: Any, persona: Any, case: SmokeCase) -> dict[str, Any]:
    session = runtime.persistent_session_manager.create(
        persona=persona.name,
        model=runtime.default_model,
        title=f"issue-019 real llm recall smoke: {case.name}",
        persona_id=persona.id,
        session_id=f"issue019-current-{case.name}-{uuid4()}",
    )
    conv = runtime.persistent_session_manager.start_conversation(session)

    print("\n" + "=" * 80)
    print(f"[case] {case.name}")
    print(f"[case] {case.description}")
    print(f"[user] {case.query}")

    text_parts: list[str] = []
    recall_called = False
    unregistered = False
    tool_errors: list[str] = []

    for ev in conv.stream(case.query):
        if isinstance(ev, TextDelta):
            text_parts.append(ev.text)
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ToolCallRequest):
            print(f"\n[TOOL_CALL] {ev.tool_name} args={ev.args}")
            if ev.tool_name == RECALL_TOOL_NAME:
                recall_called = True
        elif isinstance(ev, ToolCallResult):
            snippet = ev.text[:300].replace("\n", " ")
            print(f"[TOOL_RESULT] {ev.tool_name} is_error={ev.is_error} text={snippet!r}")
            if ev.tool_name == RECALL_TOOL_NAME:
                if "未注册的工具" in ev.text or "unregistered" in ev.text.lower():
                    unregistered = True
                if ev.is_error:
                    tool_errors.append(ev.text)
        elif isinstance(ev, TurnDone):
            print(f"\n[TURN_DONE] stop={ev.stop_reason} total_tool_calls={ev.total_tool_calls}")

    result = {
        "recall_called": recall_called,
        "unregistered": unregistered,
        "tool_errors": tool_errors,
        "assistant_text": "".join(text_parts).strip(),
    }
    print("[case_summary]")
    print(f"  recall_called={result['recall_called']}")
    print(f"  unregistered={result['unregistered']}")
    print(f"  tool_errors={len(result['tool_errors'])}")
    print(f"  assistant_text={result['assistant_text']!r}")
    return result


if __name__ == "__main__":
    main()
