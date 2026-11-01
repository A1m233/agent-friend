"""Short-lived, rotation-safe reads; observing must not lock the writer's log."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


def shared_reader(path: Path) -> BinaryIO:
    if os.name != "nt":
        return path.open("rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    # GENERIC_READ; share read/write/delete; OPEN_EXISTING. Never create/write.
    handle = create(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close(handle)
        raise
    return os.fdopen(fd, "rb")


class RotatingLogTail:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.offsets: dict[tuple[int, int], int] = {}
        self.errors: list[str] = []
        self._read(seed=True)

    def _paths(self) -> list[Path]:
        return [self.path.with_name(f"{self.path.name}.{i}") for i in range(5, 0, -1)] + [self.path]

    def _read(self, *, seed: bool = False) -> list[str]:
        lines: list[str] = []
        for path in self._paths():
            try:
                with shared_reader(path) as stream:
                    stat = os.fstat(stream.fileno())
                    identity = (stat.st_dev, stat.st_ino)
                    offset = self.offsets.get(identity, 0)
                    if seed:
                        self.offsets[identity] = stat.st_size
                        continue
                    stream.seek(offset if offset <= stat.st_size else 0)
                    block = stream.read(16 * 1024 * 1024)
                    complete = block.rfind(b"\n") + 1
                    self.offsets[identity] = stream.tell() - len(block) + complete
                    lines.extend(block[:complete].decode("utf-8", errors="replace").splitlines())
            except FileNotFoundError:
                continue  # Rotation may occur between enumeration and open.
            except OSError as exc:
                error = f"{type(exc).__name__}:{getattr(exc, 'winerror', exc.errno)}"
                if error not in self.errors:
                    self.errors.append(error)
        return lines

    def readlines(self) -> list[str]:
        return self._read()
