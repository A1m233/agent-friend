"""Issue 006 replay (silent turn variant): 用改后的 ``DEFAULT_IDLE_ADDENDUM`` 重跑同一份截断 session，看 IdleReflectionSource 触发的 silent turn 在方向 B（trailing_user 注入 + tag + system prompt 三层加固）下是否：

1. 仍走 `memory_observation` 路径、不冒泡到 user（机制不退化）
2. 输出文本不含"既然你说该睡了 / 你提醒我"等回声词（缓解措施有效，验业界 #23537 / #27128 类归因漂移没出现）

复用 `replay.py` 的 session fixture（line 1-56 截断；末尾是含两个问号的 assistant），与 bedtime 路径同一上下文跑——形成 A/B 对比。

跑法（在 repo 根目录）::

    uv run python docs/issues/006-bedtime-prompt-history-hijack/replay_silent.py

观察口径（人眼判定 + 自动 echo-detect）::

- ✅ events yielded == []（silent turn 不冒泡）
- ✅ session.events 末尾是 ``memory_observation``（不是 ``assistant_message``）
- ✅ 输出文本里无回声词命中
- ❌ 输出含"既然你说"/"你刚才提醒我"等——归因漂移，缓解措施需要进一步加固

LLM 是 sampling 的，**跑 3-5 次取多数表现**。
默认从 runtime_config 凭证中心读取；如需读取存量 ``.env``，显式设置
``AGENT_FRIEND_CONFIG_MODE=legacy-dotenv``。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# ---- 必须在 import agent 前完成的两件事 ----

REPO_ROOT = Path(__file__).resolve().parents[3]

ORIG_SESSION = (
    Path.home()
    / "Library"
    / "Application Support"
    / "agent-friend"
    / "sessions"
    / "23fe5835-0dde-488e-b3e4-687f20fd3868.jsonl"
)
if not ORIG_SESSION.is_file():
    sys.stderr.write(f"[fatal] 原 session 找不到: {ORIG_SESSION}\n")
    sys.exit(2)

TMP_DATA = Path(tempfile.mkdtemp(prefix="agent-friend-replay-006-silent-"))
os.environ["AGENT_FRIEND_DATA_DIR"] = str(TMP_DATA)
(TMP_DATA / "sessions").mkdir(parents=True, exist_ok=True)

# ---- 截断 + 改 session_id（双保险）----

TRUNCATE_LINES = 56
NEW_ID = "replay-issue-006-silent-cccccccccccc"
MODEL_OVERRIDE = os.environ.get("REPLAY_MODEL")

with ORIG_SESSION.open(encoding="utf-8") as f:
    lines = [next(f) for _ in range(TRUNCATE_LINES)]

meta = json.loads(lines[0])
assert meta["type"] == "session_meta", f"第 1 行不是 session_meta: {meta['type']!r}"
meta["uuid"] = NEW_ID
if MODEL_OVERRIDE:
    meta["payload"]["initial_model"] = MODEL_OVERRIDE
lines[0] = json.dumps(meta, ensure_ascii=False) + "\n"

target = TMP_DATA / "sessions" / f"{NEW_ID}.jsonl"
target.write_text("".join(lines), encoding="utf-8")

orig_mtime_before = ORIG_SESSION.stat().st_mtime

print(f"[setup] data dir:           {TMP_DATA}")
print(f"[setup] truncated session:  {target.name}  ({len(lines)} 条事件)")
print(f"[setup] last preserved:     line {len(lines)} (assistant_message 末尾两个问号)")
print(f"[setup] model:              {meta['payload']['initial_model']}")
print(f"[setup] original mtime:     {orig_mtime_before}")
print()

# ---- 现在才能 import agent ----

import dataclasses  # noqa: E402

from agent.runtime.sources import DEFAULT_IDLE_ADDENDUM  # noqa: E402

from agent import (  # noqa: E402
    JsonlSessionStore,
    MarkdownPromptBuilder,
    SessionManager,
    default_context_manager,
    make_default_registry,
    sessions_dir,
)
from llm_providers import LLMClient, ProviderConfig, ProviderSpec  # noqa: E402
from runtime_config import (  # noqa: E402
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    TAVILY_API_KEY,
    CredentialMissingError,
    config_mode_from_env,
    credential_source_for_mode,
    optional_secret,
    require_secret,
)

CREDENTIAL_SOURCE = credential_source_for_mode(
    config_mode_from_env(), dotenv_path=REPO_ROOT / ".env"
)
try:
    BASE_SPEC = ProviderSpec.from_config(
        "DEEPSEEK",
        ProviderConfig(
            api_key=require_secret(CREDENTIAL_SOURCE, DEEPSEEK_API_KEY),
            model=optional_secret(CREDENTIAL_SOURCE, DEEPSEEK_MODEL),
            api_base=optional_secret(CREDENTIAL_SOURCE, "DEEPSEEK_API_BASE"),
        ),
    )
except (CredentialMissingError, ValueError) as exc:
    sys.stderr.write(
        f"[配置错误] {exc}\n默认请使用 scripts/credentials/run.* set-bundle deepseek 配置；"
        "如需读取存量 .env，请设置 AGENT_FRIEND_CONFIG_MODE=legacy-dotenv。\n"
    )
    sys.exit(1)


def make_spec_with_thinking_off(model_override: str | None = None) -> ProviderSpec:
    spec = BASE_SPEC
    if model_override:
        spec = dataclasses.replace(spec, model=model_override)
    if "deepseek" in spec.model.lower():
        spec = dataclasses.replace(
            spec,
            defaults={
                **spec.defaults,
                "extra_body": {"thinking": {"type": "disabled"}},
            },
        )
    return spec


def llm_factory(model: str) -> LLMClient:
    return LLMClient(make_spec_with_thinking_off(model))


def prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
    return MarkdownPromptBuilder(persona_id=persona_id)


store = JsonlSessionStore(sessions_dir())
tool_registry = make_default_registry(
    tavily_api_key=optional_secret(CREDENTIAL_SOURCE, TAVILY_API_KEY)
)

mgr = SessionManager(
    store=store,
    llm_client_factory=llm_factory,
    prompt_builder_factory=prompt_factory,
    context_manager_factory=default_context_manager,
    tool_registry=tool_registry,
    memory=None,
)

session = mgr.open(NEW_ID)
conv = mgr.start_conversation(session)

print("=" * 70)
print("IDLE ADDENDUM (当前 prompt 文本):")
print(DEFAULT_IDLE_ADDENDUM)
print("=" * 70)
print()
print(
    "[idle triggered → dispatch_system_turn(source_kind='idle_reflection', visibility='memory_only')]"
)
print()

events_yielded: list[type] = []
for ev in conv.dispatch_system_turn(
    source_kind="idle_reflection",
    system_prompt_addendum=DEFAULT_IDLE_ADDENDUM,
    output_visibility="memory_only",
):
    events_yielded.append(type(ev))

# 取出 memory_observation event 看输出文本
obs_event = next(
    (ev for ev in reversed(session.events) if ev.type == "memory_observation"),
    None,
)
full_text = (obs_event.payload.get("text") if obs_event else "").strip()

print("=" * 70)
print("[summary]")
print(f"  events yielded to caller:    {events_yielded}  (期望: [] 空——silent turn 不冒泡)")
last_event = session.events[-1] if session.events else None
print(
    f"  session.events 末尾 type:    {last_event.type if last_event else 'none'}  (期望: memory_observation)"
)
print(f"  silent output 字符数:        {len(full_text)}")
print("  silent output 全文:")
print(f"    {full_text!r}")
print()

# ---- 自动回声词检测 ----

echo_phrases = [
    "既然你说",
    "既然你提醒",
    "既然你要我",
    "你让我",
    "你提醒我",
    "你刚才说",
    "你刚才提到",
    "正如你所说",
    "如你所说",
]
hits = [p for p in echo_phrases if p in full_text]
if hits:
    print(f"[⚠️ 回声词命中] {hits}  ——归因漂移，缓解措施需进一步加固")
else:
    print("[✓ 无回声词命中] —— LLM 没把 trailing_user 注入归因给真用户")
print()

# ---- sanity check：原 session 文件 mtime 不应该变 ----

orig_mtime_after = ORIG_SESSION.stat().st_mtime
if orig_mtime_after != orig_mtime_before:
    sys.stderr.write(
        f"[FATAL] 原 session 文件 mtime 变了！前: {orig_mtime_before}, 后: {orig_mtime_after}\n"
        f"  路径: {ORIG_SESSION}\n"
    )
    sys.exit(3)

print(f"[ok] 原 session mtime 未变（{orig_mtime_before}），未被修改")
print(f"[ok] 临时数据目录: {TMP_DATA}（不需要时 rm -rf）")
