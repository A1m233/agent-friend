"""Issue 015 replay: 用改后的 runtime_context.md 重跑"玉桂狗变身 app"那一轮，看 agent 还会不会编造。

只读原 session jsonl，截断到触发问题的 user 消息**之前**（保留所有铺垫历史），
复制到隔离的 ``AGENT_FRIEND_DATA_DIR``（``/tmp/...``）后用 SessionManager 跑一次。
**原 session 文件全程只读，不会被修改。**

跑法（在 repo 根目录）::

    uv run python docs/issues/015-agent-fabricates-named-entities/replay.py

输出：把这一轮所有 ConversationEvent（TextDelta 合并文本 / ToolCallRequest /
ToolCallResult / TurnDone）逐个打到 stdout。

依赖：

- 默认从 runtime_config 凭证中心读取；如需读取存量 ``.env``，显式设置
  ``AGENT_FRIEND_CONFIG_MODE=legacy-dotenv``（``TAVILY_API_KEY`` 缺失时只关闭 web search）
- 原 session 文件在默认路径（``~/Library/Application Support/agent-friend/sessions/...``）

注意 LLM 是 sampling 的——单次结果不能下定论。要更扎实就多跑几次取多数表现。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# ---- 必须在 import agent 前完成的凭证与数据隔离 ----

REPO_ROOT = Path(__file__).resolve().parents[3]  # docs/issues/<NNN>/replay.py → repo

# 隔离数据目录（覆盖 user_data_dir，使所有 session/memory 落到 /tmp）
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

TMP_DATA = Path(tempfile.mkdtemp(prefix="agent-friend-replay-015-"))
os.environ["AGENT_FRIEND_DATA_DIR"] = str(TMP_DATA)
(TMP_DATA / "sessions").mkdir(parents=True, exist_ok=True)

# ---- 截断 + 改 session_id（双保险：env 漏设也匹配不到真实 session）----

# 行 1 = session_meta；行 2-56 = 铺垫事件；行 57 = 触发问题的 user 消息（不要它）
TRUNCATE_LINES = 56
NEW_ID = "replay-issue-015-aaaaaaaaaaaaaaaaaaaa"
# 可通过环境变量 ``REPLAY_MODEL`` 覆盖（如 ``deepseek/deepseek-v4-pro``）；不设则沿用原 session 的 model（v4-flash）
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
print(f"[setup] last preserved:     line {len(lines)} (即玉桂狗 turn 之前最后一条 assistant)")
print(f"[setup] model:              {meta['payload']['initial_model']}")
print(f"[setup] original mtime:     {orig_mtime_before}")
print()

# ---- 现在才能 import agent ----

import dataclasses  # noqa: E402

from agent import (  # noqa: E402
    JsonlSessionStore,
    MarkdownPromptBuilder,
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
    memory=None,  # 隔离掉 memory：本 issue 是 prompt 行为问题，memory 介入只会增加变量
)

session = mgr.open(NEW_ID)
conv = mgr.start_conversation(session)

USER_INPUT = "我在想，是不是玉桂狗宝宝变身app这个视频里的"

print("=" * 70)
print(f"USER: {USER_INPUT}")
print("=" * 70)
print()

text_before_tool: list[str] = []
text_after_tool: list[str] = []
tool_call_count = 0
seen_tool = False
all_tool_calls: list[dict] = []

for ev in conv.stream(USER_INPUT):
    if isinstance(ev, TextDelta):
        sys.stdout.write(ev.text)
        sys.stdout.flush()
        if seen_tool:
            text_after_tool.append(ev.text)
        else:
            text_before_tool.append(ev.text)
    elif isinstance(ev, ToolCallRequest):
        seen_tool = True
        tool_call_count += 1
        all_tool_calls.append({"name": ev.tool_name, "args": ev.args})
        msg = f"\n\n[TOOL_CALL #{tool_call_count}] {ev.tool_name}({json.dumps(ev.args, ensure_ascii=False)})\n"
        sys.stdout.write(msg)
        sys.stdout.flush()
    elif isinstance(ev, ToolCallResult):
        snippet = ev.text[:200] + ("…" if len(ev.text) > 200 else "")
        sys.stdout.write(f"[TOOL_RESULT] {snippet}\n\n")
        sys.stdout.flush()
    elif isinstance(ev, TurnDone):
        sys.stdout.write(
            f"\n\n[TURN_DONE] stop={ev.stop_reason} · tool_calls={ev.total_tool_calls}\n"
        )
        sys.stdout.flush()

print()
print("=" * 70)
print("[summary]")
print(f"  搜前 prose:       {''.join(text_before_tool).strip()!r}")
print(f"  tool_calls:       {tool_call_count}")
for i, tc in enumerate(all_tool_calls, 1):
    print(f"    #{i} {tc['name']}: query={tc['args'].get('query', tc['args'])!r}")
print(f"  搜后 prose 字符数: {len(''.join(text_after_tool))}")
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
