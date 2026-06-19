"""``agent_bridge.app._configure_logging`` 单测。

覆盖：
- root logger 同时挂载 stream + RotatingFileHandler
- ``memory`` logger ``propagate=False`` 且有独立 file handler
- format prefix 符合 ``{ISO8601 ms} [{LEVEL:5}] [{name}] {message}``
- 反复调用不重复挂 handler
- ``AGENT_FRIEND_LOG_DIR`` env 把日志写到临时目录
"""

from __future__ import annotations

import logging
import typing
from pathlib import Path

import pytest
from agent.paths import LOG_DIR_ENV
from agent_bridge.app import IsoLocalFormatter, _configure_logging


@pytest.fixture(autouse=True)
def _reset_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """每次测试前清理 memory / root handler，避免跨测试状态污染。"""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    memory = logging.getLogger("memory")
    for h in list(memory.handlers):
        memory.removeHandler(h)
    memory.propagate = True


def test_root_has_stream_and_file_handler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path))
    _configure_logging("INFO")

    root = logging.getLogger()
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    assert any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        and h.baseFilename == str(tmp_path / "agent_bridge.log")
        for h in root.handlers
    )


def test_memory_logger_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path))
    _configure_logging("INFO")

    memory = logging.getLogger("memory")
    assert memory.propagate is False
    assert len(memory.handlers) == 1
    assert isinstance(memory.handlers[0], logging.handlers.RotatingFileHandler)
    assert memory.handlers[0].baseFilename == str(tmp_path / "memory.log")


def test_memory_log_does_not_propagate_to_root_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path))
    _configure_logging("INFO")

    memory = logging.getLogger("memory")
    memory.info("memory-only-message")

    bridge_log = tmp_path / "agent_bridge.log"
    memory_log = tmp_path / "memory.log"
    assert memory_log.exists()
    assert "memory-only-message" in memory_log.read_text(encoding="utf-8")
    assert not bridge_log.exists() or "memory-only-message" not in bridge_log.read_text(
        encoding="utf-8"
    )


def test_format_prefix_matches_design() -> None:
    formatter = IsoLocalFormatter()
    record = logging.LogRecord(
        name="agent.runtime.listeners",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="cursor feed thread spawned",
        args=(),
        exc_info=None,
    )
    record.created = 1750325025.123
    text = formatter.format(record)
    assert text.startswith("2025-06-19T")
    assert "[INFO ]" in text
    assert "[agent.runtime.listeners]" in text
    assert "cursor feed thread spawned" in text


def test_repeated_calls_do_not_duplicate_handlers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path))
    _configure_logging("INFO")
    _configure_logging("INFO")

    root = logging.getLogger()
    assert len(root.handlers) == 2
    memory = logging.getLogger("memory")
    assert len(memory.handlers) == 1


def test_rotation_creates_backup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """用小 maxBytes 触发滚文件：写超阈值后 .1 出现。"""
    monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path))
    _configure_logging("INFO")

    memory = logging.getLogger("memory")
    handler = typing.cast(logging.handlers.RotatingFileHandler, memory.handlers[0])
    # 临时把阈值降到 128 字节以触发 rotation
    handler.maxBytes = 128
    handler.backupCount = 2

    for _ in range(50):
        memory.info("x" * 50)

    backups = list(tmp_path.glob("memory.log*"))
    assert any(b.name == "memory.log.1" for b in backups)
