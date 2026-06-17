"""014 单测：``agent.runtime.sources`` 三个 EventSource 实例。

覆盖 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §4：

- :class:`UserSource`：start/submit/stop 语义、未 start 即用报错
- :class:`BedtimeSource`：fire_now 立即入队 + 构造参数校验 + schedule loop
  按时间到点触发 + stop 立即中断
- :class:`IdleReflectionSource`：fire_now 入队（``output_visibility="memory_only"``）+
  idle 阈值控制下的 _loop 行为 + stop 中断
- :func:`_seconds_until_time_of_day`：今日 / 跨夜两条路径

对应 requirement.md AC-5（BedtimeSource）/ AC-6（IdleReflectionSource）的
source 层验证。AgentRuntime 集成由 test_runtime_dispatch.py 覆盖。
"""

from __future__ import annotations

import queue
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from agent.runtime import SystemTriggerEvent, UserEvent
from agent.runtime.sources import (
    DEFAULT_BEDTIME_ADDENDUM,
    DEFAULT_IDLE_ADDENDUM,
    BedtimeSource,
    IdleReflectionSource,
    UserSource,
    _seconds_until_time_of_day,
)

# ===== 021：default addendum 文案含 <system_trigger> tag 包裹 =====


def test_default_bedtime_addendum_has_system_trigger_tag() -> None:
    """021：DEFAULT_BEDTIME_ADDENDUM 用 <system_trigger>...</system_trigger> 包裹。"""
    assert DEFAULT_BEDTIME_ADDENDUM.startswith("<system_trigger>")
    assert DEFAULT_BEDTIME_ADDENDUM.endswith("</system_trigger>")
    # 文案保留 user 视角"该睡了"语义
    assert "该睡了" in DEFAULT_BEDTIME_ADDENDUM


def test_default_idle_addendum_has_system_trigger_tag() -> None:
    """021：DEFAULT_IDLE_ADDENDUM 用 <system_trigger>...</system_trigger> 包裹。
    silent turn 不暴露给用户，但 tag 仍必要——让 LLM 识别是系统触发的反思请求。"""
    assert DEFAULT_IDLE_ADDENDUM.startswith("<system_trigger>")
    assert DEFAULT_IDLE_ADDENDUM.endswith("</system_trigger>")
    # 保留反思语义
    assert "整理" in DEFAULT_IDLE_ADDENDUM or "事实" in DEFAULT_IDLE_ADDENDUM


# ===== _seconds_until_time_of_day（基础数学） =====


def test_seconds_until_today_path() -> None:
    """目标时间今日还未过——直接算差。"""
    now = datetime(2026, 6, 12, 11, 30)
    assert _seconds_until_time_of_day(12, 0, now=now) == 1800.0


def test_seconds_until_tomorrow_path() -> None:
    """目标时间今日已过——加一天再算差。"""
    now = datetime(2026, 6, 12, 23, 30)
    # 到次日 6:00 = 23:30→24:00 (1800s) + 24:00→06:00 (21600s) = 23400s
    assert _seconds_until_time_of_day(6, 0, now=now) == 23400.0


def test_seconds_until_exact_match_goes_tomorrow() -> None:
    """now 正好是目标时间——按"已过"处理走次日（边界保守）。"""
    now = datetime(2026, 6, 12, 23, 0, 0)
    secs = _seconds_until_time_of_day(23, 0, now=now)
    assert secs == 86400.0  # 整 24 小时


# ===== UserSource =====


def test_user_source_submit_puts_user_event() -> None:
    inbox: queue.Queue[Any] = queue.Queue()
    src = UserSource()
    src.start(inbox)
    src.submit("session-1", "hello")
    ev = inbox.get_nowait()
    assert isinstance(ev, UserEvent)
    assert ev.session_id == "session-1"
    assert ev.user_input == "hello"


def test_user_source_submit_before_start_raises() -> None:
    src = UserSource()
    with pytest.raises(RuntimeError, match="尚未 start"):
        src.submit("s", "x")


def test_user_source_stop_is_no_op() -> None:
    """UserSource 无后台 thread——stop 不应抛、不应阻塞。"""
    inbox: queue.Queue[Any] = queue.Queue()
    src = UserSource()
    src.start(inbox)
    src.stop()  # 应立即返回


# ===== BedtimeSource: 构造参数校验 =====


def test_bedtime_source_validates_hour() -> None:
    with pytest.raises(ValueError, match="bedtime_hour"):
        BedtimeSource(session_id="s", bedtime_hour=24)


def test_bedtime_source_validates_minute() -> None:
    with pytest.raises(ValueError, match="bedtime_minute"):
        BedtimeSource(session_id="s", bedtime_hour=23, bedtime_minute=60)


# ===== BedtimeSource: fire_now =====


def test_bedtime_source_fire_now_puts_correct_event() -> None:
    inbox: queue.Queue[Any] = queue.Queue()
    src = BedtimeSource(
        session_id="s-1",
        bedtime_hour=23,
        bedtime_minute=0,
        prompt_addendum="该睡了",
    )
    src.start(inbox)
    try:
        src.fire_now()
        ev = inbox.get_nowait()
        assert isinstance(ev, SystemTriggerEvent)
        assert ev.session_id == "s-1"
        assert ev.source_kind == "cron:bedtime"
        assert ev.system_prompt_addendum == "该睡了"
        assert ev.output_visibility == "user"
        assert ev.event_metadata == {"bedtime_hour": 23, "bedtime_minute": 0}
    finally:
        src.stop()


def test_bedtime_source_fire_now_before_start_raises() -> None:
    src = BedtimeSource(session_id="s")
    with pytest.raises(RuntimeError, match="尚未 start"):
        src.fire_now()


# ===== BedtimeSource: schedule loop 真起 thread 端到端 =====


def test_bedtime_source_loop_fires_after_wait_then_stop_interrupts() -> None:
    """注入 now_factory 让 ``_seconds_until_time_of_day`` 返回极小值（0.1s），
    loop 应在 ~0.1s 内 fire 一次；stop() 立即中断第二轮 60s 等待。"""
    inbox: queue.Queue[Any] = queue.Queue()
    # 让 now = bedtime - 0.1s，_seconds_until 返回 0.1
    fixed_now = datetime(2026, 6, 12, 22, 59, 59, 900_000)
    src = BedtimeSource(
        session_id="s-1",
        bedtime_hour=23,
        bedtime_minute=0,
        now_factory=lambda: fixed_now,
        repeat_after_seconds=60.0,
    )
    src.start(inbox)
    try:
        # 等最多 1 秒拿一个 event
        ev = inbox.get(timeout=1.0)
        assert isinstance(ev, SystemTriggerEvent)
        assert ev.source_kind == "cron:bedtime"
    finally:
        # stop 必须立即中断 repeat_after_seconds=60s 的第二轮等待
        t0 = time.monotonic()
        src.stop(timeout=2.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"stop 应几乎立即返回，实际 {elapsed:.3f}s"


# ===== IdleReflectionSource: 构造参数校验 =====


def test_idle_reflection_source_validates_idle_minutes() -> None:
    runtime_mock = MagicMock()
    with pytest.raises(ValueError, match="idle_minutes"):
        IdleReflectionSource(session_id="s", runtime=runtime_mock, idle_minutes=0)


# ===== IdleReflectionSource: fire_now =====


def test_idle_reflection_source_fire_now_puts_memory_only_event() -> None:
    """fire_now 应入队 ``output_visibility="memory_only"`` 的事件。"""
    inbox: queue.Queue[Any] = queue.Queue()
    runtime_mock = MagicMock()
    runtime_mock.last_dispatch_finished_at = datetime.now(UTC)
    src = IdleReflectionSource(
        session_id="s-1",
        runtime=runtime_mock,
        idle_minutes=30,
        prompt_addendum="reflect",
    )
    src.start(inbox)
    try:
        src.fire_now()
        ev = inbox.get_nowait()
        assert isinstance(ev, SystemTriggerEvent)
        assert ev.session_id == "s-1"
        assert ev.source_kind == "idle_reflection"
        assert ev.system_prompt_addendum == "reflect"
        # 关键：silent turn
        assert ev.output_visibility == "memory_only"
        assert ev.event_metadata == {"idle_threshold_minutes": 30}
    finally:
        src.stop()


def test_idle_reflection_source_fire_now_before_start_raises() -> None:
    runtime_mock = MagicMock()
    runtime_mock.last_dispatch_finished_at = datetime.now(UTC)
    src = IdleReflectionSource(session_id="s", runtime=runtime_mock, idle_minutes=30)
    with pytest.raises(RuntimeError, match="尚未 start"):
        src.fire_now()


# ===== IdleReflectionSource: loop 行为 =====


def test_idle_reflection_source_loop_fires_when_elapsed_exceeds_threshold() -> None:
    """elapsed > idle_threshold → 立即 fire；stop 中断后续 idle_threshold 长 wait。"""
    inbox: queue.Queue[Any] = queue.Queue()
    runtime_mock = MagicMock()
    # last_dispatch 在 100 秒之前；idle 阈值 1 分钟 = 60 秒 → 已过
    base = datetime(2026, 6, 12, 12, 0, 0, tzinfo=UTC)
    runtime_mock.last_dispatch_finished_at = base
    src = IdleReflectionSource(
        session_id="s-1",
        runtime=runtime_mock,
        idle_minutes=1,
        now_factory=lambda: base + timedelta(seconds=100),
        poll_interval_seconds=60.0,
    )
    src.start(inbox)
    try:
        ev = inbox.get(timeout=1.0)
        assert isinstance(ev, SystemTriggerEvent)
        assert ev.source_kind == "idle_reflection"
        assert ev.output_visibility == "memory_only"
    finally:
        t0 = time.monotonic()
        src.stop(timeout=2.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"stop 应几乎立即返回，实际 {elapsed:.3f}s"


def test_idle_reflection_source_loop_does_not_fire_when_below_threshold() -> None:
    """elapsed < idle_threshold → 不 fire，下一次 poll 用 ``min(剩余, poll_interval)``
    的间隔；test 用 poll_interval=10s 但马上 stop，确保 0.5s 内无事件入队。"""
    inbox: queue.Queue[Any] = queue.Queue()
    runtime_mock = MagicMock()
    base = datetime(2026, 6, 12, 12, 0, 0, tzinfo=UTC)
    runtime_mock.last_dispatch_finished_at = base
    src = IdleReflectionSource(
        session_id="s-1",
        runtime=runtime_mock,
        idle_minutes=10,
        now_factory=lambda: base + timedelta(seconds=5),  # 才 5 秒 idle，远不够 10 分钟
        poll_interval_seconds=10.0,
    )
    src.start(inbox)
    try:
        time.sleep(0.3)
        with pytest.raises(queue.Empty):
            inbox.get_nowait()
    finally:
        t0 = time.monotonic()
        src.stop(timeout=2.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0
