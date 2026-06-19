"""026 · 召回 trace 的进程内 ring buffer。

容量 100（design §4.4），bridge 重启清空（在 requirement.md 已知限制）。
线程安全：deque 自身的 append/iter 在 CPython 下是线程安全的；
list snapshot 走 list(deque) 一次性拷贝避免迭代期变更。

通过 `Memory.on_retrieved` 回调被动接收 trace，不主动拉。
"""

from __future__ import annotations

from collections import deque
from threading import Lock

from memory.contracts import RecallTrace

__all__ = ["RecallBuffer"]

DEFAULT_CAPACITY = 100


class RecallBuffer:
    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._buffer: deque[RecallTrace] = deque(maxlen=capacity)
        self._lock = Lock()

    def record(self, trace: RecallTrace) -> None:
        """Memory.on_retrieved 钩到这里。"""
        with self._lock:
            self._buffer.append(trace)

    def snapshot(self, limit: int | None = None) -> list[RecallTrace]:
        """倒序返回最近 N 条（None = 全部）。"""
        with self._lock:
            items = list(self._buffer)
        items.reverse()
        return items[:limit] if limit is not None else items

    def clear(self) -> None:
        """清空 buffer（测试 / 手动重置用）。"""
        with self._lock:
            self._buffer.clear()
