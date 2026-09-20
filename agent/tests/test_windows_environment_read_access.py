from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from agent.execution_environment import ManagedEnvironmentError
from agent.execution_environment.windows_read_access import prepare_read_access

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows ACL provisioning")


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
