"""voice_bridge FastAPI app 工厂。

启动期一次性装配 :class:`VoiceBridgeRuntime`，把控制平面 + LLM 入站代理路由
挂到 app 上。

详见 docs/requirements/007-voice-call/design.md §4.2.3。
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import platformdirs
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .assembly import VoiceBridgeRuntime, build_runtime
from .routes import (
    register_control_routes,
    register_llm_proxy_routes,
    register_transcription_routes,
)
from .settings import VoiceBridgeSettings


def _voice_log_path() -> Path:
    override = os.environ.get("AGENT_FRIEND_LOG_DIR")
    log_dir = (
        Path(override).expanduser()
        if override
        else Path(platformdirs.user_log_dir("agent-friend", appauthor=False))
    )
    return log_dir / "voice_bridge.log"


def _configure_logging(level: str, *, file_log: bool = False) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    if not file_log:
        return

    if any(
        getattr(handler, "_agent_friend_voice_bridge_file", False)
        for handler in root_logger.handlers
    ):
        return

    try:
        log_path = _voice_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
        handler.setLevel(level.upper())
        handler._agent_friend_voice_bridge_file = True  # type: ignore[attr-defined]
        root_logger.addHandler(handler)
    except OSError as e:
        logging.getLogger(__name__).warning("voice_bridge file log disabled: %s", e)


def _add_local_smoke_cors(app: FastAPI) -> None:
    """允许本机 smoke HTML 从任意 localhost 端口调用 voice_bridge。"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            "http://127.0.0.1:8765",
            "http://localhost:8765",
            # Tauri production windows load bundled assets from tauri.localhost,
            # while dev still goes through Vite localhost.
            "http://tauri.localhost",
            "https://tauri.localhost",
        ],
        allow_origin_regex=r"http://(127\.0\.0\.1|localhost):\d+",
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )


def create_app(settings: VoiceBridgeSettings | None = None) -> FastAPI:
    """构造 FastAPI 实例。

    Args:
        settings: 可选；测试 / 注入场景下传入。生产用 ``None``，从环境变量加载。

    Returns:
        :class:`FastAPI` 实例，已挂控制平面 + LLM 代理路由。
    """
    load_from_env = settings is None
    settings = settings or VoiceBridgeSettings()
    _configure_logging(settings.log_level, file_log=load_from_env)
    runtime = build_runtime(settings)

    app = FastAPI(
        title="voice-bridge",
        version="0.1.0",
        description="agent-friend · 语音通话控制平面 + LLM 入站代理",
    )
    app.state.runtime = runtime

    _add_local_smoke_cors(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    register_control_routes(app, runtime)
    register_llm_proxy_routes(app, runtime)
    register_transcription_routes(app, runtime)
    return app


def create_app_with_runtime(runtime: VoiceBridgeRuntime) -> FastAPI:
    """测试 helper：直接传 runtime（绕过环境变量装配）。"""
    _configure_logging(runtime.settings.log_level)
    app = FastAPI(
        title="voice-bridge",
        version="0.1.0",
    )
    app.state.runtime = runtime

    _add_local_smoke_cors(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    register_control_routes(app, runtime)
    register_llm_proxy_routes(app, runtime)
    register_transcription_routes(app, runtime)
    return app
