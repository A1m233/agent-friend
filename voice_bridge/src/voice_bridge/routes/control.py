"""控制平面 HTTP 路由：拨打 / 查询 / 挂断通话。

Endpoint 列表：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| ``POST`` | ``/voice/calls`` | 拨打通话 |
| ``GET``  | ``/voice/calls/{call_id}`` | 查通话状态 |
| ``POST`` | ``/voice/calls/{call_id}/stop`` | 挂断（幂等） |

详见 docs/requirements/007-voice-call/design.md §4.3。
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ..assembly import VoiceBridgeRuntime
from ..calls import CallBinding
from ..errors import UnknownCallError, VoiceBridgeError
from ..rtc import build_scenes
from ..rtc.token import RoomTokenSigner

logger = logging.getLogger(__name__)


class StartCallBody(BaseModel):
    """``POST /voice/calls`` 请求 body。"""

    session_id: str | None = Field(None, description="续上已有 session；不传则在 agent_bridge 新建")
    persona: str | None = Field(None, description="新建 session 时使用，不传走 agent_bridge 默认")
    model: str | None = Field(None, description="新建 session 时使用，不传走 agent_bridge 默认")
    welcome_message: str | None = Field(
        None, description="覆盖默认欢迎语；不传走 settings.welcome_message"
    )
    defer_start: bool = Field(
        False,
        description="仅 smoke 调试用：先返回 RTC 凭证，等浏览器入房开麦后再启动 AIGC",
    )


class StartCallResponse(BaseModel):
    """``POST /voice/calls`` 响应。"""

    call_id: str
    session_id: str
    state: str
    rtc_app_id: str
    room_id: str
    user_id: str
    token: str


class CallStateResponse(BaseModel):
    """``GET /voice/calls/{call_id}`` 响应。"""

    call_id: str
    session_id: str
    state: str
    started_at: str


class StopCallResponse(BaseModel):
    """``POST /voice/calls/{call_id}/stop`` 响应。"""

    call_id: str
    state: str


class StartAgentResponse(BaseModel):
    """``POST /voice/calls/{call_id}/start-agent`` 响应。"""

    call_id: str
    state: str


def register_control_routes(app: FastAPI, runtime: VoiceBridgeRuntime) -> None:
    """把控制平面路由挂到 FastAPI 实例。"""
    router = APIRouter(prefix="/voice", tags=["voice-control"])

    async def start_voice_chat_for_binding(
        binding: CallBinding,
        *,
        welcome_message: str | None,
    ) -> None:
        """按已登记的 call binding 拉起火山 RTC AIGC 任务。"""
        scenes = build_scenes(
            settings=runtime.settings,
            call_id=binding.call_id,
            room_id=binding.room_id,
            bot_user_id=binding.bot_user_id,
            target_user_id=binding.target_user_id,
            welcome_message=welcome_message,
        )
        await runtime.rtc_client.start_voice_chat(scenes)
        runtime.call_registry.update_state(binding.call_id, "active")

    @router.post("/calls", response_model=StartCallResponse)
    async def start_call(body: StartCallBody) -> StartCallResponse:
        """拨打通话：创建/绑 session → 调火山 ``StartVoiceChat`` → 返回 RTC 凭证。"""
        settings = runtime.settings
        try:
            if body.session_id is None:
                created = await runtime.agent_bridge.create_session(
                    channel="voice",
                    persona=body.persona,
                    model=body.model,
                )
                session_id = created.session_id
            else:
                await runtime.agent_bridge.switch_channel(body.session_id, "voice")
                session_id = body.session_id

            call_id = str(uuid4())
            room_id = f"room-{uuid4().hex[:12]}"
            bot_user_id = f"bot-{uuid4().hex[:8]}"
            target_user_id = f"user-{uuid4().hex[:8]}"

            now_ts = int(time.time())
            signer = RoomTokenSigner(
                app_id=settings.volc_rtc_app_id,
                app_key=settings.volc_rtc_app_key,
                room_id=room_id,
                user_id=target_user_id,
                issued_at=now_ts,
                nonce=uuid4().int & 0xFFFFFFFF,
                expire_at=now_ts + 24 * 3600,
            )
            signer.add_publish_privilege(now_ts + 24 * 3600)
            signer.add_subscribe_privilege(now_ts + 24 * 3600)
            token = signer.serialize()

            binding = CallBinding(
                call_id=call_id,
                session_id=session_id,
                state="pending",
                started_at=runtime.call_registry.now(),
                room_id=room_id,
                bot_user_id=bot_user_id,
                target_user_id=target_user_id,
            )
            runtime.call_registry.bind(binding)
            if not body.defer_start:
                await start_voice_chat_for_binding(binding, welcome_message=body.welcome_message)
                binding = runtime.call_registry.lookup(call_id) or binding

            return StartCallResponse(
                call_id=call_id,
                session_id=session_id,
                state=binding.state,
                rtc_app_id=settings.volc_rtc_app_id,
                room_id=room_id,
                user_id=target_user_id,
                token=token,
            )
        except VoiceBridgeError as e:
            logger.warning("拨打通话失败: %s", e)
            raise HTTPException(
                status_code=e.info.http_status,
                detail={"error": e.info.error_code, "message": e.info.user_message},
            ) from e

    @router.post("/calls/{call_id}/start-agent", response_model=StartAgentResponse)
    async def start_agent(call_id: str) -> StartAgentResponse:
        """延迟拉起 AIGC：用于 smoke 验证“用户入房开麦后再 StartVoiceChat”的时序。"""
        binding = runtime.call_registry.lookup(call_id)
        if binding is None:
            err = UnknownCallError()
            raise HTTPException(
                status_code=err.info.http_status,
                detail={"error": err.info.error_code, "message": err.info.user_message},
            )
        if binding.state in ("active", "stopped"):
            return StartAgentResponse(call_id=call_id, state=binding.state)

        try:
            await start_voice_chat_for_binding(binding, welcome_message=None)
        except VoiceBridgeError as e:
            runtime.call_registry.update_state(call_id, "error")
            logger.warning("延迟启动 AIGC 失败: %s", e)
            raise HTTPException(
                status_code=e.info.http_status,
                detail={"error": e.info.error_code, "message": e.info.user_message},
            ) from e

        return StartAgentResponse(call_id=call_id, state="active")

    @router.post("/callbacks/state/{call_id}")
    async def receive_state_callback(call_id: str, request: Request) -> dict[str, bool]:
        """火山 RTC 会话状态回调诊断入口。"""
        payload: Any
        try:
            payload = await request.json()
        except ValueError:
            payload = (await request.body()).decode("utf-8", errors="replace")
        logger.info("voice state callback call_id=%s payload=%s", call_id, payload)
        return {"ok": True}

    @router.post("/callbacks/subtitle/{call_id}")
    async def receive_subtitle_callback(call_id: str, request: Request) -> dict[str, bool]:
        """火山 RTC 字幕/ASR 回调诊断入口。"""
        payload: Any
        try:
            payload = await request.json()
        except ValueError:
            payload = (await request.body()).decode("utf-8", errors="replace")
        logger.info("voice subtitle callback call_id=%s payload=%s", call_id, payload)
        return {"ok": True}

    @router.get("/calls/{call_id}", response_model=CallStateResponse)
    async def get_call(call_id: str) -> CallStateResponse:
        """查通话当前状态。"""
        binding = runtime.call_registry.lookup(call_id)
        if binding is None:
            err = UnknownCallError()
            raise HTTPException(
                status_code=err.info.http_status,
                detail={"error": err.info.error_code, "message": err.info.user_message},
            )
        return CallStateResponse(
            call_id=binding.call_id,
            session_id=binding.session_id,
            state=binding.state,
            started_at=binding.started_at.isoformat(),
        )

    @router.post("/calls/{call_id}/stop", response_model=StopCallResponse)
    async def stop_call(call_id: str) -> StopCallResponse:
        """挂断通话（幂等）。"""
        binding = runtime.call_registry.lookup(call_id)
        if binding is None:
            return StopCallResponse(call_id=call_id, state="stopped")
        if binding.state == "stopped":
            return StopCallResponse(call_id=call_id, state="stopped")

        if binding.state == "active":
            try:
                await runtime.rtc_client.stop_voice_chat(
                    app_id=runtime.settings.volc_rtc_app_id,
                    room_id=binding.room_id,
                    task_id=binding.call_id,
                )
            except VoiceBridgeError as e:
                logger.warning("StopVoiceChat 失败（吞掉以保证幂等）: %s", e)

        try:
            await runtime.agent_bridge.switch_channel(binding.session_id, "text")
        except VoiceBridgeError as e:
            logger.warning("挂断时切回 text 通道失败（不阻断）: %s", e)

        runtime.call_registry.update_state(call_id, "stopped")
        return StopCallResponse(call_id=call_id, state="stopped")

    app.include_router(router)
