"""用户数据目录解析 —— 集中决定「产品代用户保管」的数据落在哪里。

适用范围：sessions、用户自定义 personas、记忆数据、CLI 历史等——凡是用户视角
属于其本人、需要跨重启留存的数据。**不含**配置 / API key（那走 ``.env`` /
pydantic-settings，见决策 0002 §3.17/§3.18）。

默认路径走系统标准用户数据目录（决策 0002 §3.19）：

- Mac：``~/Library/Application Support/agent-friend/``
- Win：``%APPDATA%/agent-friend/``
- Linux：``~/.local/share/agent-friend/``（``$XDG_DATA_HOME`` 优先）

覆盖方式（便于本地多实例隔离 / 测试用临时目录）：

- 环境变量 ``AGENT_FRIEND_DATA_DIR``：覆盖整个用户数据根目录
- 调用方（CLI / bridge）也可绕过本模块，直接把更细的路径注入到
  :class:`~agent.JsonlSessionStore` / :class:`~agent.PersonaCatalog` /
  ``build_memory`` —— 这些 store 本就接受 path 参数注入

所有函数在**调用时**解析路径（而非 import 时），因此测试设置 env 后即时生效。
父目录的创建由各 store 落盘时负责，本模块只做路径计算、不碰文件系统。
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

APP_NAME = "agent-friend"
"""平台目录解析用的应用名（决定系统数据目录最后一段）。"""

DATA_DIR_ENV = "AGENT_FRIEND_DATA_DIR"
"""覆盖整个用户数据根目录的环境变量名。"""

LOG_DIR_ENV = "AGENT_FRIEND_LOG_DIR"
"""覆盖日志根目录的环境变量名。"""


def user_data_dir() -> Path:
    """返回用户数据根目录。

    优先级：``AGENT_FRIEND_DATA_DIR`` 环境变量 > 系统标准用户数据目录。
    ``roaming=True`` 让 Windows 落在 ``%APPDATA%``（与 0002 §3.19 一致）；
    该参数在 Mac/Linux 上无副作用。
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False, roaming=True))


def sessions_dir() -> Path:
    """会话 JSONL 目录：``<user_data_dir>/sessions``。"""
    return user_data_dir() / "sessions"


def memory_db_path() -> Path:
    """记忆 SQLite 文件：``<user_data_dir>/memory/memory.db``。"""
    return user_data_dir() / "memory" / "memory.db"


def personas_dir() -> Path:
    """用户自定义 persona 目录：``<user_data_dir>/personas``。"""
    return user_data_dir() / "personas"


def cli_history_path() -> Path:
    """CLI prompt 历史文件：``<user_data_dir>/.cli_history``。"""
    return user_data_dir() / ".cli_history"


def log_dir() -> Path:
    """日志根目录。

    优先级：``AGENT_FRIEND_LOG_DIR`` 环境变量 > 系统标准用户日志目录。
    macOS 返回 ``~/Library/Logs/agent-friend``；Windows / Linux 委托
    ``platformdirs.user_log_dir`` 按系统约定解析。
    """
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))


__all__ = [
    "APP_NAME",
    "DATA_DIR_ENV",
    "LOG_DIR_ENV",
    "cli_history_path",
    "log_dir",
    "memory_db_path",
    "personas_dir",
    "sessions_dir",
    "user_data_dir",
]
