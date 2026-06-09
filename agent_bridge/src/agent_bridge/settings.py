"""bridge 进程配置项。

使用 :mod:`pydantic_settings` 从环境变量 / ``.env`` 加载，优先级：
环境变量 > ``.env`` > 默认值（详见 docs/decisions/0002-incubation-tech-stack §3.17）。

本期只暴露最少几个字段：监听地址 / 端口 / 日志级别 / 会话目录。

数据路径默认走系统标准用户数据目录（决策 0002 §3.19，见 :mod:`agent.paths`），
可用 ``AGENT_FRIEND_DATA_DIR`` 整体覆盖，或用本配置的 ``AGENT_BRIDGE_*`` 单项覆盖。

详见 docs/requirements/006-agent-bridge/design.md §4.2。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent import memory_db_path, personas_dir, sessions_dir


class BridgeSettings(BaseSettings):
    """bridge 启动配置。

    所有字段都有合理默认值，开发期无需配置 ``.env`` 即可启动。

    Attributes:
        host: 监听地址；默认仅 bind 本机回环，符合
            [0002 §3.12](../../decisions/0002-incubation-tech-stack/README.md) 安全约束。
        port: 监听端口；与 ``0002 §3.18`` 预留的 18765 不同，本期默认 18800
            避免与既有占用冲突；通过 ``AGENT_BRIDGE_PORT`` 覆盖。
        log_level: uvicorn / FastAPI 日志级别。
        sessions_dir: AG-UI 持久化出口指向的 sessions 目录；默认走系统用户数据目录
            下的 ``sessions/``，与 CLI in-process 模式共享同一份会话（参见 006 R-4.3.2）。
        memory_db: 长期记忆库（SQLite）路径；默认走系统用户数据目录下的
            ``memory/memory.db``，与 CLI in-process 模式共享同一份记忆（008）。父目录自动创建。
        personas_dir: 用户自定义 persona 目录；默认走系统用户数据目录下的 ``personas/``，
            与 CLI in-process 模式共享同一份用户 persona。
        memory_enabled: 是否启用长期记忆；默认开启。置 ``False`` 时 bridge 的
            持久化会话不挂记忆（行为回到 007 及以前），便于排查 / 压测。
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENT_BRIDGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 18800
    log_level: str = "INFO"
    sessions_dir: Path = Field(default_factory=sessions_dir)
    memory_db: Path = Field(default_factory=memory_db_path)
    personas_dir: Path = Field(default_factory=personas_dir)
    memory_enabled: bool = True
