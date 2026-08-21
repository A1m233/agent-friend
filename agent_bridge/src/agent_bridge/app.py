"""FastAPI 应用装配。

:func:`create_app` 是 ``uvicorn --factory`` 模式的入口：

- 进程启动时调用一次，构造 :class:`BridgeRuntime` 与 :class:`FastAPI` 实例
- 把各协议层的路由（M6.1：OpenAI；M6.2 起：+ AG-UI + meta REST；
  M14.5 起：+ push channel + 可选 dev fire-source）挂到同一个 :class:`FastAPI` 实例
- 注册健康检查 ``GET /healthz``
- 014 起：FastAPI lifespan 在启动时调 :meth:`AgentRuntime.start`、关闭时调
  :meth:`AgentRuntime.stop`，确保 main loop 与 bridge 进程同生共死

详见 docs/requirements/006-agent-bridge/design.md §4.2 + §4.10、
docs/requirements/014-engine-main-loop-and-bridge-push/design.md §8.6。
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler

from agent.paths import log_dir
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from runtime_config import (
    config_mode_from_env,
    credential_source_for_mode,
    settings_kwargs_for_mode,
)

from .assembly import BridgeRuntime, build_runtime
from .attachment_reconciliation import reconcile_attachments
from .lifecycle import LIFECYCLE_TOKEN_ENV, BridgeLifecycleController, register_lifecycle_routes
from .migration import register_migration_routes
from .protocols.ag_ui import register_routes as register_ag_ui_routes
from .protocols.im import register_routes as register_im_routes
from .protocols.openai import register_routes as register_openai_routes
from .push import register_routes as register_push_routes
from .routes.approvals import register_routes as register_approval_routes
from .routes.attachments import register_routes as register_attachment_routes
from .routes.filesystem import register_routes as register_filesystem_routes
from .routes.memory import register_routes as register_memory_routes
from .routes.meta import register_routes as register_meta_routes
from .routes.models import register_routes as register_model_routes
from .routes.persona import register_routes as register_persona_routes
from .routes.presence import register_routes as register_presence_routes
from .routes.runs import register_routes as register_run_routes
from .routes.skills import register_routes as register_skill_routes
from .routes.social_auth import register_routes as register_social_auth_routes
from .routes.todos import register_routes as register_todo_routes
from .runtime_config import resolve_bridge_runtime_config
from .runtime_credentials import register_runtime_credential_routes
from .settings import BridgeSettings

TAURI_PRODUCTION_ORIGINS = [
    "http://tauri.localhost",
    "https://tauri.localhost",
]
JSON_COMPRESSION_MINIMUM_SIZE = 1024
JSON_COMPRESSION_LEVEL = 5


def _add_tauri_production_cors(app: FastAPI) -> None:
    """Allow desktop WebViews to call the loopback bridge directly.

    Tauri development uses a localhost Vite origin while packaged windows use
    ``tauri.localhost``. Pure browser development may still use Vite's proxy, but allowing the
    localhost origin lets Tauri dev exercise the same direct-loopback topology as production.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=TAURI_PRODUCTION_ORIGINS,
        allow_origin_regex=r"http://(127\.0\.0\.1|localhost):\d+",
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "X-Attachment-Filename"],
    )


def create_app() -> FastAPI:
    """``uvicorn --factory`` 入口；进程启动时被调用一次。

    本函数**不**显式接受参数；配置 mode 通过 ``AGENT_FRIEND_CONFIG_MODE`` 设置。
    默认桌面主路径只读 process env + system keyring；存量 ``.env`` 需显式
    ``legacy-dotenv`` mode。
    """
    mode = config_mode_from_env()
    settings = BridgeSettings(**settings_kwargs_for_mode(mode))
    _configure_logging(settings.log_level)
    logging.getLogger(__name__).info(
        "agent_bridge logging configured log_dir=%s pid=%s executable=%s base_executable=%s config_mode=%s",
        log_dir(),
        os.getpid(),
        sys.executable,
        getattr(sys, "_base_executable", ""),
        mode,
    )
    credentials = credential_source_for_mode(mode)
    runtime_config = resolve_bridge_runtime_config(credentials)
    runtime = build_runtime(settings, runtime_config=runtime_config)
    inspector_env = os.environ.get("AGENT_FRIEND_MEMORY_INSPECTOR_ENABLED")
    memory_inspector_enabled = (
        inspector_env.lower() == "true"
        if inspector_env is not None
        else os.environ.get("AGENT_BRIDGE_DEV_MODE", "false").lower() == "true"
    )
    return create_app_with_runtime(
        runtime,
        lifecycle_token=os.environ.get(LIFECYCLE_TOKEN_ENV),
        memory_inspector_enabled=memory_inspector_enabled,
    )


def create_app_with_runtime(
    runtime: BridgeRuntime,
    *,
    lifecycle_token: str | None = None,
    shutdown_callback: Callable[[], None] | None = None,
    memory_inspector_enabled: bool = True,
) -> FastAPI:
    """测试 helper：直接传入已经装配好的 :class:`BridgeRuntime`，绕过环境变量加载。

    006 起新增。生产用 :func:`create_app`，测试 / 集成场景注入 runtime。
    """
    app = FastAPI(
        title="agent-bridge",
        version="0.1.0",
        description=(
            "agent-friend 的 HTTP SSE 出口；M6.2 启用 OpenAI + AG-UI 双协议 + meta REST；"
            "M14.5 起加 push channel + 可选 dev fire-source。"
        ),
        lifespan=_make_lifespan(runtime),
    )
    _add_tauri_production_cors(app)
    app.add_middleware(
        GZipMiddleware,
        minimum_size=JSON_COMPRESSION_MINIMUM_SIZE,
        compresslevel=JSON_COMPRESSION_LEVEL,
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    register_openai_routes(app, runtime)
    register_ag_ui_routes(app, runtime)
    register_meta_routes(app, runtime)
    if memory_inspector_enabled:
        register_memory_routes(app, runtime)
    register_attachment_routes(app, runtime)
    register_approval_routes(app, runtime)
    register_filesystem_routes(app, runtime)
    register_model_routes(app, runtime)
    register_persona_routes(app, runtime)
    register_run_routes(app, runtime)
    register_todo_routes(app, runtime)
    register_presence_routes(app, runtime)
    register_social_auth_routes(app, runtime)
    register_skill_routes(app, runtime)
    register_push_routes(app, runtime)
    if runtime.im_onboard_registry is not None:
        register_im_routes(app, runtime, runtime.im_onboard_registry)

    if runtime.settings.dev_mode:
        from .dev.fire_source import register_routes as register_dev_fire_routes

        register_dev_fire_routes(app, runtime)

    if lifecycle_token:
        controller = BridgeLifecycleController(
            runtime,
            token=lifecycle_token,
            shutdown_callback=shutdown_callback,
        )
        app.state.lifecycle_controller = controller
        register_lifecycle_routes(app, controller)
        register_migration_routes(app, runtime, controller)
        register_runtime_credential_routes(app, controller)

    return app


def _make_lifespan(runtime: BridgeRuntime):  # type: ignore[no-untyped-def]
    """构造 FastAPI lifespan：启动期拉起 AgentRuntime；退出期 stop + drain。"""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        reconcile_attachments(runtime)
        if runtime.skill_service is not None:
            runtime.skill_service.start()
        if runtime.first_party_skill_updater is not None:
            runtime.first_party_skill_updater.start()
        if runtime.agent_runtime is not None:
            runtime.agent_runtime.start()
        if runtime.im_runtime is not None:
            runtime.im_runtime.start()
        if runtime.reminder_scheduler is not None:
            runtime.reminder_scheduler.start()
        try:
            yield
        finally:
            if runtime.reminder_scheduler is not None:
                runtime.reminder_scheduler.stop(timeout=5.0)
            if runtime.im_runtime is not None:
                await runtime.im_runtime.stop(timeout=5.0)
            if runtime.agent_runtime is not None:
                runtime.agent_runtime.stop(timeout=5.0)
            runtime.close()

    return lifespan


class IsoLocalFormatter(logging.Formatter):
    """ISO8601 + milliseconds + local tz, matching ``{ts} [{level:5}] [{name}] {message}``."""

    LEVEL_WIDTH = 5

    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return (
            datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds")
        )

    def format(self, record: logging.LogRecord) -> str:
        record.levelname = f"{record.levelname:<{self.LEVEL_WIDTH}}"
        return super().format(record)


def _configure_logging(level: str) -> None:
    """配置 root + memory 双 handler 树，统一格式写到 ``log_dir()`` 下。

    - root logger 挂 stream + ``agent_bridge.log``，覆盖 ``agent.*`` / ``agent_bridge.*``
      / ``llm_providers.*`` / ``tools.*`` / ``shared.*`` 等子树。
    - ``memory`` logger ``propagate=False`` 单独挂 ``memory.log``，避免重复。
    - 调用前先清已有 handler，保证 idempotent（测试反复调用 / 生产二次初始化安全）。
    """
    level_int = logging.getLevelName(level.upper())
    formatter = IsoLocalFormatter()
    target_dir = log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level_int)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)

    bridge_file = RotatingFileHandler(
        target_dir / "agent_bridge.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    bridge_file.setFormatter(formatter)
    root.addHandler(bridge_file)

    memory_logger = logging.getLogger("memory")
    memory_logger.propagate = False
    for h in list(memory_logger.handlers):
        memory_logger.removeHandler(h)
    memory_file = RotatingFileHandler(
        target_dir / "memory.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    memory_file.setFormatter(formatter)
    memory_logger.addHandler(memory_file)
