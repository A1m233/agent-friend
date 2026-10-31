from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from tools.pokemon.runtime.client import GameClient
from tools.pokemon.runtime.protocol import (
    GameError,
    atomic_json,
    atomic_text,
    controller_lock,
    steps_wire,
)
from tools.pokemon.setup import install, prepare


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    rom = tmp_path / "sample.gba"
    rom.write_bytes(bytes(256))
    emulator = tmp_path / "emulator"
    emulator.write_bytes(b"fake")
    personal = tmp_path / "skills"
    install(personal)
    result = prepare(
        rom=rom,
        emulator=emulator,
        instances_root=tmp_path / "instances",
        skill_path=personal / "pokemon-firered",
        source="original test fixture",
        language="homebrew-test",
    )
    return Path(str(result["instance_root"]))


def respond(root: Path, *, status: str = "completed", save: bool = False) -> threading.Thread:
    def run() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            request = root / "request.txt"
            if request.exists():
                try:
                    op = request.read_text(encoding="utf-8").split("|")[1]
                    request.unlink()
                except PermissionError:
                    # Windows can briefly deny reads during atomic publication.
                    # Match the emulator's next-frame retry, within this deadline.
                    time.sleep(0.002)
                    continue
                (root / "screens" / f"{op}.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
                if save:
                    (root / "saves" / f"{op}.ss0").write_bytes(b"save fixture")
                atomic_text(root / "receipts" / f"{op}.txt", f"{op}|{status}|1|ok|boot1\n")
                return
            time.sleep(0.002)

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def observe(root: Path) -> dict:
    thread = respond(root)
    result = GameClient(root, timeout=1).request("observe")
    thread.join()
    return result


@pytest.mark.parametrize(
    "steps",
    [
        None,
        [],
        [{}],
        [{"keys": ["a"], "frames": 1}],
        [{"keys": ["A"], "frames": True}],
        [{"keys": ["A", "A"], "frames": 1}],
        [{"keys": ["LEFT", "RIGHT"], "frames": 1}],
        [{"keys": [], "frames": 601}],
        [{"keys": [], "frames": 400}, {"keys": [], "frames": 400}],
        [{"keys": [], "frames": 1, "extra": 1}],
        [{"keys": [], "frames": 1}] * 33,
    ],
)
def test_bounds(steps: object) -> None:
    with pytest.raises(GameError):
        steps_wire(steps)


def test_key_masks() -> None:
    assert (
        steps_wire([{"keys": ["A", "RIGHT"], "frames": 60}, {"keys": [], "frames": 2}])
        == "17:60,0:2"
    )


def test_personal_install_is_non_destructive(tmp_path: Path) -> None:
    assert install(tmp_path)["status"] == "installed"
    assert install(tmp_path)["status"] == "unchanged"
    entry = tmp_path / "pokemon-firered/SKILL.md"
    entry.write_text("user edit", encoding="utf-8")
    with pytest.raises(GameError, match="已有不同内容"):
        install(tmp_path)
    assert entry.read_text() == "user edit"


def test_observation_and_stale_guard(instance: Path) -> None:
    client = GameClient(instance, timeout=1)
    client.resume()
    shot = observe(instance)
    with pytest.raises(GameError) as err:
        client.request("act", observation_id="wrong", steps=[{"keys": ["A"], "frames": 1}])
    assert err.value.code == "stale_observation"
    thread = respond(instance)
    result = client.request(
        "act", observation_id=shot["observation_id"], steps=[{"keys": ["A"], "frames": 1}]
    )
    thread.join()
    assert result["status"] == "completed"
    assert result["observation_id"] != shot["observation_id"]
    assert client.status()["pending"] is None
    assert Path(result["screenshot"]).exists()


def test_timeout_never_replays(instance: Path) -> None:
    client = GameClient(instance, timeout=0.05)
    with pytest.raises(GameError) as err:
        client.request("observe")
    assert err.value.code == "action_unknown"
    previous = (instance / "request.txt").read_bytes()
    with pytest.raises(GameError):
        client.request("act", steps=[{"keys": [], "frames": 1}])
    with pytest.raises(GameError):
        client.resume()
    with pytest.raises(GameError):
        client.request("observe")
    assert (instance / "request.txt").read_bytes() == previous
    assert (instance / "paused").exists()


def test_restart_reconciles_receipt_before_observing(instance: Path) -> None:
    with pytest.raises(GameError):
        GameClient(instance, timeout=0.05).request("observe")
    old = GameClient(instance).status()["pending"]
    atomic_text(instance / "receipts" / f"{old}.txt", f"{old}|cancelled|3|expired|boot1\n")
    (instance / "request.txt").unlink()
    result = observe(instance)
    assert result["observation_id"] != old
    record = json.loads((instance / "operations" / f"{old}.json").read_text())
    assert record["status"] == "cancelled"


def test_pause_is_available_while_locked(instance: Path) -> None:
    with controller_lock(instance):
        assert GameClient(instance).pause()["status"] == "pause_requested"
        with pytest.raises(GameError) as err:
            GameClient(instance).request("observe")
        assert err.value.code == "controller_busy"


def test_partial_cancel_invalidates_old_picture(instance: Path) -> None:
    client = GameClient(instance, timeout=1)
    client.resume()
    shot = observe(instance)
    thread = respond(instance, status="cancelled")
    result = client.request(
        "act", observation_id=shot["observation_id"], steps=[{"keys": [], "frames": 1}]
    )
    thread.join()
    assert result["status"] == "cancelled"
    assert client.status()["observation_id"] is None


def test_checkpoint_does_not_claim_af_recovery(instance: Path) -> None:
    client = GameClient(instance, timeout=1)
    client.resume()
    shot = observe(instance)
    thread = respond(instance, save=True)
    result = client.request("checkpoint", observation_id=shot["observation_id"])
    thread.join()
    assert result["checkpoint_scope"] == "emulator_only_not_af_context"
    assert len(result["save_sha256"]) == 64


def test_corrupt_pending_is_not_used_as_path(instance: Path) -> None:
    atomic_json(instance / "state.json", {"pending": "../outside"})
    with pytest.raises(GameError) as err:
        GameClient(instance).request("observe")
    assert err.value.code == "invalid_pending"


def test_personal_catalog_recognizes_skill(tmp_path: Path) -> None:
    from agent.skills.loader import SkillLoader
    from agent.skills.models import SkillRoot

    install(tmp_path)
    catalog = SkillLoader().load((SkillRoot("personal", tmp_path),), generation=1)
    assert not catalog.issues
    assert len(catalog.entries) == 1
    assert catalog.entries[0].name == "pokemon-firered"
    assert catalog.entries[0].source == "personal"


def test_status_exposes_paused_and_permission_roots(instance: Path) -> None:
    client = GameClient(instance)
    assert client.status()["paused"] is True
    assert client.status()["instance_root"] == str(instance)
    assert client.status()["screens_root"] == str(instance / "screens")
    client.resume()
    assert client.status()["paused"] is False
    assert "paused" not in json.loads((instance / "state.json").read_text())


def test_personal_begin_and_finish_without_manual_checkpoint_id(instance: Path, capsys) -> None:
    from tools.pokemon.runtime.commands import main

    thread = respond(instance)
    assert main(instance / "unused.json", ["begin", "--instance", str(instance)]) == 0
    thread.join()
    shot = json.loads(capsys.readouterr().out)
    assert shot["kind"] == "observe"
    assert not (instance / "paused").exists()
    thread = respond(instance, save=True)
    assert main(instance / "unused.json", ["finish", "--instance", str(instance)]) == 0
    thread.join()
    saved = json.loads(capsys.readouterr().out)
    assert saved["kind"] == "checkpoint" and saved["paused"] is True
    assert saved["previous_observation_id"] == shot["observation_id"]
    assert (instance / "paused").exists()


def test_finish_failure_still_pauses(instance: Path) -> None:
    import argparse

    from tools.pokemon.runtime.commands import execute

    client = GameClient(instance)
    client.resume()
    with pytest.raises(GameError, match="观察已过期"):
        execute(client, argparse.Namespace(command="finish", observation_id="stale"))
    assert (instance / "paused").exists()


def test_begin_timeout_pauses(instance: Path) -> None:
    import argparse

    from tools.pokemon.runtime.commands import execute

    with pytest.raises(GameError, match="模拟器未确认"):
        execute(GameClient(instance, timeout=0.05), argparse.Namespace(command="begin"))
    assert (instance / "paused").exists()


def test_taps_and_steps_use_existing_wire_bounds() -> None:
    import argparse

    from tools.pokemon.runtime.commands import action_steps

    taps = action_steps(argparse.Namespace(steps=None, step=None, tap=["START", "A"]))
    assert steps_wire(taps) == "8:8,0:24,1:8,0:24"
    movement = action_steps(
        argparse.Namespace(steps=None, tap=None, step=["B+RIGHT:30", "WAIT:20"])
    )
    assert steps_wire(movement) == "18:30,0:20"
    for parts in (["UP:601"], ["UP+DOWN:2"], ["WAIT:no"], ["UP:-1"]):
        with pytest.raises(GameError):
            action_steps(argparse.Namespace(steps=None, tap=None, step=parts))
    with pytest.raises(GameError):
        action_steps(argparse.Namespace(steps=None, tap=["A"] * 17, step=None))


def test_managed_upgrade_preserves_binding_and_refuses_user_edits(
    tmp_path: Path, monkeypatch
) -> None:
    from tools.pokemon import setup

    personal = tmp_path / "skills"
    setup.install(personal)
    target = personal / "pokemon-firered"
    (target / "binding.json").write_text('{"instance_root":"preserve"}')
    new_files = {**setup.source_files(), "scripts/new_resource.py": b"# new\n"}
    monkeypatch.setattr(setup, "source_files", lambda: new_files)
    result = setup.upgrade(personal, tmp_path / "backups")
    assert result["status"] == "updated"
    assert (Path(result["backup"]) / "binding.json").read_bytes() == (
        target / "binding.json"
    ).read_bytes()
    assert setup.upgrade(personal, tmp_path / "backups")["status"] == "unchanged"
    (target / "SKILL.md").write_text("user edit", encoding="utf-8")
    with pytest.raises(GameError, match="个人文件已修改"):
        setup.upgrade(personal, tmp_path / "backups")
    assert (target / "SKILL.md").read_text() == "user edit"


def test_expired_desktop_budget_refuses_action_before_publication(instance: Path, capsys) -> None:
    from tools.pokemon.runtime.commands import main

    GameClient(instance).resume()
    atomic_json(instance / "desktop-budget.json", {"id": "test", "deadline": 1})
    assert (
        main(
            instance / "unused",
            ["act", "--instance", str(instance), "--observation-id", "old", "--tap", "A"],
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["code"] == "budget_exhausted"
    assert not (instance / "request.txt").exists()
    assert (instance / "paused").exists()


@pytest.mark.parametrize("expires", [False, True])
def test_desktop_watch_scopes_cancellation_and_always_pauses(
    instance: Path, tmp_path: Path, monkeypatch, expires: bool
) -> None:
    import io

    from tools.pokemon import desktop_pilot

    (tmp_path / "agent_bridge.log").write_text("", encoding="utf-8")
    monkeypatch.setattr(desktop_pilot, "log_dir", lambda: tmp_path)
    ticks = iter([0, 2 if expires else 0, 2 if expires else 0])
    monkeypatch.setattr(desktop_pilot.time, "monotonic", lambda: next(ticks))
    calls = []
    run = {
        "run_id": "new",
        "session_id": "chosen",
        "control_scope": "desktop_text",
        "runtime_instance_id": "runtime",
        "created_at": "now",
        "status": "running" if expires else "terminal",
        "model_calls": 1,
    }

    def urlopen(request, timeout):
        calls.append((request.full_url, request.get_method()))
        if len(calls) == 1:
            payload = {"runs": [{**run, "run_id": "old"}]}
        elif request.full_url.endswith("/runs"):
            (tmp_path / "agent_bridge.log").write_text(
                "run_id=new event=llm_transport phase=request_started call_index=1 attempt=1\n"
                "run_id=new event=llm_transport phase=request_started call_index=1 attempt=2\n",
                encoding="utf-8",
            )
            payload = {"runs": [{**run, "session_id": "unrelated"}, run]}
        else:
            payload = run
        return io.StringIO(json.dumps(payload))

    monkeypatch.setattr(desktop_pilot.urllib.request, "urlopen", urlopen)
    GameClient(instance).resume()
    output = tmp_path / "pilot.json"
    result = desktop_pilot.watch(instance, session_id="chosen", max_seconds=1, output=output)
    assert result["stop_reason"] == ("time_budget" if expires else "run_terminal")
    assert (instance / "paused").exists()
    assert not (instance / "desktop-budget.json").exists()
    posts = [url for url, method in calls if method == "POST"]
    assert len(posts) == int(expires)
    if expires:
        assert posts[0].endswith(
            "/sessions/chosen/runs/new/cancel?expectedRuntimeInstanceId=runtime"
        )
    assert json.loads(output.read_text(encoding="utf-8"))["run_id"] == "new"
    if not expires:
        assert len(result["calls"]) == 2
        assert result["observed_model_calls"] == 1
        assert result["calls_match_final"]
