"""Run one foreground dev command while mirroring combined output to bounded log files."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from uuid import uuid4

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_KEEP_FILES = 3


class RotatingBinaryLog:
    def __init__(self, path: Path, *, max_bytes: int, keep_files: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if keep_files <= 0:
            raise ValueError("keep_files must be positive")
        self.path = path
        self.max_bytes = max_bytes
        self.keep_files = keep_files
        self._file = None
        self._size = 0

    def __enter__(self) -> RotatingBinaryLog:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("dev output log must not be a symlink")
        now = int(time.time())
        for old in self.path.parent.glob(f"{self.path.name}.legacy-*"):
            suffix = old.name.removeprefix(f"{self.path.name}.legacy-").split("-", 1)
            if len(suffix) != 2 or not suffix[0].isdigit() or len(suffix[1]) != 32:
                continue
            if (
                all(c in "0123456789abcdef" for c in suffix[1])
                and now - int(suffix[0]) >= 7 * 86400
                and old.is_file()
                and not old.is_symlink()
            ):
                old.unlink()
        if self.path.exists() and self.path.stat().st_size > self.max_bytes:
            self.path.rename(self.path.with_name(f"{self.path.name}.legacy-{now}-{uuid4().hex}"))
        self._open()
        return self

    def __exit__(self, *_args: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def write(self, data: bytes) -> None:
        if self._file is None:
            raise RuntimeError("log is not open")
        remaining_data = memoryview(data)
        while remaining_data:
            available = self.max_bytes - self._size
            if available <= 0:
                self._rotate()
                available = self.max_bytes
            chunk = remaining_data[:available]
            self._file.write(chunk)
            self._file.flush()
            self._size += len(chunk)
            remaining_data = remaining_data[len(chunk) :]

    def _open(self) -> None:
        self._file = self.path.open("ab")
        self._size = self.path.stat().st_size
        if self._size >= self.max_bytes:
            self._rotate()

    def _rotate(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        if self.keep_files <= 1:
            self.path.unlink(missing_ok=True)
        else:
            oldest = self._segment(self.keep_files - 1)
            oldest.unlink(missing_ok=True)
            for index in range(self.keep_files - 2, 0, -1):
                source = self._segment(index)
                if source.exists():
                    source.replace(self._segment(index + 1))
            if self.path.exists():
                self.path.replace(self._segment(1))
        self._file = self.path.open("wb")
        self._size = 0

    def _segment(self, index: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{index}")


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _write_console(data: bytes, prefix: bytes, at_line_start: bool = True) -> bool:
    try:
        for piece in data.splitlines(keepends=True):
            if prefix and at_line_start:
                sys.stdout.buffer.write(prefix)
            sys.stdout.buffer.write(piece)
            at_line_start = piece.endswith(b"\n")
        sys.stdout.buffer.flush()
    except (BrokenPipeError, OSError):
        pass
    return at_line_start


def run_logged(
    command: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    prefix: str = "",
    max_bytes: int = DEFAULT_MAX_BYTES,
    keep_files: int = DEFAULT_KEEP_FILES,
) -> int:
    if not command:
        raise ValueError("command is required")
    prefix_bytes = prefix.encode("utf-8")
    header = f"===== {_timestamp()} start: {' '.join(command)} =====\n".encode()

    with RotatingBinaryLog(log_path, max_bytes=max_bytes, keep_files=keep_files) as sink:
        sink.write(header)
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=None,
        )
        assert process.stdout is not None
        at_line_start = True
        log_failed = False
        dropped = 0
        try:
            for chunk in iter(lambda: process.stdout.read1(64 * 1024), b""):
                if not log_failed:
                    try:
                        sink.write(chunk)
                    except OSError as exc:
                        log_failed = True
                        print(
                            f"dev output log failed; continuing to drain output: {exc}",
                            file=sys.stderr,
                        )
                if log_failed:
                    dropped += len(chunk)
                at_line_start = _write_console(chunk, prefix_bytes, at_line_start)
            exit_code = process.wait()
        except KeyboardInterrupt:
            if process.poll() is None:
                if os.name == "nt":
                    process.terminate()
                else:
                    process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            exit_code = process.returncode if process.returncode is not None else 130

        footer = f"===== {_timestamp()} exit: code={exit_code} =====\n".encode()
        if log_failed:
            print(f"dev output log lost at least {dropped} bytes", file=sys.stderr)
        else:
            sink.write(footer)
        return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--keep-files", type=int, default=DEFAULT_KEEP_FILES)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        parser.error("a command is required after --")
    return run_logged(
        command,
        cwd=args.cwd,
        log_path=args.log,
        prefix=args.prefix,
        max_bytes=args.max_bytes,
        keep_files=args.keep_files,
    )


if __name__ == "__main__":
    raise SystemExit(main())
