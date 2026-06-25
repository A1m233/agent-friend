"""``agent.paths`` 单测：用户数据目录解析。

覆盖：
- 默认走系统标准用户数据目录（最后一段为 ``agent-friend``）
- ``AGENT_FRIEND_DATA_DIR`` 环境变量整体覆盖（含 ``~`` 展开）
- sessions / memory / personas / cli_history 各子路径布局正确
- :class:`agent.PersonaCatalog` 默认目录跟随同一套解析（端到端验证）

所有用例都用 monkeypatch 隔离 env，不污染真实系统目录。
"""

from __future__ import annotations

from pathlib import Path

import platformdirs
import pytest
from agent.paths import APP_NAME, DATA_DIR_ENV, LOG_DIR_ENV

from agent import (
    PersonaCatalog,
    cli_history_path,
    log_dir,
    memory_db_path,
    personas_dir,
    sessions_dir,
    user_data_dir,
)


def test_default_user_data_dir_uses_app_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    root = user_data_dir()
    assert root.is_absolute()
    assert root.name == "agent-friend"


def test_env_override_takes_precedence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "custom"))
    assert user_data_dir() == tmp_path / "custom"


def test_env_override_expands_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, "~/agent-friend-data")
    assert user_data_dir() == Path.home() / "agent-friend-data"


def test_subpaths_hang_under_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    assert sessions_dir() == tmp_path / "sessions"
    assert memory_db_path() == tmp_path / "memory" / "memory.db"
    assert personas_dir() == tmp_path / "personas"
    assert cli_history_path() == tmp_path / ".cli_history"


def test_resolved_lazily_per_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """env 改变后下一次调用即时生效（不是 import 时定死）。"""
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "a"))
    assert user_data_dir() == tmp_path / "a"
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "b"))
    assert user_data_dir() == tmp_path / "b"


def test_persona_catalog_default_follows_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``PersonaCatalog()`` 不传 external_dir 时，落到解析出的 personas 目录。"""
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    catalog = PersonaCatalog()
    info = catalog.create(name="tester", content="你是一个测试用人设。")
    expected = tmp_path / "personas" / "tester.md"
    assert expected.exists()
    assert catalog.read_content(info.id) == "你是一个测试用人设。"


def test_persona_catalog_explicit_dir_overrides_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """显式传 external_dir 时忽略 env 默认。"""
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "ignored"))
    explicit = tmp_path / "explicit"
    catalog = PersonaCatalog(external_dir=explicit)
    catalog.create(name="tester", content="显式目录人设。")
    assert (explicit / "tester.md").exists()
    assert not (tmp_path / "ignored").exists()


def test_log_dir_env_override_takes_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path / "logs"))
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    assert log_dir() == tmp_path / "logs"


def test_log_dir_env_override_expands_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LOG_DIR_ENV, "~/agent-friend-logs")
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    assert log_dir() == Path.home() / "agent-friend-logs"


def test_log_dir_default_uses_platform_log_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LOG_DIR_ENV, raising=False)
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    root = log_dir()
    assert root.is_absolute()
    assert root == Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))
    assert APP_NAME in root.parts
