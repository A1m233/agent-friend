"""Run one foreground dev command while mirroring combined output to bounded log files."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

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


def _write_console(data: bytes, prefix: bytes) -> None:
    try:
        if prefix:
            sys.stdout.buffer.write(prefix)
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    except (BrokenPipeError, OSError):
        pass


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
        try:
            for line in iter(process.stdout.readline, b""):
                sink.write(line)
                _write_console(line, prefix_bytes)
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
