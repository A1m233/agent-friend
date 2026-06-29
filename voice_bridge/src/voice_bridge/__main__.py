"""``python -m voice_bridge`` 入口：启动 uvicorn 跑 :func:`create_app`。

绑定地址 / 端口 / 日志级别等来自 :class:`VoiceBridgeSettings`。
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from runtime_config import (
    CONFIG_MODE_ENV,
    config_mode_from_env,
    normalize_config_mode,
    settings_kwargs_for_mode,
)

from .settings import VoiceBridgeSettings


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run voice_bridge.")
    parser.add_argument(
        "--config-mode",
        choices=("desktop", "legacy-dotenv", "env-only"),
        help="runtime config mode; default comes from AGENT_FRIEND_CONFIG_MODE or desktop",
    )
    parser.add_argument(
        "--legacy-dotenv",
        action="store_true",
        help="shortcut for --config-mode legacy-dotenv",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    if args.legacy_dotenv:
        os.environ[CONFIG_MODE_ENV] = "legacy-dotenv"
    elif args.config_mode:
        os.environ[CONFIG_MODE_ENV] = normalize_config_mode(args.config_mode)

    mode = config_mode_from_env()
    settings = VoiceBridgeSettings(**settings_kwargs_for_mode(mode))
    uvicorn.run(
        "voice_bridge.app:create_app",
        host=settings.host,
        port=settings.port,
        factory=True,
        log_level=settings.log_level.lower(),
        reload=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
