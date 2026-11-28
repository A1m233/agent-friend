"""Timeouts end a waiter, not the request's ownership or reserved capacity."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from agent.command_runtime.exec_server import ExecServerClient, ExecServerError, ExecServerRequest

FIXTURE = Path(__file__).parent / "fixtures/fake_exec_server.py"


@pytest.fixture
def client() -> Iterator[ExecServerClient]:
    client = ExecServerClient(
        [sys.executable, str(FIXTURE)],
        env=os.environ,
        notification=lambda *_: None,
        max_ordinary_requests=1,
    )
    try:
        client.start()
        yield client
    finally:
        client.close()


def timed_out_start(client: ExecServerClient) -> ExecServerRequest:
    handles: list[ExecServerRequest] = []
    with pytest.raises(ExecServerError, match="request_timeout") as failure:
        client.request(
            "process/start",
            {"processId": "proc_late", "argv": ["fake:delayed-rejection"]},
            timeout_seconds=0.02,
            on_submitted=handles.append,
        )
    assert failure.value.request_sent is True
    assert not handles[0].event.is_set()
    return handles[0]


def test_timeout_retains_request_and_reserves_query_and_control(client: ExecServerClient) -> None:
    handle = timed_out_start(client)
    submitted: list[ExecServerRequest] = []
    with pytest.raises(ExecServerError, match="admission_timeout") as failure:
        client.request("fs/getMetadata", {}, timeout_seconds=0.02, on_submitted=submitted.append)
    assert failure.value.request_sent is False
    assert submitted == []
    with pytest.raises(ExecServerError, match="process is starting"):
        client.request("process/read", {"processId": "proc_late"}, timeout_seconds=1)
    assert client.request("process/terminate", {"processId": "proc_late"}, timeout_seconds=1) == {
        "running": False
    }
    # Neither a query error nor terminate acceptance retires the start request.
    assert not handle.event.is_set()
    client.notify("fixture/release-rejections", {})
    assert handle.event.wait(1)
    assert handle.response is not None and handle.response.from_peer
    assert isinstance(handle.response.error, dict)
    assert "orchestrator_helper_launch_canceled" in handle.response.error["message"]
    assert client.request("fs/getMetadata", {}, timeout_seconds=1)["isDirectory"]


def test_disconnect_completes_retained_handle_without_fabricating_peer_rejection(
    client: ExecServerClient,
) -> None:
    handle = timed_out_start(client)
    entered = threading.Event()

    def queued() -> None:
        entered.set()
        client.request("fs/getMetadata", {}, timeout_seconds=2)

    with ThreadPoolExecutor(1) as pool:
        waiter = pool.submit(queued)
        assert entered.wait(1)
        client.close()
        with pytest.raises(ExecServerError, match="disconnected") as failure:
            waiter.result(1)
        assert failure.value.request_sent is False
    assert handle.event.is_set()
    assert handle.response is not None and not handle.response.from_peer
    assert client._pending == {}


def test_duplicate_reply_cannot_replace_recorded_outcome(client: ExecServerClient) -> None:
    handle = timed_out_start(client)
    client.notify("fixture/release-rejections", {})
    assert handle.event.wait(1)
    response = handle.response
    client._dispatch({"id": handle.request_id, "result": {"sandboxType": "windows"}})
    assert handle.response is response
