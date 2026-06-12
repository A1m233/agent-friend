"""014 · dev 端点 ``POST /dev/fire-source``：立即触发指定 EventSource。

**仅** ``BridgeSettings.dev_mode=True`` 时由 :func:`agent_bridge.app.create_app_with_runtime`
挂载——生产环境完全不存在该路由，避免外部跨进程触发主动轮。

用例：dev / 测试场景下不想等 23:00 真 bedtime 才看到 BedtimeSource 行为——
``curl -X POST localhost:18800/dev/fire-source?source_name=cron:bedtime``
即可立即触发一次。

详见 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §6 + §9.1。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ..assembly import BridgeRuntime


logger = logging.getLogger(__name__)


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    """挂 dev fire-source 路由到 FastAPI app。

    本路由**只在** :func:`agent_bridge.app.create_app_with_runtime` 看到
    ``settings.dev_mode=True`` 时被调用挂载。
    """
    router = APIRouter()

    @router.post("/dev/fire-source")
    async def fire_source(
        source_name: str = Query(
            ...,
            description="目标 source 的 name 字段（如 cron:bedtime / idle_reflection）",
        ),
    ) -> dict[str, str]:
        if runtime.agent_runtime is None:
            raise HTTPException(
                status_code=503,
                detail="agent_runtime 未装配，dev fire 不可用",
            )

        # 找到匹配 name 的 source
        target = None
        for src in runtime.agent_runtime._sources:
            if getattr(src, "name", None) == source_name:
                target = src
                break

        if target is None:
            raise HTTPException(
                status_code=404,
                detail=f"未找到名为 {source_name!r} 的 EventSource",
            )

        fire_now = getattr(target, "fire_now", None)
        if not callable(fire_now):
            raise HTTPException(
                status_code=400,
                detail=f"source {source_name!r} 不支持 fire_now（如 UserSource 需走 submit）",
            )

        try:
            fire_now()
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

        logger.info("dev fire-source 触发：%s", source_name)
        return {"status": "fired", "source": source_name}

    app.include_router(router)
