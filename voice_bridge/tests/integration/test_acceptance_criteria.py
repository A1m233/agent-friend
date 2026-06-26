"""007 voice-call 集成测试：mock 火山 OpenAPI + mock agent_bridge，覆盖 AC-1 ~ AC-7。

AC-8 / AC-9 / AC-10 由其他途径覆盖（既有测试不退化 / 文件系统断言 / 脚本就位检查）。

详见 docs/requirements/007-voice-call/requirement.md §6。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from voice_bridge.app import create_app_with_runtime
from voice_bridge.assembly import VoiceBridgeRuntime
from voice_bridge.calls import CallRegistry
from voice_bridge.clients import AgentBridgeClient
from voice_bridge.rtc import VolcRtcClient
from voice_bridge.settings import VoiceBridgeSettings

# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------


@pytest.fixture
def settings() -> VoiceBridgeSettings:
    return VoiceBridgeSettings(
        host="127.0.0.1",
        port=18900,
        public_url="https://test.example.com",
        agent_bridge_url="http://test-agent-bridge",
        volc_access_key="AKLT-test",
        volc_secret_key="test-secret",
        volc_rtc_app_id="rtc-app-id",
        volc_rtc_app_key="rtc-app-key",
        volc_speech_app_id="speech-app-id",
        volc_speech_access_token="speech-token",
        voice_type="zh_female_test_bigtts",
        welcome_message="嗨呀",
    )


@pytest.fixture
def runtime(settings: VoiceBridgeSettings) -> VoiceBridgeRuntime:
    return VoiceBridgeRuntime(
        settings=settings,
        rtc_client=VolcRtcClient(
            access_key=settings.volc_access_key,
            secret_key=settings.volc_secret_key,
        ),
        call_registry=CallRegistry(),
        agent_bridge=AgentBridgeClient(settings.agent_bridge_url),
    )


@pytest.fixture
def client(runtime: VoiceBridgeRuntime) -> TestClient:
    app = create_app_with_runtime(runtime)
    return TestClient(app)


# ----------------------------------------------------------------------------
# Mock helpers
# ----------------------------------------------------------------------------


def _mock_volc_start_voice_chat_success(respx_mock: respx.MockRouter) -> Any:
    return respx_mock.post(
        "https://rtc.volcengineapi.com/?Action=StartVoiceChat&Version=2024-12-01"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "ResponseMetadata": {"Action": "StartVoiceChat"},
                "Result": "ok",
            },
        )
    )


def _mock_volc_stop_voice_chat_success(respx_mock: respx.MockRouter) -> Any:
    return respx_mock.post(
        "https://rtc.volcengineapi.com/?Action=StopVoiceChat&Version=2024-12-01"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"ResponseMetadata": {"Action": "StopVoiceChat"}, "Result": "ok"},
        )
    )


def _mock_agent_bridge_create_session(
    respx_mock: respx.MockRouter, session_id: str = "sess-test-1"
) -> Any:
    return respx_mock.post("http://test-agent-bridge/v1/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "session_id": session_id,
                "persona": "default",
                "model": "deepseek/deepseek-chat",
                "channel": "voice",
            },
        )
    )


def _mock_agent_bridge_switch_channel(respx_mock: respx.MockRouter, session_id: str) -> Any:
    return respx_mock.post(f"http://test-agent-bridge/v1/sessions/{session_id}/channel").mock(
        return_value=httpx.Response(
            200,
            json={"session_id": session_id, "channel": "voice"},
        )
    )


def _mock_agent_bridge_get_session(
    respx_mock: respx.MockRouter,
    session_id: str,
    *,
    event_types: list[str] | None = None,
) -> Any:
    events = [{"type": event_type} for event_type in (event_types or [])]
    return respx_mock.get(f"http://test-agent-bridge/v1/sessions/{session_id}").mock(
        return_value=httpx.Response(
            200,
            json={"session_id": session_id, "events": events},
        )
    )


def _mock_agent_bridge_delete_session(respx_mock: respx.MockRouter, session_id: str) -> Any:
    return respx_mock.delete(f"http://test-agent-bridge/v1/sessions/{session_id}").mock(
        return_value=httpx.Response(
            200,
            json={"session_id": session_id, "deleted": True},
        )
    )


# ----------------------------------------------------------------------------
# AC-1: 控制平面拨打通话
# ----------------------------------------------------------------------------


class TestAC1CallStart:
    @respx.mock
    def test_call_start_returns_call_id_and_rtc_credentials(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        _mock_agent_bridge_create_session(respx_mock)
        _mock_volc_start_voice_chat_success(respx_mock)

        resp = client.post("/voice/calls", json={})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["state"] == "active"
        assert data["call_id"]
        assert data["trace_id"] == data["call_id"]
        assert data["session_id"] == "sess-test-1"
        assert data["rtc_app_id"] == "rtc-app-id"
        assert data["room_id"]
        assert data["user_id"]
        assert data["token"]

    @respx.mock
    def test_call_start_signs_volc_request_with_v4(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        _mock_agent_bridge_create_session(respx_mock)
        volc_route = _mock_volc_start_voice_chat_success(respx_mock)

        client.post("/voice/calls", json={})
        assert volc_route.called
        request = volc_route.calls[0].request
        # V4 必填头
        assert "Authorization" in request.headers
        assert request.headers["Authorization"].startswith("HMAC-SHA256 ")
        assert "X-Date" in request.headers
        assert "X-Content-Sha256" in request.headers

    @respx.mock
    def test_call_start_passes_session_id_when_provided(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        switch_route = _mock_agent_bridge_switch_channel(respx_mock, "user-supplied-session")
        _mock_volc_start_voice_chat_success(respx_mock)

        resp = client.post(
            "/voice/calls",
            json={"session_id": "user-supplied-session"},
        )
        assert resp.status_code == 200, resp.text
        # voice_bridge 调 switch_channel 而不是 create_session
        assert switch_route.called


# ----------------------------------------------------------------------------
# AC-2: 控制平面挂断通话
# ----------------------------------------------------------------------------


class TestAC2CallStop:
    @respx.mock
    def test_call_stop_calls_volc_with_same_task_id(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        _mock_agent_bridge_create_session(respx_mock)
        _mock_volc_start_voice_chat_success(respx_mock)
        stop_route = _mock_volc_stop_voice_chat_success(respx_mock)
        respx_mock.post("http://test-agent-bridge/v1/sessions/sess-test-1/channel").mock(
            return_value=httpx.Response(200, json={})
        )
        _mock_agent_bridge_get_session(respx_mock, "sess-test-1")
        _mock_agent_bridge_delete_session(respx_mock, "sess-test-1")

        start_resp = client.post("/voice/calls", json={}).json()
        call_id = start_resp["call_id"]

        stop_resp = client.post(f"/voice/calls/{call_id}/stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["state"] == "stopped"

        # 校验 voice_bridge 发出的 StopVoiceChat 用同一个 TaskId（= call_id）
        assert stop_route.called
        body = json.loads(stop_route.calls[0].request.content)
        assert body["TaskId"] == call_id

    @respx.mock
    def test_stop_is_idempotent(self, client: TestClient, respx_mock: respx.MockRouter) -> None:
        _mock_agent_bridge_create_session(respx_mock)
        _mock_volc_start_voice_chat_success(respx_mock)
        _mock_volc_stop_voice_chat_success(respx_mock)
        respx_mock.post("http://test-agent-bridge/v1/sessions/sess-test-1/channel").mock(
            return_value=httpx.Response(200, json={})
        )
        _mock_agent_bridge_get_session(respx_mock, "sess-test-1")
        _mock_agent_bridge_delete_session(respx_mock, "sess-test-1")

        call_id = client.post("/voice/calls", json={}).json()["call_id"]
        a = client.post(f"/voice/calls/{call_id}/stop").json()
        b = client.post(f"/voice/calls/{call_id}/stop").json()
        assert a["state"] == b["state"] == "stopped"

    def test_stop_unknown_call_is_idempotent(self, client: TestClient) -> None:
        resp = client.post("/voice/calls/nonexistent/stop")
        assert resp.status_code == 200
        assert resp.json()["state"] == "stopped"


# ----------------------------------------------------------------------------
# AC-3: 通话状态机
# ----------------------------------------------------------------------------


class TestAC3StateMachine:
    @respx.mock
    def test_active_after_start(self, client: TestClient, respx_mock: respx.MockRouter) -> None:
        _mock_agent_bridge_create_session(respx_mock)
        _mock_volc_start_voice_chat_success(respx_mock)

        call_id = client.post("/voice/calls", json={"trace_id": "trace-llm-1"}).json()["call_id"]
        get_resp = client.get(f"/voice/calls/{call_id}").json()
        assert get_resp["state"] == "active"

    @respx.mock
    def test_stopped_after_stop(self, client: TestClient, respx_mock: respx.MockRouter) -> None:
        _mock_agent_bridge_create_session(respx_mock)
        _mock_volc_start_voice_chat_success(respx_mock)
        _mock_volc_stop_voice_chat_success(respx_mock)
        respx_mock.post("http://test-agent-bridge/v1/sessions/sess-test-1/channel").mock(
            return_value=httpx.Response(200, json={})
        )
        _mock_agent_bridge_get_session(respx_mock, "sess-test-1")
        _mock_agent_bridge_delete_session(respx_mock, "sess-test-1")

        call_id = client.post("/voice/calls", json={"trace_id": "trace-llm-1"}).json()["call_id"]
        client.post(f"/voice/calls/{call_id}/stop")
        get_resp = client.get(f"/voice/calls/{call_id}").json()
        assert get_resp["state"] == "stopped"

    def test_get_unknown_call_returns_404(self, client: TestClient) -> None:
        resp = client.get("/voice/calls/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["error"] == "call_not_found"


# ----------------------------------------------------------------------------
# AC-4: LLM 入站代理 session 注入
# ----------------------------------------------------------------------------


class TestAC4LLMProxySessionInject:
    @respx.mock
    def test_proxy_injects_session_header_and_streams(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        # 先拨打一通通话拿 call_id
        _mock_agent_bridge_create_session(respx_mock, session_id="sess-llm-1")
        _mock_volc_start_voice_chat_success(respx_mock)

        call_id = client.post("/voice/calls", json={"trace_id": "trace-llm-1"}).json()["call_id"]

        # mock agent_bridge 的 OpenAI 端点
        sse_chunks = [
            'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
            'data: {"choices":[{"delta":{"content":"嗨"}}]}\n\n',
            "data: [DONE]\n\n",
        ]

        agent_route = respx_mock.post("http://test-agent-bridge/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content="".join(sse_chunks).encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        )

        # 模拟火山 RTC 调过来
        body = {
            "model": "any",
            "messages": [{"role": "user", "content": "你好"}],
            "stream": True,
        }
        resp = client.post(
            f"/voice/llm/{call_id}/v1/chat/completions",
            json=body,
        )
        assert resp.status_code == 200

        # 校验 (a) header 注入
        assert agent_route.called
        upstream_req = agent_route.calls[0].request
        assert upstream_req.headers["x-agent-friend-session-id"] == "sess-llm-1"
        assert upstream_req.headers["x-agent-friend-voice-trace-id"] == "trace-llm-1"
        assert upstream_req.headers["x-agent-friend-voice-call-id"] == call_id
        assert upstream_req.headers["x-agent-friend-voice-round-seq"] == "1"

        # 校验 (b) body 完整透传
        forwarded_body = json.loads(upstream_req.content)
        assert forwarded_body == body

        # 校验 (c) SSE 内容原样回传
        full_text = resp.text
        assert "嗨" in full_text
        assert "[DONE]" in full_text

    def test_proxy_unknown_call_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/voice/llm/nonexistent/v1/chat/completions",
            json={"model": "x", "messages": []},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "call_not_found"


# ----------------------------------------------------------------------------
# AC-5 / AC-6: channel 字段贯穿 + channel 互切
#
# 这两个 AC 涉及 voice_bridge 和 agent_bridge 配合，最直接的覆盖方式是端到端在
# 同一进程内同时启 agent_bridge + voice_bridge。本期作为 voice_bridge 集成测试，
# 我们覆盖 voice_bridge 一侧的契约：
#   - 拨打不带 session_id 时，voice_bridge 调 agent_bridge create_session + channel=voice
#   - 拨打带 session_id 时，voice_bridge 调 agent_bridge switch_channel(voice)
#   - 挂断时，voice_bridge 调 agent_bridge switch_channel(text)
#
# agent_bridge 真的把 channel 落到 session 文件这一段在 agent core 单元测试
# (agent/tests/test_channel.py) 里覆盖。
# ----------------------------------------------------------------------------


class TestAC5ChannelOnNewSession:
    @respx.mock
    def test_create_session_called_with_channel_voice(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        create_route = _mock_agent_bridge_create_session(respx_mock)
        _mock_volc_start_voice_chat_success(respx_mock)

        client.post("/voice/calls", json={})
        assert create_route.called
        body = json.loads(create_route.calls[0].request.content)
        assert body["channel"] == "voice"

    @respx.mock
    def test_persona_and_model_passed_through(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        create_route = _mock_agent_bridge_create_session(respx_mock)
        _mock_volc_start_voice_chat_success(respx_mock)

        client.post(
            "/voice/calls",
            json={"persona": "linus", "model": "deepseek/deepseek-v4-flash"},
        )
        body = json.loads(create_route.calls[0].request.content)
        assert body["persona"] == "linus"
        assert body["model"] == "deepseek/deepseek-v4-flash"


class TestAC6ChannelSwitch:
    @respx.mock
    def test_existing_session_upgrade_calls_switch_channel(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        switch_route = _mock_agent_bridge_switch_channel(respx_mock, "existing-sess-1")
        _mock_volc_start_voice_chat_success(respx_mock)

        client.post("/voice/calls", json={"session_id": "existing-sess-1"})
        assert switch_route.called
        body = json.loads(switch_route.calls[0].request.content)
        assert body["channel"] == "voice"

    @respx.mock
    def test_stop_downgrades_back_to_text(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        _mock_agent_bridge_create_session(respx_mock, session_id="sess-stop-1")
        _mock_volc_start_voice_chat_success(respx_mock)
        _mock_volc_stop_voice_chat_success(respx_mock)
        downgrade_route = respx_mock.post(
            "http://test-agent-bridge/v1/sessions/sess-stop-1/channel"
        ).mock(return_value=httpx.Response(200, json={}))
        _mock_agent_bridge_get_session(respx_mock, "sess-stop-1")
        _mock_agent_bridge_delete_session(respx_mock, "sess-stop-1")

        call_id = client.post("/voice/calls", json={}).json()["call_id"]
        client.post(f"/voice/calls/{call_id}/stop")
        assert downgrade_route.called
        body = json.loads(downgrade_route.calls[0].request.content)
        assert body["channel"] == "text"

    @respx.mock
    def test_stop_deletes_empty_session_created_by_voice_bridge(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        _mock_agent_bridge_create_session(respx_mock, session_id="sess-empty-1")
        _mock_volc_start_voice_chat_success(respx_mock)
        _mock_volc_stop_voice_chat_success(respx_mock)
        respx_mock.post("http://test-agent-bridge/v1/sessions/sess-empty-1/channel").mock(
            return_value=httpx.Response(200, json={})
        )
        _mock_agent_bridge_get_session(respx_mock, "sess-empty-1")
        delete_route = _mock_agent_bridge_delete_session(respx_mock, "sess-empty-1")

        call_id = client.post("/voice/calls", json={}).json()["call_id"]
        resp = client.post(f"/voice/calls/{call_id}/stop")

        assert resp.status_code == 200
        assert delete_route.called

    @respx.mock
    def test_stop_keeps_created_session_after_dialog_messages(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        _mock_agent_bridge_create_session(respx_mock, session_id="sess-dialog-1")
        _mock_volc_start_voice_chat_success(respx_mock)
        _mock_volc_stop_voice_chat_success(respx_mock)
        respx_mock.post("http://test-agent-bridge/v1/sessions/sess-dialog-1/channel").mock(
            return_value=httpx.Response(200, json={})
        )
        _mock_agent_bridge_get_session(
            respx_mock,
            "sess-dialog-1",
            event_types=["session_meta", "user_message"],
        )

        call_id = client.post("/voice/calls", json={}).json()["call_id"]
        resp = client.post(f"/voice/calls/{call_id}/stop")

        assert resp.status_code == 200

    @respx.mock
    def test_stop_does_not_delete_existing_session(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        switch_route = respx_mock.post(
            "http://test-agent-bridge/v1/sessions/existing-sess-2/channel"
        ).mock(return_value=httpx.Response(200, json={}))
        _mock_volc_start_voice_chat_success(respx_mock)
        _mock_volc_stop_voice_chat_success(respx_mock)

        call_id = client.post("/voice/calls", json={"session_id": "existing-sess-2"}).json()[
            "call_id"
        ]
        resp = client.post(f"/voice/calls/{call_id}/stop")

        assert resp.status_code == 200
        assert switch_route.call_count == 2


# ----------------------------------------------------------------------------
# AC-7: 跨进程错误兜底
# ----------------------------------------------------------------------------


class TestAC7ErrorFallback:
    @respx.mock
    def test_volc_rate_limit_returns_503_with_user_message(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        _mock_agent_bridge_create_session(respx_mock)
        respx_mock.post(
            "https://rtc.volcengineapi.com/?Action=StartVoiceChat&Version=2024-12-01"
        ).mock(
            return_value=httpx.Response(
                429,
                json={"ResponseMetadata": {"Error": {"Code": "Throttled"}}},
            )
        )

        resp = client.post("/voice/calls", json={})
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"]["error"] == "volc_rate_limited"
        assert "Throttled" not in body["detail"]["message"]
        assert "繁忙" in body["detail"]["message"]

    @respx.mock
    def test_volc_auth_error_returns_502_with_user_message(
        self, client: TestClient, respx_mock: respx.MockRouter
    ) -> None:
        _mock_agent_bridge_create_session(respx_mock)
        respx_mock.post(
            "https://rtc.volcengineapi.com/?Action=StartVoiceChat&Version=2024-12-01"
        ).mock(
            return_value=httpx.Response(
                403,
                json={"ResponseMetadata": {"Error": {"Code": "InvalidSecret"}}},
            )
        )

        resp = client.post("/voice/calls", json={})
        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["error"] == "volc_auth_failed"
        # 不暴露技术细节
        assert "InvalidSecret" not in body["detail"]["message"]
        assert "AKLT" not in body["detail"]["message"]

    def test_agent_bridge_unreachable_returns_502(self, client: TestClient) -> None:
        # 不 mock agent_bridge → httpx 真去连 test-agent-bridge → 解析失败
        resp = client.post("/voice/calls", json={})
        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["error"] in (
            "agent_bridge_unreachable",
            "session_bind_failed",
        )
