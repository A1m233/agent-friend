"""IM channel HTTP routes(022 起):``/v1/im/*``。

供桌面前端"接入 IM"面板调用:列已绑定 / 启动扫码 / 轮询 onboard / 解绑。

| Method | Path | 用途 |
|---|---|---|
| ``GET``    | ``/v1/im/providers``                     | 列已绑定 IM(类型 + 脱敏 id + status) |
| ``POST``   | ``/v1/im/onboard/start``                 | 启动扫码 onboard(返回 task_id) |
| ``GET``    | ``/v1/im/onboard/{task_id}``             | 前端轮询 onboard 状态 |
| ``DELETE`` | ``/v1/im/providers/{im_type}/{bind_id}`` | 解绑(stop provider + 删凭据) |

错误模型(沿 :mod:`agent_bridge.routes.meta` 风格):

- 400:不支持的 im_type
- 404:未知 task_id / 解绑找不到对应 provider
- 503:``runtime.im_runtime is None``(进程未装配 IM,理论不该发生)

详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §4 + §3.9。
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from ...assembly import BridgeRuntime
from .onboard import OnboardSessionRegistry

__all__ = ["register_routes"]

logger = logging.getLogger(__name__)


class OnboardStartBody(BaseModel):
    """``POST /v1/im/onboard/start`` 请求体。"""

    im_type: Literal["qq"] = Field(..., description="目标 IM 类型;本期仅支持 qq")


class OnboardStartResponse(BaseModel):
    task_id: str


def register_routes(
    app: FastAPI,
    runtime: BridgeRuntime,
    onboard_registry: OnboardSessionRegistry,
) -> None:
    """把 ``/v1/im/*`` 4 个端点挂到 :class:`FastAPI` 实例。

    Args:
        app: 已建好的 :class:`FastAPI` 实例。
        runtime: 已装配好的 :class:`BridgeRuntime`,需要 ``runtime.im_runtime``
            已就位(否则所有端点都 503)。
        onboard_registry: 进程级 :class:`OnboardSessionRegistry`,由
            :mod:`agent_bridge.assembly` 装配。
    """
    router = APIRouter(prefix="/v1/im", tags=["im"])

    def _require_im_runtime() -> Any:
        if runtime.im_runtime is None:
            raise HTTPException(
                status_code=503,
                detail="im_runtime 未装配——bridge 当前不接受 IM 操作",
            )
        return runtime.im_runtime

    @router.get("/providers")
    def list_providers() -> list[dict[str, Any]]:
        """返回已绑定 IM 列表(类型 + 脱敏 id + status)。"""
        im = _require_im_runtime()
        return [asdict(info) for info in im.list_status()]

    @router.post("/onboard/start", response_model=OnboardStartResponse)
    async def start_onboard(body: OnboardStartBody) -> OnboardStartResponse:
        """启动一次扫码 onboard;返回 ``task_id`` 给前端轮询。"""
        _require_im_runtime()  # 仅检查 im_runtime 已装配
        try:
            task_id = await onboard_registry.start(body.im_type)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return OnboardStartResponse(task_id=task_id)

    @router.get("/onboard/{task_id}")
    def get_onboard_status(task_id: str) -> dict[str, Any]:
        """前端轮询拿状态。返回的 dict 形如::

        {
            "task_id": "...",
            "im_type": "qq",
            "status": "pending" | "qr_ready" | "success" | "failed",
            "qr_url": "https://..." or null,
            "bind_id_masked": "ABCD...EFGH" or null,
            "error": "..." or null,
        }
        """
        state = onboard_registry.get(task_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"unknown task_id: {task_id}")
        return {
            "task_id": state.task_id,
            "im_type": state.im_type,
            "status": state.status.value,
            "qr_url": state.qr_url,
            "bind_id_masked": state.bind_id_masked,
            "error": state.error,
        }

    @router.delete("/providers/{im_type}/{bind_id}")
    async def unbind_provider(im_type: str, bind_id: str) -> dict[str, Any]:
        """解绑:stop provider + 删凭据。

        Returns:
            ``{"ok": True, "found": True/False}`` —— ``found=False`` 等价于
            "本来就没绑定"(幂等)。
        """
        im = _require_im_runtime()
        found = await im.unbind(im_type, bind_id)
        return {"ok": True, "found": found}

    app.include_router(router)
