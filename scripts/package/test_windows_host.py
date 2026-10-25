import io
import json
from types import SimpleNamespace

import pytest
import windows_host as host
from windows_shell_paths import SmokePathRedirection


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "win32")
    monkeypatch.delenv(host.MARKER, raising=False)


def test_management_and_help_do_not_probe(monkeypatch, windows):
    monkeypatch.setattr(host, "verify_smoke_environment", lambda: pytest.fail("must not probe"))
    assert host.maybe_relay(["version-status"]) is None
    assert host.maybe_relay(["installer-smoke", "--help"]) is None


def test_normal_host_does_not_relaunch(monkeypatch, windows):
    monkeypatch.setattr(host, "verify_smoke_environment", lambda: None)
    monkeypatch.setattr(host, "_relay", lambda args: pytest.fail("must not relay"))
    assert host.maybe_relay(["installer-smoke"]) is None


def test_only_redirection_relays_once_with_exact_arguments(monkeypatch, windows):
    def redirected():
        raise SmokePathRedirection("redirected")

    monkeypatch.setattr(host, "verify_smoke_environment", redirected)
    received = []
    monkeypatch.setattr(host, "_relay", lambda args: received.append(args) or 7)
    args = ["installer-smoke", "--smoke-build-report", "C:/space dir/a&b.json"]
    assert host.maybe_relay(args) == 7
    assert received == [args]
    monkeypatch.setenv(host.MARKER, "1")
    with pytest.raises(RuntimeError, match="refusing another launch"):
        host.maybe_relay(args)
    assert len(received) == 1


def test_environment_mismatch_is_not_retried(monkeypatch, windows):
    def mismatch():
        raise RuntimeError("environment mismatch")

    monkeypatch.setattr(host, "verify_smoke_environment", mismatch)
    monkeypatch.setattr(host, "_relay", lambda args: pytest.fail("must not relay"))
    with pytest.raises(RuntimeError, match="environment mismatch"):
        host.maybe_relay(["all", "--local-test"])


def request(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "token": "test",
                "args": ["installer-smoke"],
                "state": "C:/state",
                "releases": "C:/releases",
            }
        )
    )
    return path


def test_worker_checks_before_invoking_package(monkeypatch, tmp_path):
    def redirected():
        raise SmokePathRedirection("still redirected")

    monkeypatch.setattr(host, "verify_smoke_environment", redirected)
    monkeypatch.setattr(host.subprocess, "call", lambda *a, **kw: pytest.fail("must not run"))
    assert host.worker(request(tmp_path)) == 1
    assert json.loads((tmp_path / "result.json").read_text())["exitCode"] == 1
    assert "still redirected" in (tmp_path / "output.log").read_text()


def test_worker_forwards_failure_and_uses_fixed_entrypoint(monkeypatch, tmp_path):
    monkeypatch.setattr(host, "verify_smoke_environment", lambda: None)
    monkeypatch.setattr(host.subprocess, "CREATE_NO_WINDOW", 0x8000000, raising=False)
    received = []
    monkeypatch.setattr(host.subprocess, "call", lambda cmd, **kw: received.append((cmd, kw)) or 23)
    assert host.worker(request(tmp_path)) == 23
    cmd, options = received[0]
    assert cmd[-2:] == [str(host.Path(host.__file__).with_name("dispatch.py")), "installer-smoke"]
    assert options["env"][host.MARKER] == "1"
    assert options["env"]["AGENT_FRIEND_PACKAGE_STATE_DIR"] == "C:/state"
    assert options["creationflags"] == 0x8000000
    assert host._follow(tmp_path, "test") == 23


def test_follow_rejects_stale_receipts(tmp_path):
    host._write_json(tmp_path / "result.json", {"token": "other", "exitCode": 0})
    with pytest.raises(RuntimeError, match="invalid completion"):
        host._follow(tmp_path, "test")


def test_follow_reports_worker_crash_without_retry(monkeypatch, tmp_path):
    host._write_json(tmp_path / "started.json", {"token": "test", "pid": 123})
    monkeypatch.setattr(host, "_alive", lambda pid: False)
    with pytest.raises(RuntimeError, match="exited without completion"):
        host._follow(tmp_path, "test")


def test_root_overrides_preserve_paths(monkeypatch):
    received = []
    monkeypatch.setattr(
        host, "resolve_package_roots", lambda root, **kw: received.append(kw) or SimpleNamespace()
    )
    host._roots(["all", "--state-dir=C:/one space", "--releases-dir", "C:/two space"])
    assert received == [
        {"state_dir": host.Path("C:/one space"), "releases_dir": host.Path("C:/two space")}
    ]


def test_follow_survives_legacy_console_encoding(monkeypatch, tmp_path):
    (tmp_path / "output.log").write_bytes("✓ 构建完成\r\n".encode())
    host._write_json(tmp_path / "result.json", {"token": "test", "exitCode": 0})
    buffer = io.BytesIO()
    console = io.TextIOWrapper(buffer, encoding="gbk", errors="strict")
    with monkeypatch.context() as patch:
        patch.setattr(host.sys, "stdout", console)
        assert host._follow(tmp_path, "test") == 0
    console.flush()
    assert "构建完成" in buffer.getvalue().decode("gbk")
