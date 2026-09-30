"""Disposable replay index: old transcript payloads never accumulate in the heap."""

from __future__ import annotations

import json
from collections import OrderedDict
from tempfile import TemporaryFile
from typing import Any, BinaryIO, cast

from ..messages import Message


class RolloutItems:
    """Keep verified bytes in an anonymous temporary file, with small seek offsets.

    A later commit may refer to an earlier item, so dropping items after their first
    commit is unsafe. This index lives only for one replay and is never authority.
    """

    def __init__(self) -> None:
        self._file = cast(BinaryIO, TemporaryFile(mode="w+b"))  # noqa: SIM115 - owned by this context manager
        self._offsets: dict[str, tuple[int, int]] = {}
        self._recent: OrderedDict[str, tuple[dict[str, Any], bytes, Message]] = OrderedDict()
        self._recent_bytes = 0

    def __enter__(self) -> RolloutItems:
        return self

    def __exit__(self, *args: object) -> None:
        self._file.close()

    def __contains__(self, key: str) -> bool:
        return key in self._offsets

    def put(self, key: str, canonical: bytes, raw: dict[str, Any], message: Message) -> None:
        self._file.seek(0, 2)
        self._offsets[key] = (self._file.tell(), len(canonical))
        self._file.write(canonical)
        previous = self._recent.pop(key, None)
        if previous is not None:
            self._recent_bytes -= len(previous[1])
        if len(canonical) <= 8 * 1024**2:
            self._recent[key] = (raw, canonical, message)
            self._recent_bytes += len(canonical)
        while len(self._recent) > 256 or self._recent_bytes > 8 * 1024**2:
            _, evicted = self._recent.popitem(last=False)
            self._recent_bytes -= len(evicted[1])

    def get(self, key: str) -> tuple[dict[str, Any], bytes, Message]:
        cached = self._recent.get(key)
        if cached is not None:
            self._recent.move_to_end(key)
            return cached
        offset, length = self._offsets[key]
        self._file.seek(offset)
        raw = self._file.read(length)
        if len(raw) != length:
            raise OSError("rollout replay index truncated")
        value = cast(dict[str, Any], json.loads(raw))
        return value, raw, Message.from_dict(value)
