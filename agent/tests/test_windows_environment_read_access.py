from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from agent.execution_environment import ManagedEnvironmentError, windows_read_access
from agent.execution_environment.windows_read_access import prepare_read_access

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows ACL provisioning")


@pytest.fixture(autouse=True)
def reset_preparation_cache() -> None:
    windows_read_access._prepared.clear()


def test_read_access_cache_checks_identity_acl_and_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    acl = [b"original"]
    identity = [1, 2, 3]
    monkeypatch.setattr(windows_read_access, "_identity", lambda _root: (*identity, acl[0]))
    monkeypatch.setattr(
        "agent.execution_environment.windows_read_access.time.monotonic", lambda: clock[0]
    )
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    prepare_read_access([tmp_path])
    prepare_read_access([tmp_path])
    assert run.call_count == 1
    acl[0] = b"revoked"
    prepare_read_access([tmp_path])
    assert run.call_count == 2
    identity[1] += 1
    prepare_read_access([tmp_path])
    assert run.call_count == 3
    identity[2] += 1
    prepare_read_access([tmp_path])
    assert run.call_count == 4
    clock[0] += windows_read_access._CACHE_SECONDS
    prepare_read_access([tmp_path])
    assert run.call_count == 5


def test_failed_or_replaced_grant_is_never_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Mock(side_effect=[subprocess.CalledProcessError(5, ["icacls"]), None])
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ManagedEnvironmentError):
        prepare_read_access([tmp_path])
    assert not windows_read_access._prepared
    prepare_read_access([tmp_path])
    assert run.call_count == 2
    windows_read_access._prepared.clear()
    monkeypatch.setattr(
        windows_read_access, "_identity", Mock(side_effect=[(1, 2, 0, b"acl"), (1, 3, 0, b"acl")])
    )
    monkeypatch.setattr(subprocess, "run", Mock())
    with pytest.raises(ManagedEnvironmentError):
        prepare_read_access([tmp_path])
    assert not windows_read_access._prepared


def test_read_access_prepares_concurrent_requests_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time
    from concurrent.futures import ThreadPoolExecutor

    run = Mock(side_effect=lambda *args, **kwargs: time.sleep(0.02))
    monkeypatch.setattr(subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: prepare_read_access([tmp_path]), range(4)))
    assert run.call_count == 1


def test_dacl_read_is_stable_and_does_not_grant_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    first = windows_read_access._read_dacl(tmp_path)
    assert first == windows_read_access._read_dacl(tmp_path)
    assert len(first) >= 20
    run.assert_not_called()


def test_read_access_waits_for_rx_grant_without_resetting_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "python"
    second = tmp_path / "native"
    first.mkdir()
    second.mkdir()
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    prepare_read_access((first, second, first))
    assert run.call_count == 2
    for call, root in zip(run.call_args_list, (first, second), strict=True):
        assert call.args[0] == [
            str(Path(os.environ["SYSTEMROOT"]) / "System32/icacls.exe"),
            str(root),
            "/grant",
            "CodexSandboxUsers:(OI)(CI)(RX)",
            "/Q",
        ]
        assert call.kwargs["check"] is True
        assert call.kwargs["timeout"] == 60
        assert call.kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_read_access_failure_stops_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Mock(side_effect=subprocess.CalledProcessError(5, ["icacls"]))
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ManagedEnvironmentError) as error:
        prepare_read_access((tmp_path, tmp_path / "next"))
    assert error.value.code == "managed_environment_read_access_failed"
    assert run.call_count == 1
