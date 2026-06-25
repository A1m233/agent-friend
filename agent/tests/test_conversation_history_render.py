"""``render.format_hits`` 单元测试。

覆盖：

- 空 hits → 拟人化兜底（不含 "No results" / 列表字眼）
- 各时间档（今天 / 昨天 / 2-6 天 / 同年 / 跨年）
- 配对（pair 存在）与孤立（pair=None）
- ``_truncate`` 长内容截断与单行化（换行→空格）
- inline reminder 末尾出现

详见 docs/requirements/020-engine-tool-conversation-history/design.md §4.3。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from agent.sessions.events import Event
from agent.tools.builtin.conversation_history.render import Hit, format_hits

_TZ = timezone(timedelta(hours=8))
_NOW = datetime(2026, 6, 17, 14, 30, tzinfo=_TZ)  # 周三


def _ev(
    ev_type: str,
    content: str,
    ts: datetime,
    *,
    uuid: str = "uuid-x",
    extra_payload: dict[str, Any] | None = None,
) -> Event:
    payload: dict[str, Any] = {"content": content}
    if extra_payload:
        payload.update(extra_payload)
    return Event(type=ev_type, uuid=uuid, ts=ts, payload=payload)  # type: ignore[arg-type]


# ===== 空结果 =====


def test_empty_hits_returns_persona_fallback() -> None:
    text = format_hits([], _NOW)
    assert "翻了翻" in text
    assert "没和你聊过" in text
    assert "不要凭通识" in text
    assert "补具体细节" in text
    assert "通用知识回答" in text
    assert "web_search" in text
    # 不能出现技术化字眼
    for forbidden in ("No results", "找到 0", "session", "event"):
        assert forbidden not in text


# ===== 时间格式化各档 =====


@pytest.mark.parametrize(
    "ts_offset_hours,expected_substring",
    [
        (-2, "今天"),
        (-24, "昨天"),
        (-24 * 3, "3 天前"),
        (-24 * 10, "06-07"),  # 同年但 ≥7 天，用 MM-DD
    ],
)
def test_time_format_tiers(ts_offset_hours: int, expected_substring: str) -> None:
    ts = _NOW + timedelta(hours=ts_offset_hours)
    hit = Hit(matched=_ev("user_message", "你好", ts), pair=None)
    text = format_hits([hit], _NOW)
    assert expected_substring in text


def test_cross_year_uses_full_date() -> None:
    """跨年用 ``YYYY-MM-DD HH:MM`` 格式。"""
    ts = datetime(2025, 6, 17, 14, 30, tzinfo=_TZ)
    hit = Hit(matched=_ev("user_message", "去年的事", ts), pair=None)
    text = format_hits([hit], _NOW)
    assert "2025-06-17" in text
    # 不应出现拟人时间段（去年的内容用全日期）
    assert "今天" not in text and "昨天" not in text


# ===== 拟人称谓 =====


def test_user_message_renders_as_you() -> None:
    ts = _NOW - timedelta(hours=2)
    hit = Hit(matched=_ev("user_message", "我学日语呢", ts), pair=None)
    text = format_hits([hit], _NOW)
    assert "你说：" in text
    # 不暴露 schema 字眼
    for forbidden in ("user_message", "role", "user:", "assistant"):
        assert forbidden not in text


def test_assistant_message_renders_as_me() -> None:
    ts = _NOW - timedelta(hours=2)
    hit = Hit(matched=_ev("assistant_message", "试试不开字幕", ts), pair=None)
    text = format_hits([hit], _NOW)
    assert "我说：" in text


# ===== 配对 =====


def test_pair_renders_before_matched() -> None:
    ts = _NOW - timedelta(days=3)
    user_ev = _ev("user_message", "我学日语", ts - timedelta(seconds=10), uuid="u1")
    asst_ev = _ev("assistant_message", "试试不开字幕", ts, uuid="a1")
    hit = Hit(matched=asst_ev, pair=user_ev)
    text = format_hits([hit], _NOW)
    # pair 在 matched 之前出现
    pair_pos = text.index("我学日语")
    matched_pos = text.index("试试不开字幕")
    assert pair_pos < matched_pos
    # 双方都有说话方标签
    assert "你说：" in text
    assert "我说：" in text


def test_no_pair_only_matched_line() -> None:
    ts = _NOW - timedelta(hours=2)
    hit = Hit(matched=_ev("user_message", "唯一一条", ts), pair=None)
    text = format_hits([hit], _NOW)
    assert "唯一一条" in text
    # 只有一句"你说"——没有配对的"我说"
    assert text.count("你说") == 1
    assert "我说" not in text


# ===== 截断 =====


def test_long_content_truncated_with_ellipsis() -> None:
    long_content = "あ" * 300
    ts = _NOW - timedelta(hours=2)
    hit = Hit(matched=_ev("user_message", long_content, ts), pair=None)
    text = format_hits([hit], _NOW)
    assert "..." in text
    # 不应包含完整 300 字
    assert "あ" * 300 not in text


def test_newlines_in_content_replaced_with_spaces() -> None:
    content_with_nl = "第一行\n第二行\n第三行"
    ts = _NOW - timedelta(hours=2)
    hit = Hit(matched=_ev("user_message", content_with_nl, ts), pair=None)
    text = format_hits([hit], _NOW)
    # 原始内容里的 \n 不应保留在 matched 行
    matched_line = next(line for line in text.split("\n") if "第一行" in line)
    assert "第二行" in matched_line
    assert "第三行" in matched_line


# ===== inline reminder =====


def test_inline_reminder_at_end() -> None:
    ts = _NOW - timedelta(hours=2)
    hit = Hit(matched=_ev("user_message", "话题", ts), pair=None)
    text = format_hits([hit], _NOW)
    # 关键引导词
    assert "朋友的口吻" in text
    assert "ISO 时间戳" in text or "时间戳" in text
    assert "证据边界" in text
    assert "事件日期" in text
    assert "web_search" in text
    # reminder 应该是末尾
    assert text.rstrip().endswith("补这个历史问题。")


def test_inline_reminder_absent_when_empty() -> None:
    """空结果不带命中结果的 inline reminder，但带 0-hit 防编造提醒。"""
    text = format_hits([], _NOW)
    assert "朋友的口吻" not in text
    assert "之前聊过" in text


# ===== 总览 =====


def test_count_in_header() -> None:
    hits = [
        Hit(matched=_ev("user_message", f"msg {i}", _NOW - timedelta(hours=i + 1)), pair=None)
        for i in range(3)
    ]
    text = format_hits(hits, _NOW)
    assert "找到 3 条" in text
