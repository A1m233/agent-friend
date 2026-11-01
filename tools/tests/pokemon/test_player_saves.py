from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest
from tools.pokemon.runtime.client import GameClient
from tools.pokemon.runtime.commands import main
from tools.pokemon.runtime.protocol import GameError, atomic_json
from tools.pokemon.runtime.saves import list_saves, load_save, saved_record


def checkpoint(root: Path) -> str:
    key = "a" * 32
    path = root / "saves" / f"{key}.ss0"
    path.parent.mkdir()
    path.write_bytes(b"opaque state")
    atomic_json(
        root / "operations" / f"{key}.json",
        {
            "operation_id": key,
            "kind": "checkpoint",
            "status": "completed",
            "started_at": 100,
            "save_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    )
    return key


def test_only_completed_local_checkpoint_is_loadable(tmp_path: Path) -> None:
    key = checkpoint(tmp_path)
    assert list_saves(tmp_path)["saves"][0]["checkpoint_id"] == key
    assert saved_record(tmp_path, key, verify_hash=True)["kind"] == "checkpoint"
    with pytest.raises(GameError, match="本实例"):
        saved_record(tmp_path, "b" * 32)
    (tmp_path / "saves" / f"{key}.ss0").write_bytes(b"changed")
    with pytest.raises(GameError, match="校验"):
        saved_record(tmp_path, key, verify_hash=True)


class LoadClient(GameClient):
    def __init__(self, root: Path, *, fail: bool = False) -> None:
        self.root, self.fail = root, fail
        self.paused = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def pause(self) -> dict[str, Any]:
        self.paused = True
        return {}

    def resume(self) -> dict[str, Any]:
        self.paused = False
        return {}

    def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((kind, kwargs))
        if self.fail:
            raise GameError("action_unknown", "uncertain result")
        if kind == "restore":
            assert self.paused
            assert kwargs["preserve_experience"] is True
        return {"status": "completed", "operation_id": kind, "observation_id": kind}


def test_load_gets_fresh_observation_and_does_not_reset_budget(tmp_path: Path) -> None:
    key = checkpoint(tmp_path)
    budget = tmp_path / "desktop-budget.json"
    atomic_json(budget, {"deadline": time.time() + 60})
    original = budget.read_bytes()
    client = LoadClient(tmp_path)
    assert load_save(client, key)["observation_id"] == "observe"
    assert [call[0] for call in client.calls] == ["restore", "observe"]
    assert not client.paused and budget.read_bytes() == original
    client = LoadClient(tmp_path, fail=True)
    with pytest.raises(GameError):
        load_save(client, key)
    assert client.paused and len(client.calls) == 1


def test_watchdog_expiry_during_restore_does_not_resume(tmp_path: Path) -> None:
    key = checkpoint(tmp_path)
    atomic_json(tmp_path / "desktop-budget.json", {"id": "watch", "deadline": time.time() + 60})

    class ExpiringClient(LoadClient):
        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            result = super().request(kind, **kwargs)
            atomic_json(self.root / "desktop-budget.json", {"id": "watch", "deadline": 0})
            return result

    client = ExpiringClient(tmp_path)
    assert load_save(client, key)["paused"] is True
    assert client.paused and len(client.calls) == 1


def test_player_load_cannot_bypass_expired_round(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = checkpoint(tmp_path)
    atomic_json(tmp_path / "instance.json", {"schema": 1})
    atomic_json(tmp_path / "desktop-budget.json", {"deadline": 0})
    assert (
        main(tmp_path / "unused", ["load", "--instance", str(tmp_path), "--checkpoint-id", key])
        == 1
    )
    assert json.loads(capsys.readouterr().out)["code"] == "budget_exhausted"
    assert not (tmp_path / "request.txt").exists()
