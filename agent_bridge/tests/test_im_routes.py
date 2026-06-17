"""IM HTTP routes 单测(M22.6)。

用 FastAPI TestClient 验证 4 个 endpoints 的 status code + response shape;
mock im_runtime / onboard_registry,**不依赖**真实 BridgeRuntime 装配。

覆盖端点:

- ``GET /v1/im/providers``
- ``POST /v1/im/onboard/start``
- ``GET /v1/im/onboard/{task_id}``
- ``DELETE /v1/im/providers/{im_type}/{bind_id}``

以及 503(im_runtime 未装配)/ 400(im_type 不支持) / 404(task_id 不存在)。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from agent_bridge.protocols.im.onboard import (
    OnboardSessionRegistry,
    OnboardStatus,
    OnboardTaskState,
)
from agent_bridge.protocols.im.routes import register_routes
from agent_bridge.protocols.im.runtime import ProviderInfo
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------- helpers ----------


def _make_app(
    im_runtime: object | None,
    onboard_registry: OnboardSessionRegistry | MagicMock,
) -> FastAPI:
    """构造一个最小 FastAPI app,挂上 IM routes,绕开真正的 BridgeRuntime 装配。"""
    app = FastAPI()
    runtime = MagicMock()
    runtime.im_runtime = im_runtime
    register_routes(app, runtime, onboard_registry)
    return app


# ============================================================================
# GET /v1/im/providers
# ============================================================================


def test_list_providers_returns_empty_when_no_bindings() -> None:
    im_runtime = MagicMock()
    im_runtime.list_status.return_value = []
    onboard = MagicMock()
    client = TestClient(_make_app(im_runtime, onboard))

    resp = client.get("/v1/im/providers")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_providers_returns_bindings() -> None:
    im_runtime = MagicMock()
    im_runtime.list_status.return_value = [
        ProviderInfo(
            im_type="qq",
            bind_id="OPENID-USER-FULL-EE26",
            bind_id_masked="OPEN...EE26",
            status="active",
        ),
    ]
    onboard = MagicMock()
    client = TestClient(_make_app(im_runtime, onboard))

    resp = client.get("/v1/im/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0] == {
        "im_type": "qq",
        "bind_id": "OPENID-USER-FULL-EE26",
        "bind_id_masked": "OPEN...EE26",
        "status": "active",
    }


def test_list_providers_returns_503_when_im_runtime_missing() -> None:
    onboard = MagicMock()
    client = TestClient(_make_app(im_runtime=None, onboard_registry=onboard))

    resp = client.get("/v1/im/providers")
    assert resp.status_code == 503
    assert "im_runtime 未装配" in resp.json()["detail"]


# ============================================================================
# POST /v1/im/onboard/start
# ============================================================================


def test_onboard_start_returns_task_id() -> None:
    im_runtime = MagicMock()
    onboard = MagicMock()
    onboard.start = AsyncMock(return_value="TASK-ABC")
    client = TestClient(_make_app(im_runtime, onboard))

    resp = client.post("/v1/im/onboard/start", json={"im_type": "qq"})
    assert resp.status_code == 200
    assert resp.json() == {"task_id": "TASK-ABC"}
    onboard.start.assert_awaited_once_with("qq")


def test_onboard_start_rejects_invalid_im_type() -> None:
    """``im_type`` 不在 Literal["qq"] 内 → 422(pydantic 校验)。"""
    im_runtime = MagicMock()
    onboard = MagicMock()
    client = TestClient(_make_app(im_runtime, onboard))

    resp = client.post("/v1/im/onboard/start", json={"im_type": "feishu"})
    assert resp.status_code == 422


def test_onboard_start_503_when_im_runtime_missing() -> None:
    onboard = MagicMock()
    onboard.start = AsyncMock(return_value="TASK-ABC")
    client = TestClient(_make_app(im_runtime=None, onboard_registry=onboard))

    resp = client.post("/v1/im/onboard/start", json={"im_type": "qq"})
    assert resp.status_code == 503


def test_onboard_start_400_when_registry_raises_value_error() -> None:
    """OnboardSessionRegistry.start 抛 ValueError(未来加新 im_type 时可能) → 400。"""
    im_runtime = MagicMock()
    onboard = MagicMock()
    onboard.start = AsyncMock(side_effect=ValueError("unsupported IM type: napcat"))
    client = TestClient(_make_app(im_runtime, onboard))

    # 注意:本期 pydantic Literal 已经在更早阶段挡了非 qq;这里用 monkeypatch 跳过
    # 校验、直接打到 registry.start 验证错误处理路径
    # 实际本期不可达,这里只是契约保护
    # 用 qq 通过 pydantic,然后 registry 模拟抛错
    resp = client.post("/v1/im/onboard/start", json={"im_type": "qq"})
    assert resp.status_code == 400
    assert "unsupported IM type" in resp.json()["detail"]


# ============================================================================
# GET /v1/im/onboard/{task_id}
# ============================================================================


def test_onboard_status_returns_state() -> None:
    im_runtime = MagicMock()
    onboard = MagicMock()
    onboard.get.return_value = OnboardTaskState(
        task_id="TASK-ABC",
        im_type="qq",
        status=OnboardStatus.QR_READY,
        qr_url="https://q.qq.com/test",
    )
    client = TestClient(_make_app(im_runtime, onboard))

    resp = client.get("/v1/im/onboard/TASK-ABC")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "TASK-ABC"
    assert body["im_type"] == "qq"
    assert body["status"] == "qr_ready"
    assert body["qr_url"] == "https://q.qq.com/test"
    assert body["bind_id_masked"] is None
    assert body["error"] is None


def test_onboard_status_success_state() -> None:
    im_runtime = MagicMock()
    onboard = MagicMock()
    onboard.get.return_value = OnboardTaskState(
        task_id="T",
        im_type="qq",
        status=OnboardStatus.SUCCESS,
        qr_url="https://q.qq.com/x",
        bind_id_masked="ABCD...EFGH",
    )
    client = TestClient(_make_app(im_runtime, onboard))

    resp = client.get("/v1/im/onboard/T")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["bind_id_masked"] == "ABCD...EFGH"


def test_onboard_status_failed_state() -> None:
    im_runtime = MagicMock()
    onboard = MagicMock()
    onboard.get.return_value = OnboardTaskState(
        task_id="T",
        im_type="qq",
        status=OnboardStatus.FAILED,
        error="RuntimeError: network down",
    )
    client = TestClient(_make_app(im_runtime, onboard))

    resp = client.get("/v1/im/onboard/T")
    body = resp.json()
    assert body["status"] == "failed"
    assert "network down" in body["error"]


def test_onboard_status_404_when_task_unknown() -> None:
    im_runtime = MagicMock()
    onboard = MagicMock()
    onboard.get.return_value = None
    client = TestClient(_make_app(im_runtime, onboard))

    resp = client.get("/v1/im/onboard/NEVER-EXISTED")
    assert resp.status_code == 404
    assert "unknown task_id" in resp.json()["detail"]


# ============================================================================
# DELETE /v1/im/providers/{im_type}/{bind_id}
# ============================================================================


def test_unbind_provider_success() -> None:
    im_runtime = MagicMock()
    im_runtime.unbind = AsyncMock(return_value=True)
    onboard = MagicMock()
    client = TestClient(_make_app(im_runtime, onboard))

    resp = client.delete("/v1/im/providers/qq/OPENID-X")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "found": True}
    im_runtime.unbind.assert_awaited_once_with("qq", "OPENID-X")


def test_unbind_provider_idempotent_when_not_found() -> None:
    """解绑一个不存在的 provider → ok=True, found=False(幂等)。"""
    im_runtime = MagicMock()
    im_runtime.unbind = AsyncMock(return_value=False)
    onboard = MagicMock()
    client = TestClient(_make_app(im_runtime, onboard))

    resp = client.delete("/v1/im/providers/qq/NEVER-BOUND")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "found": False}


def test_unbind_provider_503_when_im_runtime_missing() -> None:
    onboard = MagicMock()
    client = TestClient(_make_app(im_runtime=None, onboard_registry=onboard))

    resp = client.delete("/v1/im/providers/qq/X")
    assert resp.status_code == 503
