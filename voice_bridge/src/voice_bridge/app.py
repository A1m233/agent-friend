"""voice_bridge FastAPI app 工厂。

启动期一次性装配 :class:`VoiceBridgeRuntime`，把控制平面 + LLM 入站代理路由
挂到 app 上。

详见 docs/requirements/007-voice-call/design.md §4.2.3。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .assembly import VoiceBridgeRuntime, build_runtime
from .routes import register_control_routes, register_llm_proxy_routes
from .settings import VoiceBridgeSettings


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )


def _add_local_smoke_cors(app: FastAPI) -> None:
    """允许本机 smoke HTML 从任意 localhost 端口调用 voice_bridge。"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            "http://127.0.0.1:8765",
            "http://localhost:8765",
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
    settings = settings or VoiceBridgeSettings()
    _configure_logging(settings.log_level)
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
    return app
