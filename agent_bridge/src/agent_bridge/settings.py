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
from agent import user_data_dir as _user_data_dir


def _im_credentials_dir() -> Path:
    """默认 IM 凭据存储目录:``<user_data_dir>/im_credentials/``。

    AES-GCM 加密落盘,跟 sessions/ memory/ personas/ 同级。
    """
    return _user_data_dir() / "im_credentials"


def _im_resume_dir() -> Path:
    """默认 IM resume token 目录:``<user_data_dir>/im_resume/``。

    SDK 自管的 WebSocket session_id + last_seq 用于断线 Resume;
    不是凭据(无需加密),纯 json 落盘。
    """
    return _user_data_dir() / "im_resume"


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

    # 022:IM 通道相关
    im_enabled: bool = True
    """是否启用 IM 通道(``protocols/im/``)。默认 ``True``——本进程启动时
    :class:`IMRuntime` 装配 + 加载已绑定凭据 + lifespan start/stop。

    置 ``False`` 时 ``BridgeRuntime.im_runtime`` 保持 ``None``,``/v1/im/*``
    路由全部 503;便于纯 OpenAI / AG-UI 出口测试或排查时禁用 IM。"""

    im_credentials_dir: Path = Field(default_factory=_im_credentials_dir)
    """IM 凭据 AES-GCM 加密落盘目录;默认 ``<user_data_dir>/im_credentials/``,
    可用 ``AGENT_BRIDGE_IM_CREDENTIALS_DIR`` 覆盖(测试 / sandbox 用)。"""

    im_resume_dir: Path = Field(default_factory=_im_resume_dir)
    """IM SDK Resume token(WebSocket session_id + last_seq)落盘目录;
    默认 ``<user_data_dir>/im_resume/``,不加密。"""

    # 014：main loop / push 通道相关
    dev_mode: bool = False
    """是否挂载 ``/dev/*`` 端点（如 ``/dev/fire-source``）。**生产环境永不开**——
    端点能跨进程立即触发任意 EventSource，仅供 dev / 测试使用。
    通过 ``AGENT_BRIDGE_DEV_MODE`` 显式开启；默认 ``False``。"""

    enable_bedtime: bool = False
    """是否启用 :class:`BedtimeSource`。默认 ``False``——主动陪伴属于产品决策，
    没显式开启不应该自己冒出来。开启时同时设置 ``bedtime_target_session_id``。"""

    bedtime_target_session_id: str = ""
    """``enable_bedtime=True`` 时 bedtime 提醒发给哪个 session 的 id；
    必须是 ``sessions_dir`` 下已存在的 session id（v1 单 session 假设）。"""

    bedtime_hour: int = 23
    """bedtime 触发时（local time），0-23。默认 23（晚上 11 点）。"""

    bedtime_minute: int = 0
    """bedtime 触发分（local time），0-59。默认 0。"""

    enable_idle_reflection: bool = False
    """是否启用 :class:`IdleReflectionSource`。默认 ``False``。"""

    idle_target_session_id: str = ""
    """``enable_idle_reflection=True`` 时 silent turn 落入哪个 session 的 id。"""

    idle_minutes: int = 30
    """idle reflection 触发的空闲阈值（分钟）。默认 30。"""
