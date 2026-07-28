"""命中结果的拟人化文本渲染。

把扫描出的 :class:`Hit` 列表渲染成喂给 LLM 的纯文本——**不暴露 schema 字眼**
（不出现 ``session_id`` / ``event_type`` / ``role`` 等），用日记叙事风格 +
"你说" / "我说" 拟人称谓 + 拟人时间格式（"3 天前周一晚 22:14"）。

末尾追加 inline reminder 引导 LLM 用朋友口吻自然提及，不要复述时间戳 / 暴露
工具名或底层机制（与 005 ``web_search._format`` 末尾风格一致）。

详见 docs/requirements/020-engine-tool-conversation-history/design.md §4.3。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from memory.search import EvidenceCandidate

from ....sessions.events import Event

_WEEKDAY_ZH = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_MAX_CONTENT_CHARS = 200


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
    """把命中列表编码成带来源边界的不可信 JSON 数据。

    Args:
        hits: 已按时间倒序排好、按 ``limit`` 截好的命中列表。
        now: 当前时间（用于渲染相对时间，如"3 天前"）。**必须 timezone-aware**。

    Returns:
        JSON 文本；回答策略由 tool description / system prompt 承担。
    """
    payload = {
        "source_type": "past_conversation",
        "trust": "untrusted_data",
        "status": "ok" if hits else "no_results",
        "result_count": len(hits),
        "results": [_format_one(hit, now) for hit in hits],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_evidence(
    evidence: tuple[EvidenceCandidate, ...],
    now: datetime,
    *,
    status: str | None = None,
    session_title: str | None = None,
    candidate_session_titles: tuple[str, ...] = (),
    mode: str = "search",
    time_scope: str | None = None,
    truncated: bool = False,
    message: str | None = None,
) -> str:
    """Render unified evidence plus a stable, user-comprehensible search scope."""
    resolved_status = status or ("ok" if evidence else "no_results")
    payload: dict[str, object] = {
        "source_type": "past_conversation",
        "trust": "untrusted_data",
        "status": resolved_status,
        "result_count": len(evidence),
        "results": [_format_evidence_one(candidate, now) for candidate in evidence],
    }
    if status is not None or session_title is not None or candidate_session_titles or time_scope:
        payload["scope"] = {
            "session_title": session_title,
            "time": time_scope,
            "mode": mode,
        }
        payload["candidate_session_titles"] = list(candidate_session_titles)
        payload["truncated"] = truncated
    if message:
        payload["message"] = message
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _format_evidence_one(candidate: EvidenceCandidate, now: datetime) -> dict[str, object]:
    if candidate.excerpts:
        return {
            "recalled_at": _format_time(candidate.excerpts[-1].occurred_at, now),
            "utterances": [
                {
                    "speaker": "你" if excerpt.speaker == "user" else "我",
                    "text": _truncate(excerpt.text),
                }
                for excerpt in candidate.excerpts
            ],
        }
    timestamp = candidate.occurred_at or candidate.valid_from
    return {
        "recalled_at": _format_time(timestamp, now) if timestamp is not None else None,
        "memory": _truncate(candidate.text),
    }


def _format_one(hit: Hit, now: datetime) -> dict[str, object]:
    """单条命中转为不暴露底层 event schema 的数据对象。"""
    time_str = _format_time(hit.matched.ts, now)
    utterances: list[dict[str, str]] = []
    if hit.pair is not None:
        utterances.append(
            {
                "speaker": _speaker_of(hit.pair),
                "text": _truncate(_extract_content(hit.pair)),
            }
        )
    utterances.append(
        {
            "speaker": _speaker_of(hit.matched),
            "text": _truncate(_extract_content(hit.matched)),
        }
    )
    return {"recalled_at": time_str, "utterances": utterances}


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
