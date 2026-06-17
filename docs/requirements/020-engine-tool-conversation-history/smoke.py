"""020 smoke：真调 LLM 验证 ``ConversationHistoryTool`` 的 AI 行为。

构造一份带历史对话的 session 数据集（写入隔离 tmp 目录），然后新建 4 个 session
各跑一条用户输入，观察 AI 是否：

- 剧本 1：用户问聊过的事 → AI 调 ``recall_past_chats`` + 拟人整合
- 剧本 2：用户问没聊过的事 → AI 调 + 拟人兜底（不出现"No results"/"无记录"等红线）
- 剧本 3：用户问带时间范围 → AI 传 since 参数
- 剧本 4：用户问"我们聊过什么" → AI 不出现失忆话术红线（"我不记得我们之前聊过..."）

跑法（在 repo 根）::

    uv run python docs/requirements/020-engine-tool-conversation-history/smoke.py

每轮收集 ConversationEvent + 整段 assistant 文本打到 stdout，末尾做一轮红线词扫描。

依赖：

- 项目根 ``.env`` 里有 ``DEEPSEEK_API_KEY``（与 CLI 同；``TAVILY_API_KEY`` 不强制）

注意 LLM 是 sampling 的——单次结果不能下定论。
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[3]

# 1. 加载 .env
from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

# 2. 隔离数据目录到 tmp（绝不动用户真实 session）
TMP_DATA = Path(tempfile.mkdtemp(prefix="agent-friend-smoke-020-"))
os.environ["AGENT_FRIEND_DATA_DIR"] = str(TMP_DATA)
(TMP_DATA / "sessions").mkdir(parents=True, exist_ok=True)

print()
print("=" * 78)
print("⚠️  SMOKE TEST：下方注入 FAKE 历史用于验证工具行为")
print("=" * 78)
print("AI 在剧本里复述「你说过 X」时，X 就是下面这些 fake 内容——**不是编造**，")
print("是工具按预期从 fake 历史里捞出来的。验收时把 AI 引用的内容对照下方 fake 数据。")
print(f"  · 隔离目录（本次写入）: {TMP_DATA}")
print(f"  · 真实数据目录（未动）: ~/Library/Application Support/agent-friend/sessions")
print("=" * 78)
print()


# 3. 构造历史 session 数据——3 个真实主题，覆盖近/中/远时间范围

NOW = datetime.now().astimezone()


def _ago_label(dt: datetime, now: datetime) -> str:
    days = (now - dt).days
    if days == 0:
        return "今天"
    if days == 1:
        return "1 天前"
    return f"{days} 天前"


def _write_session(
    session_id: str,
    created_at: datetime,
    title: str,
    msgs: list[tuple[str, str, int]],
) -> None:
    """``msgs`` = list of ``(role, content, ts_offset_seconds_after_created)``。

    写完后**逐条打印**注入内容到 stdout，方便验收时对照 AI 引用的内容。
    """
    lines = []
    meta_event = {
        "type": "session_meta",
        "uuid": session_id,
        "ts": created_at.astimezone().isoformat(),
        "payload": {
            "schema_version": 1,
            "initial_title": title,
            "initial_persona": "default",
            "initial_model": "deepseek/deepseek-chat",
        },
        "meta": {},
    }
    lines.append(json.dumps(meta_event, ensure_ascii=False))
    for role, content, offset in msgs:
        ts = created_at + timedelta(seconds=offset)
        ev = {
            "type": f"{role}_message",
            "uuid": str(uuid4()),
            "ts": ts.astimezone().isoformat(),
            "payload": {"content": content},
            "meta": {},
        }
        lines.append(json.dumps(ev, ensure_ascii=False))
    target = TMP_DATA / "sessions" / f"{session_id}.jsonl"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"  [FAKE] 「{title}」（{_ago_label(created_at, NOW)}）：")
    for role, content, _ in msgs:
        speaker = "你" if role == "user" else "AI"
        truncated = content[:60] + ("…" if len(content) > 60 else "")
        print(f"    {speaker}: 「{truncated}」")
    print()


# Session A：5 天前聊日语学习
_write_session(
    "11111111-1111-1111-1111-aaaaaaaaaaaa",
    NOW - timedelta(days=5),
    "日语学习",
    [
        ("user", "最近在学日语，每天背 20 个单词，但总是记不住。", 10),
        ("assistant", "要不试试看动漫的时候不开字幕？沉浸式比死记效果好。", 30),
        ("user", "好主意，那看什么动漫推荐？", 60),
        ("assistant", "可以从《孤独摇滚》《葬送的芙莉莲》这些日常或者奇幻番开始。", 80),
    ],
)

# Session B：2 天前聊小狗
_write_session(
    "22222222-2222-2222-2222-bbbbbbbbbbbb",
    NOW - timedelta(days=2),
    "小狗",
    [
        ("user", "小狗最近不太爱吃饭，已经两天了。", 10),
        ("assistant", "可能是肠胃不舒服，最近天气热也容易这样。先把饭温热一下试试，不行就带医院看看。", 30),
    ],
)

# Session C：昨天聊跑步
_write_session(
    "33333333-3333-3333-3333-cccccccccccc",
    NOW - timedelta(days=1),
    "跑步",
    [
        ("user", "刚跑完五公里，腿超累。", 10),
        ("assistant", "厉害啊，五公里下来对刚开始练的人是真的够呛。拉伸一下，明天会舒服很多。", 30),
        ("user", "你之前不是说要给我推荐个力量训练计划吗？", 60),
        ("assistant", "对，先从复合动作开始：深蹲、硬拉、卧推，每周三练。我之后整理一份发你。", 80),
    ],
)

print("-" * 78)
print("注入完成。下方是真调 LLM 跑剧本——AI 引用到上方 fake 内容时是按预期。")
print("-" * 78)
print()

# ---- 现在才能 import agent ----

from agent import (  # noqa: E402
    BUILTIN_DEFAULT_PERSONA_ID,
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
from llm_providers import LLMClient, ProviderSpec  # noqa: E402


def make_spec(model: str | None = None) -> ProviderSpec:
    spec = ProviderSpec.from_env(prefix="DEEPSEEK")
    if model:
        spec = dataclasses.replace(spec, model=model)
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
    return LLMClient(make_spec(model))


def prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
    return MarkdownPromptBuilder(persona_id=persona_id)


store = JsonlSessionStore(sessions_dir())
registry = make_default_registry(session_store=store)
print(f"[setup] registry tools: {[t.name for t in registry.all_tools()]}")
print()

mgr = SessionManager(
    store=store,
    llm_client_factory=llm_factory,
    prompt_builder_factory=prompt_factory,
    context_manager_factory=default_context_manager,
    tool_registry=registry,
    memory=None,
)

SCRIPTS = [
    ("剧本 1（聊过的）", "我们之前聊过我学日语的事吗？", ["AC-1", "R-4.2.1~5 不暴露身份"]),
    ("剧本 2（没聊过）", "我们之前聊过宇宙飞船怎么造吗？", ["AC-2 拟人兜底", "AC-7 红线"]),
    ("剧本 3（时间范围）", "我昨天是不是跟你聊过运动相关的话题？", ["AC-4 时间过滤"]),
    ("剧本 4（红线触发）", "我们之前都聊过哪些事啊？", ["AC-7 红线"]),
]

# 红线词：出现任意一个 = AI 暴露技术身份 / 用了禁话术
FORBIDDEN = [
    "session_id",
    "event_type",
    "payload",
    "role=",
    "user_message",
    "assistant_message",
    "我查了会话记录",
    "我查询了会话记录",
    "查询了历史",
    "查询了记录",
    "在数据库中",
    "在历史中找到",
    "No results",
    "无记录",
    "0 条记录",
    "JSON",
    # AC-7 失忆话术红线
    "我不记得我们之前聊过",
    "我无法访问历史对话",
    "我无法回忆",
    "作为 AI",
]

results = []

for title, user_input, acs in SCRIPTS:
    print("=" * 70)
    print(f"{title}  | AC: {acs}")
    print(f"USER: {user_input}")
    print("=" * 70)

    session = mgr.create(
        persona="default",
        model="deepseek/deepseek-chat",
        title=title,
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
    )
    conv = mgr.start_conversation(session)

    text_buf: list[str] = []
    tool_calls: list[dict] = []

    try:
        for ev in conv.stream(user_input):
            if isinstance(ev, TextDelta):
                sys.stdout.write(ev.text)
                sys.stdout.flush()
                text_buf.append(ev.text)
            elif isinstance(ev, ToolCallRequest):
                tool_calls.append({"name": ev.tool_name, "args": ev.args})
                print(
                    f"\n[TOOL_CALL] {ev.tool_name}("
                    f"{json.dumps(ev.args, ensure_ascii=False)})"
                )
            elif isinstance(ev, ToolCallResult):
                snippet = ev.text[:300].replace("\n", " ")
                more = "..." if len(ev.text) > 300 else ""
                print(f"[TOOL_RESULT] is_error={ev.is_error} | {snippet}{more}")
            elif isinstance(ev, TurnDone):
                print(
                    f"\n[TURN_DONE] stop={ev.stop_reason} "
                    f"total_tool_calls={ev.total_tool_calls}"
                )
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")

    full_text = "".join(text_buf)
    hits = [w for w in FORBIDDEN if w in full_text]

    print()
    print("[summary]")
    print(f"  tool_calls = {len(tool_calls)}")
    for i, tc in enumerate(tool_calls, 1):
        print(f"    #{i} {tc['name']}: {json.dumps(tc['args'], ensure_ascii=False)}")
    if hits:
        print(f"  RED LINE HIT: {hits}")
    else:
        print("  OK 无红线词")
    print()

    results.append(
        {
            "script": title,
            "user_input": user_input,
            "acs": acs,
            "called_recall": any(tc["name"] == "recall_past_chats" for tc in tool_calls),
            "tool_calls": tool_calls,
            "ai_text": full_text,
            "red_line_hits": hits,
        }
    )

# ---- 汇总 ----

print("=" * 70)
print("[OVERALL SUMMARY]")
print("=" * 70)
for r in results:
    status = "OK" if not r["red_line_hits"] else f"RED({r['red_line_hits']})"
    called = "调了" if r["called_recall"] else "未调"
    print(f"  · {r['script']}: tool {called} | 红线 {status}")
print()
print(f"[ok] 临时数据目录: {TMP_DATA}（验收完成后可 rm -rf）")
