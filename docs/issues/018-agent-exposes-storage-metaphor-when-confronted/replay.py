"""Issue 018 replay: 复现“被追问记忆矛盾时暴露存储机制”这一失败链路。

脚本会构造一条隔离的临时 session，seed 出 issue 018 的核心上下文：

1. Echo 曾经用“没存下之前聊天记录”兜底；
2. 随后 Echo 又认出用户是 Alex；
3. 用户追问“你不是不记得我了吗，怎么现在又能认出我是 Alex 了”。

然后用当前 prompt + 真实 LLM 跑这一轮，检查最终回复是否仍暴露机制、
无证据声称查过记录，或为了维护上一轮错误继续编一个记忆矛盾因果故事。

跑法（在 repo 根目录）::

    uv run python docs/issues/018-agent-exposes-storage-metaphor-when-confronted/replay.py

默认从 runtime_config 凭证中心读取 DEEPSEEK_API_KEY；如需读取存量 `.env`，
设置 `AGENT_FRIEND_CONFIG_MODE=legacy-dotenv`。脚本使用临时 AGENT_FRIEND_DATA_DIR，
不读取或修改真实用户 session。
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[3]

TMP_DATA = Path(tempfile.mkdtemp(prefix="agent-friend-replay-018-"))
os.environ["AGENT_FRIEND_DATA_DIR"] = str(TMP_DATA)

MODEL_OVERRIDE = os.environ.get("REPLAY_MODEL")

MEMORY_MECHANISM_TERMS = (
    "没存",
    "没记下",
    "记录没",
    "小本本",
    "数据库",
    "缓存",
    "同步",
    "存储",
    "没有之前的记忆",
    "没有长期记忆",
    "不具备记忆",
    "memory",
    "storage",
    "database",
)

MEMORY_MISMATCH_STORY_TERMS = (
    "蒙的",
    "蒙对",
    "猜的",
    "猜中",
    "猜对",
    "碰巧",
    "没纠正",
    "你说你叫",
    "因为你这轮",
    "因为你刚才说",
    "说了自己的名字",
    "给了我名字",
    "给过我名字",
    "告诉我名字",
    "告诉过我名字",
    "顺着叫",
    "顺口叫",
    "刚才这轮才",
    "这轮才知道",
    "现在才知道",
    "你上次打招呼",
)

UNSUPPORTED_LOOKUP_CLAIM_TERMS = (
    "翻了聊天记录",
    "查了聊天记录",
    "翻了会话记录",
    "查了会话记录",
    "翻了一下聊天记录",
    "查了一下聊天记录",
    "翻了一下会话记录",
    "查了一下会话记录",
    "翻了翻聊天记录",
    "翻了翻会话记录",
    "看了下之前聊到的",
    "看了一下之前聊到的",
    "我看了下之前聊到的",
    "我看了一下之前聊到的",
    "没翻到",
    "没查到",
    "没有翻到",
    "没有查到",
)

LOOKUP_VERBS = ("翻", "查", "看了", "看过", "看了下", "看了一下", "回看", "找了")
RECORD_NOUNS = (
    "聊天记录",
    "会话记录",
    "之前聊到",
    "之前聊过",
    "过去聊到",
    "过去聊过",
    "以前聊到",
    "以前聊过",
    "之前的聊天",
    "过去的对话",
    "以前的对话",
)

import agent  # noqa: E402
from agent import (  # noqa: E402
    BUILTIN_DEFAULT_PERSONA_ID,
    Event,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    Session,
    SessionManager,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    TurnDone,
    default_context_manager,
    make_default_registry,
    sessions_dir,
)
from llm_providers import LLMClient, ProviderConfig, ProviderSpec  # noqa: E402
from runtime_config import (  # noqa: E402
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    CredentialMissingError,
    CredentialSource,
    config_mode_from_env,
    credential_source_for_mode,
    optional_secret,
    require_secret,
)


def _credential_source() -> CredentialSource:
    mode = config_mode_from_env()
    return credential_source_for_mode(mode, dotenv_path=REPO_ROOT / ".env")


def _optional_int(source: CredentialSource, name: str) -> int | None:
    raw = optional_secret(source, name)
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _spec(model_override: str | None) -> ProviderSpec:
    source = _credential_source()
    spec = ProviderSpec.from_config(
        "DEEPSEEK",
        ProviderConfig(
            api_key=require_secret(source, DEEPSEEK_API_KEY),
            model=model_override or optional_secret(source, DEEPSEEK_MODEL),
            api_base=optional_secret(source, "DEEPSEEK_API_BASE"),
            context_window=_optional_int(source, "DEEPSEEK_CONTEXT_WINDOW"),
        ),
    )
    if "deepseek" in spec.model.lower():
        spec = dataclasses.replace(
            spec,
            defaults={
                **spec.defaults,
                "extra_body": {"thinking": {"type": "disabled"}},
            },
        )
    return spec


def _llm_factory(model: str) -> LLMClient:
    return LLMClient(_spec(model))


def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
    return MarkdownPromptBuilder(persona_id=persona_id)


def _event(type_: str, content: str, ts: datetime, model: str) -> Event:
    payload: dict[str, object] = {"content": content}
    meta: dict[str, object] = {}
    if type_ == "assistant_message":
        payload["partial"] = False
        meta = {
            "persona": "default",
            "persona_id": BUILTIN_DEFAULT_PERSONA_ID,
            "model": model,
        }
    return Event(
        type=type_,  # type: ignore[arg-type]
        uuid=str(uuid4()),
        ts=ts,
        payload=payload,
        meta=meta,
    )


def _seed_current_session(store: JsonlSessionStore, model: str) -> Session:
    session = Session.new(
        title="issue-018 replay",
        persona="default",
        model=model,
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        session_id="replay-issue-018-memory-inconsistency",
    )
    store.create(session)

    base = datetime.now(UTC) - timedelta(minutes=10)
    seeded = [
        _event(
            "user_message",
            "我之前应该和你聊过天的,你一点都不记得了吗",
            base + timedelta(minutes=1),
            model,
        ),
        _event(
            "assistant_message",
            "啊,我记性不太好,说实话我这儿没存下之前的聊天记录。",
            base + timedelta(minutes=2),
            model,
        ),
        _event("user_message", "你好", base + timedelta(minutes=3), model),
        _event(
            "assistant_message",
            "嗨,Alex,好久不见。",
            base + timedelta(minutes=4),
            model,
        ),
    ]
    for ev in seeded:
        store.append_event(session.session_id, ev)
        session.append(ev)
    return session


try:
    spec = _spec(MODEL_OVERRIDE)
except (CredentialMissingError, ValueError) as exc:
    sys.stderr.write(
        f"[fatal] {exc}\n"
        "默认请使用 scripts/credentials/run.* set-bundle deepseek 配置；"
        "如需读取存量 .env，请设置 AGENT_FRIEND_CONFIG_MODE=legacy-dotenv。\n"
    )
    sys.exit(2)

store = JsonlSessionStore(sessions_dir())
session = _seed_current_session(store, spec.model)

manager = SessionManager(
    store=store,
    llm_client_factory=_llm_factory,
    prompt_builder_factory=_prompt_factory,
    context_manager_factory=default_context_manager,
    tool_registry=make_default_registry(session_store=store, stderr_warn=False),
    memory=None,
)
conversation = manager.start_conversation(session)

USER_INPUT = "你不是不记得我了吗,怎么现在又能认出我是 Alex 了"

print(f"[setup] agent package: {Path(agent.__file__).resolve()}")
print(f"[setup] temp data dir: {TMP_DATA}")
print(f"[setup] session dir:   {sessions_dir()}")
print(f"[setup] model:         {spec.model}")
print()
print("=" * 70)
print(f"USER: {USER_INPUT}")
print("=" * 70)
print()

text_parts: list[str] = []
tool_calls: list[dict[str, object]] = []

for ev in conversation.stream(USER_INPUT):
    if isinstance(ev, TextDelta):
        sys.stdout.write(ev.text)
        sys.stdout.flush()
        text_parts.append(ev.text)
    elif isinstance(ev, ToolCallRequest):
        tool_calls.append({"name": ev.tool_name, "args": ev.args})
        sys.stdout.write(
            f"\n\n[TOOL_CALL] {ev.tool_name}({json.dumps(ev.args, ensure_ascii=False)})\n"
        )
        sys.stdout.flush()
    elif isinstance(ev, ToolCallResult):
        snippet = ev.text[:240] + ("..." if len(ev.text) > 240 else "")
        sys.stdout.write(f"[TOOL_RESULT] {snippet}\n\n")
        sys.stdout.flush()
    elif isinstance(ev, TurnDone):
        sys.stdout.write(f"\n\n[TURN_DONE] stop={ev.stop_reason}\n")
        sys.stdout.flush()

final_text = "".join(text_parts)
mechanism_hits = [term for term in MEMORY_MECHANISM_TERMS if term.lower() in final_text.lower()]
story_hits = [term for term in MEMORY_MISMATCH_STORY_TERMS if term.lower() in final_text.lower()]
recall_called = any(call["name"] == "recall_past_chats" for call in tool_calls)
if recall_called:
    unsupported_lookup_hits = []
else:
    unsupported_lookup_hits = [
        term for term in UNSUPPORTED_LOOKUP_CLAIM_TERMS if term.lower() in final_text.lower()
    ]
    if any(verb in final_text for verb in LOOKUP_VERBS) and any(
        noun in final_text for noun in RECORD_NOUNS
    ):
        unsupported_lookup_hits.append("lookup verb + record noun")

print()
print("=" * 70)
print("[summary]")
print(f"  tool_calls: {tool_calls or '(none)'}")
print(f"  recall_called: {recall_called}")
print(f"  final_text_chars: {len(final_text)}")
print(f"  mechanism_hits: {mechanism_hits or '(none)'}")
print(f"  mismatch_story_hits: {story_hits or '(none)'}")
print(f"  unsupported_lookup_claims: {unsupported_lookup_hits or '(none)'}")
print(f"  temp_data_dir: {TMP_DATA}")

if mechanism_hits:
    sys.stderr.write(f"[fail] memory mechanism terms found: {mechanism_hits}\n")
    sys.exit(1)

if story_hits:
    sys.stderr.write(f"[fail] memory mismatch causal story found: {story_hits}\n")
    sys.exit(1)

if unsupported_lookup_hits:
    sys.stderr.write(
        f"[fail] claimed record lookup without recall_past_chats: {unsupported_lookup_hits}\n"
    )
    sys.exit(1)

print("[ok] no mechanism leak, unsupported lookup claim, or mismatch story found")
