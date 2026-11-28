from __future__ import annotations

import os
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent.command_runtime import CommandRuntime
from agent_bridge.routes.approvals import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.skipif(os.name != "nt", reason="Windows recovery API")
def test_recovery_api_ack_query_retry_and_versioned_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = CommandRuntime(None, codex_home=tmp_path / "home", execution_root=tmp_path / "exec")
    entered, release = threading.Event(), threading.Event()

    def repair(**kwargs: Any) -> None:
        entered.set()
        assert release.wait(3)
        kwargs["check_cancelled"]()

    monkeypatch.setattr(runtime, "_setup_windows_sandbox", repair)
    app = FastAPI()
    register_routes(app, cast(Any, SimpleNamespace(command_runtime=runtime)))
    path = "/v1/command-runtime/windows-sandbox/recoveries"
    try:
        with TestClient(app) as client:
            assert client.post(path, json={"request_key": ""}).status_code == 422
            response = client.post(path, json={"request_key": "lost-http"})
            assert response.status_code == 202
            assert entered.wait(2)
            identity = response.json()["recovery_id"]
            assert (
                client.post(path, json={"request_key": "lost-http"}).json()["recovery_id"]
                == identity
            )
            snapshot = client.get(f"{path}/{identity}").json()
            assert snapshot["result"] is None
            assert client.get(f"{path}/unknown").status_code == 404
            assert (
                client.post(f"{path}/{identity}/cancel", json={"expected_version": 1}).status_code
                == 409
            )
            cancelled = client.post(
                f"{path}/{identity}/cancel", json={"expected_version": snapshot["version"]}
            )
            assert cancelled.status_code == 200 and cancelled.json()["phase"] == "stopping"
            release.set()
            with runtime._recovery._condition:
                assert runtime._recovery._condition.wait_for(
                    lambda: runtime._recovery._flight is None, timeout=2
                )
            assert client.get(f"{path}/{identity}").json()["result"] == "cancelled"
    finally:
        release.set()
        runtime.close()
