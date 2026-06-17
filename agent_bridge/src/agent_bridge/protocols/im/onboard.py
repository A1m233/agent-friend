"""IM 扫码 onboard 异步 task 注册表(022 起)。

设计要点(详见 design.md §3.8):

- HTTP ``POST /v1/im/onboard/start`` 启动一次 onboard,返回 ``task_id`` + 状态 PENDING
- 内部 async task 调 ``qqbot_agent_sdk.start_onboard(on_qr_ready=callback)``
- ``on_qr_ready`` callback 拿到 QR URL 时,task 状态变 QR_READY + 填 qr_url
- ``start_onboard`` 返回时(扫码成功)拿到 OnboardResult,task 状态变 SUCCESS +
  填 bind_id + 调 :meth:`IMRuntime.register_after_onboard`(落盘凭据 + 立即启动 provider)
- 异常 → task 状态变 FAILED + 填 error
- 前端通过 ``GET /v1/im/onboard/{task_id}`` 轮询拿状态

详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §3.8。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from .credentials import ImCredential
from .runtime import IMRuntime, _mask

__all__ = ["OnboardSessionRegistry", "OnboardStatus", "OnboardTaskState"]

logger = logging.getLogger(__name__)


class OnboardStatus(StrEnum):
    """Onboard 任务状态机。

    流转: PENDING(初始) → QR_READY(拿到 QR URL,等用户扫码) → SUCCESS / FAILED
    """

    PENDING = "pending"
    QR_READY = "qr_ready"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class OnboardTaskState:
    """单次 onboard task 的状态。mutable;后端 async task 持续更新这个对象,
    前端轮询 ``GET /v1/im/onboard/{task_id}`` 看最新状态。"""

    task_id: str
    im_type: str
    status: OnboardStatus = OnboardStatus.PENDING
    qr_url: str | None = None
    bind_id_masked: str | None = None  # SUCCESS 后填(脱敏后)
    error: str | None = None
    _task: asyncio.Task[None] | None = field(default=None, repr=False, compare=False)


# ``start_onboard`` 默认从 qqbot_agent_sdk import;测试可注入替代函数。
_StartOnboardFunc = "Callable[..., Awaitable[Any]]"


class OnboardSessionRegistry:
    """活跃 onboard 流程的注册表。

    Args:
        im_runtime: onboard 成功时调用 :meth:`IMRuntime.register_after_onboard`
            的引用。
    """

    SUPPORTED_TYPES = frozenset({"qq"})

    def __init__(self, im_runtime: IMRuntime) -> None:
        self._runtime = im_runtime
        self._tasks: dict[str, OnboardTaskState] = {}

    async def start(self, im_type: str) -> str:
        """启动一次 onboard;返回 ``task_id``,前端用它轮询状态。

        Raises:
            ValueError: ``im_type`` 不在 :attr:`SUPPORTED_TYPES` 中。
        """
        if im_type not in self.SUPPORTED_TYPES:
            raise ValueError(f"unsupported IM type for onboard: {im_type}")

        task_id = uuid.uuid4().hex
        state = OnboardTaskState(task_id=task_id, im_type=im_type)
        self._tasks[task_id] = state

        # 把 onboard 流程放后台 task,立刻返回 task_id 给前端
        state._task = asyncio.create_task(self._run_qq_onboard(state))
        return task_id

    def get(self, task_id: str) -> OnboardTaskState | None:
        return self._tasks.get(task_id)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _run_qq_onboard(self, state: OnboardTaskState) -> None:
        # 延迟 import,便于测试 monkeypatch
        from qqbot_agent_sdk.onboard import start_onboard

        def on_qr(url: str) -> None:
            state.qr_url = url
            state.status = OnboardStatus.QR_READY
            logger.info("QQ onboard QR ready (task_id=%s)", state.task_id)

        try:
            result = await start_onboard(on_qr_ready=on_qr)
            cred = ImCredential(
                im_type="qq",
                bind_id=result.user_openid,
                app_id=result.app_id,
                client_secret=result.client_secret,
                user_openid=result.user_openid,
            )
            self._runtime.register_after_onboard(cred)
            state.bind_id_masked = _mask(cred.bind_id)
            state.status = OnboardStatus.SUCCESS
            logger.info(
                "QQ onboard SUCCESS (task_id=%s, bind=%s)",
                state.task_id,
                state.bind_id_masked,
            )
        except Exception as e:
            logger.exception("QQ onboard FAILED (task_id=%s)", state.task_id)
            state.error = f"{type(e).__name__}: {e}"
            state.status = OnboardStatus.FAILED
