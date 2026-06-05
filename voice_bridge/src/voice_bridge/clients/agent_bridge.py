"""调 agent_bridge HTTP 端点的薄客户端。

voice_bridge **不**直接 import agent / agent_bridge 内部对象，所有跟 session
相关的事都走这层 HTTP——保持模块边界清晰，未来 agent_bridge 单独部署也能切换。

详见 docs/requirements/007-voice-call/design.md §3.1（依赖方向）。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..errors import AgentBridgeUnreachableError, SessionBindFailedError


@dataclass(frozen=True)
class CreateSessionResult:
    """``POST /v1/sessions`` 成功响应。"""

    session_id: str
    channel: str


class AgentBridgeClient:
    """调 agent_bridge HTTP 的薄封装。

    所有方法都是 async，错误统一转 :class:`AgentBridgeUnreachableError` /
    :class:`SessionBindFailedError`。
    """

    def __init__(
        self,
        base_url: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._timeout = timeout

    async def create_session(
        self,
        *,
        channel: str = "voice",
        persona: str | None = None,
        model: str | None = None,
    ) -> CreateSessionResult:
        """调 agent_bridge ``POST /v1/sessions`` 显式创建 session。

        Args:
            channel: ``"voice"`` 或 ``"text"``。
            persona: 可选；不传走 agent_bridge 自身默认。
            model: 可选；不传走 agent_bridge 自身默认。

        Raises:
            AgentBridgeUnreachableError: 网络层错误。
            SessionBindFailedError: agent_bridge 返回 4xx/5xx。
        """
        body: dict[str, str] = {"channel": channel}
        if persona is not None:
            body["persona"] = persona
        if model is not None:
            body["model"] = model

        data = await self._post_json("/v1/sessions", body)
        return CreateSessionResult(
            session_id=str(data["session_id"]),
            channel=str(data.get("channel", channel)),
        )

    async def switch_channel(self, session_id: str, channel: str) -> None:
        """调 agent_bridge ``POST /v1/sessions/{id}/channel`` 切换 channel。"""
        await self._post_json(
            f"/v1/sessions/{session_id}/channel",
            {"channel": channel},
        )

    async def _post_json(self, path: str, body: dict[str, str]) -> dict[str, object]:
        url = self._base_url + path
        try:
            client = self._http_client or httpx.AsyncClient(timeout=self._timeout)
            close_after = self._http_client is None
            try:
                resp = await client.post(url, json=body)
            finally:
                if close_after:
                    await client.aclose()
        except httpx.RequestError as e:
            raise AgentBridgeUnreachableError(detail=str(e)) from e

        if resp.is_success:
            try:
                payload = resp.json()
                if isinstance(payload, dict):
                    return payload
            except ValueError:
                pass
            return {}
        raise SessionBindFailedError(
            detail=f"agent_bridge {path} 返回 {resp.status_code}: {resp.text[:200]}"
        )
