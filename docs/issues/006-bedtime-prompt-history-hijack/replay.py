"""Issue 006 replay: 用改后的 ``DEFAULT_BEDTIME_ADDENDUM`` 重跑"history 末尾是 agent 提问"的场景，看 BedtimeSource 触发的 system turn 是否还会被 history 拽去续写上文。

复用 015 截断的同一份 session（line 1-56），末尾刚好是一条长 assistant，含两个问号——
"你还记得大概是在哪里看到的吗？……真人小孩？"——完美符合 issue 006 复现条件：
history 末尾让 LLM 倾向"接续上文回答"。

跑法（在 repo 根目录）::

    uv run python docs/issues/006-bedtime-prompt-history-hijack/replay.py

观察口径（人眼判定，无自动 assertion）：

- ✅ 主动开口提醒"该睡了 / 早点休息"语义，按当前 ``DEFAULT_BEDTIME_ADDENDUM`` 预期
- ❌ 以 user 口吻续写上文（"还没睡 / 打几把游戏 / 答你提的问题"——issue 006 失败模式）

LLM 是 sampling 的，**跑 3-5 次取多数表现**。仿 015 replay.py 结构，原 session 全程只读。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# ---- 必须在 import agent 前完成的两件事 ----

# 1. 加载 .env：DEEPSEEK_API_KEY 等
REPO_ROOT = Path(__file__).resolve().parents[3]  # docs/issues/<NNN>/replay.py → repo
from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

# 2. 隔离数据目录（覆盖 user_data_dir，使所有 session/memory 落到 /tmp）
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

TMP_DATA = Path(tempfile.mkdtemp(prefix="agent-friend-replay-006-"))
os.environ["AGENT_FRIEND_DATA_DIR"] = str(TMP_DATA)
(TMP_DATA / "sessions").mkdir(parents=True, exist_ok=True)

# ---- 截断 + 改 session_id（双保险）----

# 行 1 = session_meta；行 2-56 = 含铺垫历史；line 56 是一条 assistant_message，
# 内容末尾刚好是两个问号——issue 006 复现的完美 fixture（history 末尾让 LLM 想接答）
TRUNCATE_LINES = 56
NEW_ID = "replay-issue-006-bbbbbbbbbbbbbbbbbbbb"
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

from agent import (  # noqa: E402
    JsonlSessionStore,
    MarkdownPromptBuilder,
    SessionManager,
    TextDelta,
    TurnDone,
    default_context_manager,
    make_default_registry,
    sessions_dir,
)
from agent.runtime.sources import DEFAULT_BEDTIME_ADDENDUM  # noqa: E402
from llm_providers import LLMClient, ProviderSpec  # noqa: E402


def make_spec_with_thinking_off(model_override: str | None = None) -> ProviderSpec:
    spec = ProviderSpec.from_env(prefix="DEEPSEEK")
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
tool_registry = make_default_registry()

mgr = SessionManager(
    store=store,
    llm_client_factory=llm_factory,
    prompt_builder_factory=prompt_factory,
    context_manager_factory=default_context_manager,
    tool_registry=tool_registry,
    memory=None,  # 隔离 memory：本 issue 是 prompt 行为问题，memory 介入只会增加变量
)

session = mgr.open(NEW_ID)
conv = mgr.start_conversation(session)

print("=" * 70)
print("BEDTIME ADDENDUM (当前 prompt 文本):")
print(DEFAULT_BEDTIME_ADDENDUM)
print("=" * 70)
print()
print("[bedtime triggered → dispatch_system_turn(source_kind='cron:bedtime', ...)]")
print()

text_buf: list[str] = []
for ev in conv.dispatch_system_turn(
    source_kind="cron:bedtime",
    system_prompt_addendum=DEFAULT_BEDTIME_ADDENDUM,
    output_visibility="user",
):
    if isinstance(ev, TextDelta):
        sys.stdout.write(ev.text)
        sys.stdout.flush()
        text_buf.append(ev.text)
    elif isinstance(ev, TurnDone):
        sys.stdout.write(f"\n\n[TURN_DONE] stop={ev.stop_reason} · tool_calls={ev.total_tool_calls}\n")
        sys.stdout.flush()

full_text = "".join(text_buf).strip()
print()
print("=" * 70)
print("[summary]")
print(f"  output 字符数:   {len(full_text)}")
print(f"  output 全文:")
print(f"    {full_text!r}")
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
