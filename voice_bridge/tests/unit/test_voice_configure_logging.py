"""``voice_bridge.app._configure_logging`` file handler tests."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest
from voice_bridge.app import _configure_logging


@pytest.fixture(autouse=True)
def _reset_logging() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)


def test_file_log_uses_rotating_handler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_FRIEND_LOG_DIR", str(tmp_path))
    _configure_logging("INFO", file_log=True)

    root = logging.getLogger()
    file_handlers = [
        handler
        for handler in root.handlers
        if isinstance(handler, RotatingFileHandler)
        and getattr(handler, "_agent_friend_voice_bridge_file", False)
    ]
    assert len(file_handlers) == 1
    assert file_handlers[0].baseFilename == str(tmp_path / "voice_bridge.log")
    assert file_handlers[0].maxBytes == 10 * 1024 * 1024
    assert file_handlers[0].backupCount == 5


def test_repeated_file_log_setup_does_not_duplicate_handler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENT_FRIEND_LOG_DIR", str(tmp_path))
    _configure_logging("INFO", file_log=True)
    _configure_logging("INFO", file_log=True)

    root = logging.getLogger()
    assert (
        sum(getattr(handler, "_agent_friend_voice_bridge_file", False) for handler in root.handlers)
        == 1
    )
