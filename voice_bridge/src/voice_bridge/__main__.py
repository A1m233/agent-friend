"""``python -m voice_bridge`` 入口：启动 uvicorn 跑 :func:`create_app`。

绑定地址 / 端口 / 日志级别等来自 :class:`VoiceBridgeSettings`（环境变量 / ``.env``）。
"""

from __future__ import annotations

import sys

import uvicorn
from dotenv import load_dotenv

from .settings import VoiceBridgeSettings


def main() -> int:
    load_dotenv()
    settings = VoiceBridgeSettings()
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
