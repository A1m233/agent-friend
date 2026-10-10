"""Cross-process locks; filenames persist, ownership does not."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class Busy(RuntimeError):
    pass


_held = threading.local()


@contextmanager
def lock(path: Path) -> Iterator[None]:
    key = (os.getpid(), str(path.resolve()))
    counts = getattr(_held, "counts", {})
    _held.counts = counts
    if key in counts:
        counts[key] += 1
        try:
            yield
        finally:
            counts[key] -= 1
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise Busy(f"build resource is busy: {path.name}") from error
        counts[key] = 1
        try:
            yield
        finally:
            del counts[key]
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
