"""014 · EventSource 实例：UserSource / BedtimeSource / IdleReflectionSource。

三个示例 source 覆盖两类语义：

- :class:`UserSource` — 包装现有 user 输入路径（``Conversation.send`` / ``stream``
  既有调用方仍可用；新路径走 :meth:`UserSource.submit` 入 inbox），保持向后兼容
- :class:`BedtimeSource` — A 类（定时主动发声）：到指定时间塞
  ``SystemTriggerEvent(source_kind="cron:bedtime", output_visibility="user")``
- :class:`IdleReflectionSource` — D 类（silent turn 落 memory）：系统空闲达阈值
  时塞 ``SystemTriggerEvent(source_kind="idle_reflection",
  output_visibility="memory_only")``

详见 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §4。
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from .inbox import SystemTriggerEvent, UserEvent

if TYPE_CHECKING:
    from . import AgentRuntime


logger = logging.getLogger(__name__)


DEFAULT_BEDTIME_ADDENDUM = (
    "<system_trigger>"
    "到约定休息时间啦——该睡了。"
    "按你当前 persona 自然温和地提醒一句，不长篇大论，不强迫，"
    "让用户感到陪伴而不是被监督。"
    "</system_trigger>"
)


DEFAULT_IDLE_ADDENDUM = (
    "<system_trigger>"
    "定时器触发：基于最近的对话，沉默地为自己整理 1-3 条值得长存的事实——"
    "用户偏好、生活节奏、情绪倾向等都可以；不必回应任何人，只是写给未来的自己。"
    "</system_trigger>"
)


# ===== UserSource =====


class UserSource:
    """user 触发轮的入队适配器（014 R-4.2.2）。

    不起独立 thread——user 输入由 bridge 路由处理器主动调 :meth:`submit`
    （在请求 thread 里），自然入队。:meth:`start` / :meth:`stop` 是 no-op，
    仅为符合 :class:`EventSource` 协议。

    Note:
        本期 ``agent_bridge`` 的 pull encoder 路径**仍直接调** ``conv.stream``，
        不走 UserSource——只在镜像复制时通过 ``listeners.fan_out_event`` 让
        push subscriber 也看到 user_turn envelope（014 design §4.1 工程妥协）。
        UserSource 在本期 dev / 测试场景下用。
    """

    name: ClassVar[str] = "user"

    def __init__(self) -> None:
        self._inbox: queue.Queue[Any] | None = None

    def start(self, inbox: queue.Queue[Any]) -> None:
        self._inbox = inbox

    def stop(self) -> None:
        # 无后台 thread
        pass

    def submit(self, session_id: str, user_input: str) -> None:
        """把 user 输入 enqueue 成 :class:`UserEvent`。

        Raises:
            RuntimeError: 在 :meth:`start` 之前调用。
        """
        if self._inbox is None:
            raise RuntimeError("UserSource 尚未 start，inbox 未绑定")
        self._inbox.put(UserEvent(session_id=session_id, user_input=user_input))


# ===== BedtimeSource =====


def _seconds_until_time_of_day(
    hour: int,
    minute: int,
    *,
    now: datetime,
) -> float:
    """从 ``now`` 算到当天（或次日）``hour:minute`` 还有多少秒。

    Args:
        hour: 0-23 目标小时。
        minute: 0-59 目标分钟。
        now: 当前时间（aware 或 naive 均可；本函数纯算 delta，不假设时区）。

    Returns:
        非负浮点秒数；目标时间今日已过则返回到明日同时间的秒数。
    """
    target_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target_today > now:
        return (target_today - now).total_seconds()
    return (target_today + timedelta(days=1) - now).total_seconds()


class BedtimeSource:
    """A 类示例：定时主动发声（``cron:bedtime``）。

    内部独立 thread 跑 ``_loop``：算到下一个 bedtime 还有多少秒 → ``Event.wait``
    可中断地等待 → 到点塞 :class:`SystemTriggerEvent` 入 inbox。

    Args:
        session_id: bedtime 提醒发给哪个 session（v1 假设单 session）。
        bedtime_hour: 0-23，默认 23（晚上 11 点）。
        bedtime_minute: 0-59，默认 0。
        prompt_addendum: 注入 trailing system 的引导话；默认温和提醒模板。
        now_factory: 取"当前时间"的 callable，**测试注入用**——默认
            ``lambda: datetime.now()``（local naive 时间，便于按用户钟点对齐）。
        repeat_after_seconds: fire 后下一次 schedule 至少间隔多少秒；默认 60
            （避免边缘 case 重复触发）。

    Note:
        使用 local naive ``datetime.now()`` 让 ``bedtime_hour=23`` 直观对应
        用户钟面的"晚上 11 点"——session 落盘的 event ts 仍是 UTC（由
        Conversation 内部转换），不影响调度精度。
    """

    name: ClassVar[str] = "cron:bedtime"

    def __init__(
        self,
        *,
        session_id: str,
        bedtime_hour: int = 23,
        bedtime_minute: int = 0,
        prompt_addendum: str = DEFAULT_BEDTIME_ADDENDUM,
        now_factory: Callable[[], datetime] | None = None,
        repeat_after_seconds: float = 60.0,
    ) -> None:
        if not 0 <= bedtime_hour < 24:
            raise ValueError(f"bedtime_hour 必须在 [0, 24)，got {bedtime_hour}")
        if not 0 <= bedtime_minute < 60:
            raise ValueError(f"bedtime_minute 必须在 [0, 60)，got {bedtime_minute}")
        self._session_id = session_id
        self._bedtime_hour = bedtime_hour
        self._bedtime_minute = bedtime_minute
        self._prompt_addendum = prompt_addendum
        self._now_factory = now_factory or datetime.now
        self._repeat_after_seconds = repeat_after_seconds
        self._inbox: queue.Queue[Any] | None = None
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

    def start(self, inbox: queue.Queue[Any]) -> None:
        if self._thread is not None:
            return  # idempotent
        self._inbox = inbox
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="BedtimeSource")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        if self._thread is None:
            return
        self._stop_evt.set()
        self._thread.join(timeout=timeout)
        self._thread = None

    def fire_now(self) -> None:
        """立即塞一条 bedtime SystemTriggerEvent（供 dev 端点调）。

        正常路径由 ``_loop`` 按时间到点自动调；fire_now 让 dev / 测试场景
        能立刻触发而无需等到真 bedtime。

        Raises:
            RuntimeError: 在 :meth:`start` 之前调用。
        """
        if self._inbox is None:
            raise RuntimeError("BedtimeSource 尚未 start，inbox 未绑定")
        self._inbox.put(
            SystemTriggerEvent(
                session_id=self._session_id,
                source_kind=self.name,
                system_prompt_addendum=self._prompt_addendum,
                output_visibility="user",
                event_metadata={
                    "bedtime_hour": self._bedtime_hour,
                    "bedtime_minute": self._bedtime_minute,
                },
            )
        )

    def _loop(self) -> None:
        while not self._stop_evt.is_set():
            now = self._now_factory()
            wait_secs = _seconds_until_time_of_day(
                self._bedtime_hour, self._bedtime_minute, now=now
            )
            # 用 Event.wait(timeout) 让 stop 能立刻中断（vs time.sleep）
            if self._stop_evt.wait(timeout=wait_secs):
                return
            self.fire_now()
            # 防止边界情况重复触发：fire 后再等一段
            if self._stop_evt.wait(timeout=self._repeat_after_seconds):
                return


# ===== IdleReflectionSource =====


class IdleReflectionSource:
    """D 类示例：silent turn 落 memory（``idle_reflection``）。

    内部独立 thread 跑 ``_loop``：定期读 ``runtime.last_dispatch_finished_at``
    算空闲多久 → 达阈值时塞 :class:`SystemTriggerEvent`
    （``output_visibility="memory_only"``）→ silent turn 由
    :meth:`Conversation.dispatch_system_turn` 完成 memory 喂入，不冒泡到用户。

    Args:
        session_id: 目标 session。
        runtime: :class:`AgentRuntime` 引用，用来读最近 dispatch 完成时间。
        idle_minutes: 触发阈值（分钟）。
        prompt_addendum: 注入 trailing system 的反思引导话。
        now_factory: 取"当前时间"的 callable，测试注入用；默认
            ``lambda: datetime.now(UTC)``（与 runtime 的 timestamp 一致）。
        poll_interval_seconds: 未达阈值时下次 poll 的间隔（取 ``min(剩余, 该值)``）；
            默认 60 秒；测试注入更小值加速。
    """

    name: ClassVar[str] = "idle_reflection"

    def __init__(
        self,
        *,
        session_id: str,
        runtime: AgentRuntime,
        idle_minutes: int = 30,
        prompt_addendum: str = DEFAULT_IDLE_ADDENDUM,
        now_factory: Callable[[], datetime] | None = None,
        poll_interval_seconds: float = 60.0,
    ) -> None:
        if idle_minutes <= 0:
            raise ValueError(f"idle_minutes 必须 > 0，got {idle_minutes}")
        self._session_id = session_id
        self._runtime = runtime
        self._idle_minutes = idle_minutes
        self._prompt_addendum = prompt_addendum
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._poll_interval = poll_interval_seconds
        self._inbox: queue.Queue[Any] | None = None
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

    def start(self, inbox: queue.Queue[Any]) -> None:
        if self._thread is not None:
            return
        self._inbox = inbox
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="IdleReflectionSource")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        if self._thread is None:
            return
        self._stop_evt.set()
        self._thread.join(timeout=timeout)
        self._thread = None

    def fire_now(self) -> None:
        """立即塞一条 idle_reflection SystemTriggerEvent（silent turn）。

        Raises:
            RuntimeError: 在 :meth:`start` 之前调用。
        """
        if self._inbox is None:
            raise RuntimeError("IdleReflectionSource 尚未 start，inbox 未绑定")
        self._inbox.put(
            SystemTriggerEvent(
                session_id=self._session_id,
                source_kind=self.name,
                system_prompt_addendum=self._prompt_addendum,
                output_visibility="memory_only",
                event_metadata={"idle_threshold_minutes": self._idle_minutes},
            )
        )

    def _loop(self) -> None:
        idle_threshold_seconds = self._idle_minutes * 60.0
        while not self._stop_evt.is_set():
            now = self._now_factory()
            last = self._runtime.last_dispatch_finished_at
            elapsed = (now - last).total_seconds()
            need = idle_threshold_seconds - elapsed
            if need <= 0:
                self.fire_now()
                # fire 后等满一个 idle 周期，避免连续狂触
                if self._stop_evt.wait(timeout=idle_threshold_seconds):
                    return
            else:
                # 还没到阈值——等 min(剩余, poll_interval)
                if self._stop_evt.wait(timeout=min(need, self._poll_interval)):
                    return
