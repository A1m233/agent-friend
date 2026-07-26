from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def _load_module() -> Any:
    path = Path(__file__).with_name("run.py")
    spec = importlib.util.spec_from_file_location("im_add_binding_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_uses_reject_policy_and_binds_new_provider(monkeypatch: Any) -> None:
    module = _load_module()
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    states = iter(
        [
            {
                "status": "qr_ready",
                "qr_url": "https://q.qq.com/safe",
                "bind_id": None,
            },
            {
                "status": "success",
                "qr_url": "https://q.qq.com/safe",
                "bind_id": "NEW/OPENID",
                "bind_id_masked": "NEW/...ENID",
            },
        ]
    )

    def fake_request(
        _base_url: str,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, object] | None = None,
    ) -> Any:
        calls.append((method, path, body))
        if path == "/v1/im/providers":
            return []
        if path == "/v1/im/onboard/start":
            return {"task_id": "TASK"}
        if path == "/v1/im/onboard/TASK":
            return next(states)
        if path.endswith("/session-binding"):
            return {"session_id": "SESSION"}
        raise AssertionError(path)

    shown_qr: list[str] = []
    monkeypatch.setattr(module, "request_json", fake_request)
    monkeypatch.setattr(module, "print_qr", shown_qr.append)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run.py", "--session-id", "SESSION", "--timeout", "5"],
    )

    assert module.main() == 0
    assert shown_qr == ["https://q.qq.com/safe"]
    assert (
        "POST",
        "/v1/im/onboard/start",
        {"im_type": "qq", "conflict_policy": "reject"},
    ) in calls
    assert (
        "PUT",
        "/v1/im/providers/qq/NEW%2FOPENID/session-binding",
        {"session_id": "SESSION"},
    ) in calls
