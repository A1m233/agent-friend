"""命中结果的拟人化文本渲染。

把扫描出的 :class:`Hit` 列表渲染成喂给 LLM 的纯文本——**不暴露 schema 字眼**
（不出现 ``session_id`` / ``event_type`` / ``role`` 等），用日记叙事风格 +
"你说" / "我说" 拟人称谓 + 拟人时间格式（"3 天前周一晚 22:14"）。

末尾追加 inline reminder 引导 LLM 用朋友口吻自然提及，不要复述时间戳 / 暴露
查询动作（与 005 ``web_search._format`` 末尾风格一致）。

详见 docs/requirements/020-engine-tool-conversation-history/design.md §4.3。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ....sessions.events import Event

_WEEKDAY_ZH = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_MAX_CONTENT_CHARS = 200
_INLINE_REMINDER = (
    "\n请基于这些回忆用朋友的口吻自然提及；不要直接吐 ISO 时间戳，也不要直说"
    "「我查了会话记录」之类的话。\n"
    "证据边界：只有上面片段字面出现的内容才算聊过；片段没有的作品、作者、方法、"
    "步骤、数字、工具、候选项或事件日期，都要说没翻到。不要把条目前面的回忆时间"
    "当成用户问的事情发生日期，也不要同一轮改用通识或 web_search 补这个历史问题。"
)
_EMPTY_REMINDER = (
    "回忆结果约束：如果用户暗示之前聊过或我说过，直接说明没翻到。不要凭通识、训练数据、"
    "印象或用户暗示补具体细节，也不要在同一轮切成通用知识回答或 web_search。"
    "如果用户需要通用知识或外部事实，先问是否要单独讲。"
)


@dataclass(frozen=True)
class Hit:
    """单条命中结果。

    Attributes:
        matched: 命中的事件（``user_message`` 或 ``assistant_message``）。
        pair: 紧邻前一条的对话事件（保证语义完整）；命中是首条时为 ``None``。
    """

    matched: Event
    pair: Event | None


def format_hits(hits: list[Hit], now: datetime) -> str:
    """把命中列表渲染成喂给 LLM 的拟人化文本。

    Args:
        hits: 已按时间倒序排好、按 ``limit`` 截好的命中列表。
        now: 当前时间（用于渲染相对时间，如"3 天前"）。**必须 timezone-aware**。

    Returns:
        日记叙事风格的纯文本；空 hits 返回拟人化兜底文案。
    """
    if not hits:
        return f"我翻了翻，好像没和你聊过这个。\n{_EMPTY_REMINDER}"

    parts = [f"我翻了翻和你聊过的事，找到 {len(hits)} 条相关的回忆：\n"]
    for hit in hits:
        parts.append(_format_one(hit, now))
    parts.append(_INLINE_REMINDER)
    return "\n\n".join(parts)


def _format_one(hit: Hit, now: datetime) -> str:
    """单条命中的渲染——时间头 + 可选 pair 行 + matched 行。"""
    time_str = _format_time(hit.matched.ts, now)
    matched_speaker = _speaker_of(hit.matched)
    matched_content = _truncate(_extract_content(hit.matched))

    lines = [f"· {time_str}"]
    if hit.pair is not None:
        pair_speaker = _speaker_of(hit.pair)
        pair_content = _truncate(_extract_content(hit.pair))
        lines.append(f"  {pair_speaker}说：「{pair_content}」")
    lines.append(f"  {matched_speaker}说：「{matched_content}」")
    return "\n".join(lines)


def _format_time(ts: datetime, now: datetime) -> str:
    """把 UTC ts 渲染成拟人化本地时间字符串。

    分档：
    - 同一天 → ``今天 HH:MM``
    - 昨天 → ``昨天 周X X午 HH:MM``
    - 2~6 天前 → ``N 天前 周X X午 HH:MM``
    - 同年（≥7 天前）→ ``MM-DD 周X X午 HH:MM``
    - 跨年 → ``YYYY-MM-DD HH:MM``
    """
    local_ts = ts.astimezone(now.tzinfo)
    days = (now.date() - local_ts.date()).days
    weekday = _WEEKDAY_ZH[local_ts.weekday()]
    period = "上午" if local_ts.hour < 12 else "下午"
    hm = local_ts.strftime("%H:%M")

    if days == 0:
        return f"今天 {hm}"
    if days == 1:
        return f"昨天 {weekday}{period} {hm}"
    if 2 <= days <= 6:
        return f"{days} 天前 {weekday}{period} {hm}"
    if local_ts.year == now.year:
        return f"{local_ts.strftime('%m-%d')} {weekday}{period} {hm}"
    return f"{local_ts.strftime('%Y-%m-%d')} {hm}"


def _speaker_of(ev: Event) -> str:
    """``user_message`` → "你"；``assistant_message`` → "我"。"""
    return "你" if ev.type == "user_message" else "我"


def _extract_content(ev: Event) -> str:
    """从 event payload 取 content 字段；非字符串 / 缺失时返回空串。"""
    content = ev.payload.get("content", "")
    return content if isinstance(content, str) else ""


def _truncate(text: str, max_chars: int = _MAX_CONTENT_CHARS) -> str:
    """单条内容上限：超长截尾 + ``...``；换行替换为空格保持单行视觉。"""
    cleaned = text.strip().replace("\n", " ")
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars] + "..."
