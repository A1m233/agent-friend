"""摘要压缩的 prompt 模板与转录渲染（009 M3）。

借鉴 Claude Code 的两个工程技巧（探索 §4 / design §6.2）：

1. ``<analysis>`` 草稿纸：让模型先在 ``<analysis>`` 里梳理时间线/关键事实/情绪走向，
   再写 ``<summary>``；最终把 ``<analysis>`` strip 掉只留 summary 进上下文——免费提升
   summary 质量。
2. **user 原话关键信息逐字保留**：转述会让用户意图漂移，故 prompt 显式要求保留原话。

但**分段改成"朋友陪伴"语境**（cc 的 Files/Errors/Code Sections 是 coding agent 专用，
不适合本项目）：用户画像 / 关键事实与承诺 / 当前话题与未决事项 / 情绪与关系基调 /
用户原话关键片段 / 最近上下文。

摘要调用走 :meth:`llm_providers.LLMClient.complete`（单轮、纯文本、天然不带 tools）。
详见 docs/requirements/009-engine-context-management/design.md §6.2。
"""

from __future__ import annotations

import re
from typing import Any

from ..messages import Message

SUMMARY_SYSTEM_PROMPT = """\
你是一个"朋友陪伴型" AI 的上下文压缩器。你的任务是把一段很长的人机对话历史，\
压缩成一份**结构化摘要**，让 AI 在后续对话中仍然记得用户是谁、聊过什么、答应过什么、\
现在聊到哪，并保持一贯的关系基调——就像一个好朋友不会因为聊久了就忘记你。

请严格按以下两段式输出：

<analysis>
（草稿纸，自由梳理：对话的时间线、出现过的关键事实、用户的情绪起伏、还没聊完的话头。\
这一段只是帮你想清楚，最终不会保留。）
</analysis>

<summary>
## 用户画像与背景
（用户是谁：称呼/身份/重要的人和事/长期处境/偏好。只写对话里真出现过的，别编。）

## 关键事实与承诺
（双方陈述过的事实，以及任一方做出的承诺/约定。带上时间线索如果有。）

## 当前话题与未决事项
（最近在聊什么、有哪些没说完或等待跟进的话头/待办。）

## 情绪与关系基调
（用户当下的情绪状态、希望被如何对待、你和 ta 之间的相处基调。）

## 用户原话关键片段
（**逐字摘录**用户说过的、承载意图/情感/事实的关键句子，不要转述、不要改写。\
没有就写"（无）"。）

## 最近上下文
（紧接当前这一刻之前，对话正停在哪、AI 上一句大致说了什么，便于无缝接续。）
</summary>

要求：
- 只输出上述两段，不要任何额外解释或寒暄。
- "用户原话关键片段"必须逐字，其余段落可凝练但不得篡改事实或捏造。
- 用中文。"""

_SUMMARY_CONTEXT_PREFIX = "[此前对话的压缩摘要——你应当像记得这些一样自然地继续对话]\n"


def render_transcript(messages: list[Message]) -> str:
    """把消息序列渲染成可读的对话转录文本（喂给摘要 prompt）。

    各 role 的渲染：``user`` → ``用户:``；``assistant`` 文本 → ``AI:``，工具调用 →
    ``AI[调用工具 X]:``；``tool`` 结果 → ``[工具结果 X]:``；``system``（含已有摘要）→
    ``[系统/摘要]:``。
    """
    lines: list[str] = []
    for m in messages:
        if m.role == "user":
            lines.append(f"用户: {m.content}")
        elif m.role == "assistant":
            if m.content:
                lines.append(f"AI: {m.content}")
            for tc in m.meta.get("tool_calls", []) if m.meta else []:
                lines.append(f"AI[调用工具 {tc.get('name', '')}]: {tc.get('args', {})}")
        elif m.role == "tool":
            tool_name = m.meta.get("tool_name", "") if m.meta else ""
            lines.append(f"[工具结果 {tool_name}]: {m.content}")
        elif m.role == "system":
            lines.append(f"[系统/摘要]: {m.content}")
    return "\n".join(lines)


def build_summary_messages(conversation_text: str) -> list[dict[str, Any]]:
    """构造摘要调用的 OpenAI 风格消息（system 指令 + user 待压缩内容）。"""
    return [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "以下是需要压缩的对话记录：\n\n"
                f"{conversation_text}\n\n"
                "请严格按系统指令里的两段式格式输出。"
            ),
        },
    ]


_ANALYSIS_RE = re.compile(r"<analysis>.*?</analysis>", re.DOTALL | re.IGNORECASE)
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL | re.IGNORECASE)


def strip_analysis(raw: str) -> str:
    """从模型输出里取出最终 summary 正文（丢弃 ``<analysis>`` 草稿纸）。

    优先取 ``<summary>...</summary>`` 内容；没有显式 summary 标签时，剥掉
    ``<analysis>...</analysis>`` 块后返回剩余文本；都没有则原样返回（容错）。
    """
    match = _SUMMARY_RE.search(raw)
    if match:
        return match.group(1).strip()
    return _ANALYSIS_RE.sub("", raw).strip()


def render_summary_as_context(summary: str) -> str:
    """把 summary 包装成注入上下文的 system 消息正文（加前缀让 AI 自然接续）。"""
    return _SUMMARY_CONTEXT_PREFIX + summary
