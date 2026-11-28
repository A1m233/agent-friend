"""Bounded native helper execution with cooperative cancellation and owned cleanup."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable, Mapping

from .process import OwnedWindowsProcess


class HelperCleanupUnconfirmed(OSError):
    pass


def run_windows_helper(
    command: list[str],
    *,
    timeout: float,
    check_cancelled: Callable[[], None] = lambda: None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    check_cancelled()
    process = OwnedWindowsProcess(command, env=env or os.environ)
    output, error = bytearray(), bytearray()

    def drain(stream: object, tail: bytearray) -> None:
        try:
            assert hasattr(stream, "read")
            while chunk := stream.read(4096):
                tail.extend(chunk)
                if len(tail) > 65536:
                    del tail[:-65536]
        except (OSError, ValueError):
            return

    readers = [
        threading.Thread(target=drain, args=(stream, tail), daemon=True)
        for stream, tail in ((process.stdout, output), (process.stderr, error))
    ]
    for reader in readers:
        reader.start()
    try:
        assert process.stdin is not None
        process.stdin.close()
        deadline = time.monotonic() + timeout
        while (returncode := process.poll()) is None:
            check_cancelled()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, timeout)
            time.sleep(0.02)
    finally:
        try:
            if not process.stop_and_wait(timeout=5):
                raise HelperCleanupUnconfirmed("native_helper_cleanup_unconfirmed")
            for reader in readers:
                reader.join(timeout=1)
            if any(reader.is_alive() for reader in readers):
                raise HelperCleanupUnconfirmed("native_helper_output_unconfirmed")
        finally:
            process.close()
    return subprocess.CompletedProcess(command, returncode, bytes(output), bytes(error))
