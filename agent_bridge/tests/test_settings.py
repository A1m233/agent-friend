"""``BridgeSettings`` 数据路径解析单测。

覆盖：
- 默认数据路径跟随 ``AGENT_FRIEND_DATA_DIR``（经 :mod:`agent.paths` 解析）
- 单项 ``AGENT_BRIDGE_*`` 环境变量可覆盖对应路径
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_bridge.settings import BridgeSettings


def test_data_paths_follow_user_data_dir_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENT_FRIEND_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_BRIDGE_SESSIONS_DIR", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_MEMORY_DB", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_PERSONAS_DIR", raising=False)

    settings = BridgeSettings()

    assert settings.sessions_dir == tmp_path / "sessions"
    assert settings.memory_db == tmp_path / "memory" / "memory.db"
    assert settings.personas_dir == tmp_path / "personas"


def test_bridge_specific_env_overrides_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENT_FRIEND_DATA_DIR", str(tmp_path / "global"))
    monkeypatch.setenv("AGENT_BRIDGE_SESSIONS_DIR", str(tmp_path / "custom-sessions"))

    settings = BridgeSettings()

    assert settings.sessions_dir == tmp_path / "custom-sessions"
    # 未单独覆盖的项仍跟随全局数据目录
    assert settings.memory_db == tmp_path / "global" / "memory" / "memory.db"
