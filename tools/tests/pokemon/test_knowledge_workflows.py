from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw
from tools.pokemon.runtime.client import GameClient
from tools.pokemon.runtime.commands import main
from tools.pokemon.runtime.knowledge import Knowledge, notes
from tools.pokemon.runtime.ocr import read_text
from tools.pokemon.runtime.protocol import GameError, atomic_json
from tools.pokemon.runtime.vision import read_frame
from tools.pokemon.runtime.workflows import (
    inspection_layout,
    inspection_matches,
    matches,
    move_matches,
    replay,
    save_workflow,
    trace_action,
)


def shot(root: Path, number: int, epoch: int = 0, *, cursor: int = 10) -> str:
    source = f"{number:032x}"
    path = root / "screens" / f"{source}.png"
    path.parent.mkdir(exist_ok=True)
    image = Image.new("RGB", (240, 160), (100, 110, 90))
    ImageDraw.Draw(image).rectangle((0, 80, 239, 90), fill=(25, 35, 45))
    ImageDraw.Draw(image).rectangle((cursor, 20, cursor + 8, 28), fill="black")
    image.save(path)
    atomic_json(
        root / "operations" / f"{source}.json",
        {"knowledge_epoch": epoch, "knowledge_timeline": "initial"},
    )
    return source


def test_player_records_do_not_expire_with_button_epochs(tmp_path: Path) -> None:
    source = shot(tmp_path, 1)
    knowledge = Knowledge(tmp_path)
    knowledge.update({"party": {"bud": {"species": "妙蛙种子", "hp": "39/39"}}}, source)
    knowledge.update({"bag": {"potion": {"count": 4}}}, source)
    old = knowledge.load()
    old["epoch"] += 10
    atomic_json(knowledge.path, old)
    record = knowledge.query("party", "bud")["sections"]["party"]["bud"]
    assert "stale" not in record
    assert record["values"]["hp"] == "39/39"
    assert record["values"]["species"] == "妙蛙种子"
    newer = shot(tmp_path, 2, 1)
    knowledge.update({"party": {"bud": {"hp": "30/39"}}}, newer)
    assert knowledge.query("party")["sections"]["party"]["bud"]["values"]["hp"] == "30/39"
    assert knowledge.query("bag")["sections"]["bag"]["potion"]["values"]["count"] == 4


def test_query_does_not_touch_files_and_invalid_patch_is_atomic(tmp_path: Path) -> None:
    source = shot(tmp_path, 1)
    knowledge = Knowledge(tmp_path)
    assert knowledge.query("bag")["sections"] == {"bag": {}}
    assert not knowledge.path.exists()
    knowledge.update({"party": {"bud": {"hp": 20}}}, source)
    before = knowledge.path.read_bytes()
    with pytest.raises(GameError):
        knowledge.update({"party": {"bud": {"hp": 30}, "bad/entity": {"hp": 40}}}, source)
    assert knowledge.path.read_bytes() == before
    with pytest.raises(GameError, match="版本"):
        knowledge.update({"party": {"bud": {"hp": 30}}}, source, revision=0)
    with pytest.raises(GameError, match="来源"):
        knowledge.update({"party": {"bud": {"hp": 30}}}, "../outside")


def test_notebook_optional_multi_source_delete_and_version_history(tmp_path: Path) -> None:
    knowledge = Knowledge(tmp_path)
    knowledge.update({"goals": {"current": {"next": "去探索出口"}}})
    sources = [shot(tmp_path, 1), shot(tmp_path, 2)]
    knowledge.update({"party": {"bud": {"hp": 30}}, "bag": {"potion": {"count": None}}}, sources)
    record = knowledge.query("party", "bud", details=True)["sections"]["party"]["bud"]
    assert record["metadata"]["hp"]["sources"] == sources
    assert record["metadata"]["hp"]["updated_at"]
    assert "metadata" not in knowledge.query("party")["sections"]["party"]["bud"]
    knowledge.update({"bag": {"potion": None}})
    assert knowledge.query("bag")["sections"]["bag"] == {}
    previous = json.loads((tmp_path / "knowledge-history/2.json").read_text(encoding="utf-8"))
    assert previous["sections"]["bag"]["potion"]["count"]["value"] is None


def test_legacy_notes_migrate_without_resurrecting_old_prose(tmp_path: Path) -> None:
    atomic_json(tmp_path / "notes.json", {"text": "旧笔记"})
    knowledge = Knowledge(tmp_path)
    assert knowledge.query()["journal"] == "旧笔记"
    assert not knowledge.path.exists()
    notes(tmp_path, "新计划")
    assert knowledge.query()["journal"] == "新计划"
    atomic_json(tmp_path / "notes.json", {"text": "不能覆盖的新旧冲突"})
    knowledge.update({"notes": {"route": {"lesson": "新经验"}}})
    assert notes(tmp_path)["text"] == "新计划"
    knowledge.checkpoint("paired")
    notes(tmp_path, "后来")
    knowledge.restore("paired")
    assert notes(tmp_path)["text"] == "新计划"


def test_current_page_and_records_commit_as_one_revision(tmp_path: Path) -> None:
    knowledge = Knowledge(tmp_path)
    knowledge.update({"bag": {"ball": {"count": 3}}}, text="旧地点，战斗中")
    result = knowledge.update(
        {"bag": {"ball": {"count": 2}}, "party": {"new_member": {"name": "新伙伴"}}},
        text="捕获完成，下一步回城；具体HP未知",
        revision=1,
    )
    assert result["revision"] == 2
    assert knowledge.query()["journal"] == "捕获完成，下一步回城；具体HP未知"
    assert knowledge.query("bag")["sections"]["bag"]["ball"]["values"]["count"] == 2
    before = json.loads((tmp_path / "knowledge-history/1.json").read_text(encoding="utf-8"))
    assert before["journal"] == "旧地点，战斗中"
    assert before["sections"]["bag"]["ball"]["count"]["value"] == 3
    assert "hp" not in knowledge.query("party")["sections"]["party"]["new_member"]["values"]
    stored = knowledge.path.read_bytes()
    with pytest.raises(GameError):
        knowledge.update({"party": {"bad/id": {"hp": 10}}}, text="不能半写入")
    with pytest.raises(GameError):
        knowledge.update({"bag": {"ball": {"count": 1}}}, text="x" * 8001)
    assert knowledge.path.read_bytes() == stored
    assert len(list((tmp_path / "knowledge-history").glob("*.json"))) == 2


def test_focused_notebook_preserves_experience_without_inference(tmp_path: Path) -> None:
    knowledge = Knowledge(tmp_path)
    knowledge.update(
        {
            "party": {"bud": {"hp": None}},
            "bag": {"ball": {"count": 2}},
            "goals": {"current": {"target": "过去的目标"}},
            "places": {"forest": {"route": "桥东绕行"}},
            "notes": {"battle": {"finished": False}},
        },
        text="目前在城里，先找商店",
    )
    stored = knowledge.path.read_bytes()
    brief = knowledge.query()
    assert set(brief["sections"]) == {"party", "bag"}
    assert brief["index"] == {"goals": ["current"], "places": ["forest"], "notes": ["battle"]}
    assert "过去的目标" not in json.dumps(brief, ensure_ascii=False)
    selected = knowledge.query("places", "forest")
    assert selected["sections"]["places"]["forest"]["values"]["route"] == "桥东绕行"
    assert "journal" not in selected
    assert knowledge.query(all_records=True)["sections"]["notes"]["battle"]["values"] == {
        "finished": False
    }
    assert knowledge.path.read_bytes() == stored


@pytest.mark.parametrize("flags", [["--entity", "bud"], ["--all", "--section", "party"]])
def test_notebook_rejects_ambiguous_queries(tmp_path: Path, capsys: Any, flags: list[str]) -> None:
    atomic_json(tmp_path / "instance.json", {"schema": 1})
    binding = tmp_path / "binding.json"
    atomic_json(binding, {"instance_root": str(tmp_path)})
    assert main(binding, ["knowledge", *flags]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "invalid_query"
    assert not (tmp_path / "knowledge.json").exists()


def test_notebook_cli_combines_page_and_patch_and_rejects_query_flags(
    tmp_path: Path, capsys: Any
) -> None:
    atomic_json(tmp_path / "instance.json", {"schema": 1})
    binding = tmp_path / "binding.json"
    atomic_json(binding, {"instance_root": str(tmp_path)})
    argv = [
        "knowledge",
        "--text",
        "恢复完成，下一步探索",
        "--data",
        '{"party":{"bud":{"hp":null}}}',
    ]
    assert main(binding, argv) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 1
    assert main(binding, [*argv, "--section", "party"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "invalid_arguments"
    assert main(binding, ["knowledge", "--all"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["journal"] == "恢复完成，下一步探索"
    assert result["sections"]["party"]["bud"]["values"]["hp"] is None
    assert main(binding, ["knowledge", "--text", ""]) == 0
    capsys.readouterr()
    assert Knowledge(tmp_path).query()["journal"] == ""


def test_notebook_cli_accepts_structured_json_without_source(tmp_path: Path, capsys: Any) -> None:
    atomic_json(tmp_path / "instance.json", {"schema": 1})
    binding = tmp_path / "binding.json"
    atomic_json(binding, {"instance_root": str(tmp_path)})
    text = {"goals": {"current": {"next": '中文 "引号" 和 \\ 路径'}}}
    assert main(binding, ["knowledge", "--data", json.dumps(text)]) == 0
    capsys.readouterr()
    assert main(binding, ["knowledge", "--section", "goals", "--details"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["sections"]["goals"]["current"]["values"] == text["goals"]["current"]


def test_restore_pairs_knowledge_and_notes_and_blocks_future_evidence(tmp_path: Path) -> None:
    source = shot(tmp_path, 1)
    knowledge = Knowledge(tmp_path)
    knowledge.update({"bag": {"potion": {"count": 4}}}, source)
    notes(tmp_path, "before")
    knowledge.checkpoint("one")
    knowledge.update({"bag": {"potion": {"count": 3}}}, source)
    notes(tmp_path, "future")
    knowledge.restore("one")
    assert notes(tmp_path)["text"] == "before"
    assert knowledge.query("bag")["sections"]["bag"]["potion"]["values"]["count"] == 4
    with pytest.raises(GameError, match="时间线"):
        knowledge.update({"bag": {"potion": {"count": 3}}}, source)
    knowledge.restore("legacy-without-knowledge")
    assert knowledge.query()["sections"] == {}
    assert notes(tmp_path)["text"] == ""


def test_unknown_action_blocks_record_writes_but_not_queries(tmp_path: Path) -> None:
    source = shot(tmp_path, 1)
    atomic_json(tmp_path / "state.json", {"pending": source})
    assert Knowledge(tmp_path).query()["sections"] == {}
    with pytest.raises(GameError, match="对账"):
        Knowledge(tmp_path).update({"bag": {"potion": {"count": 4}}}, source)
    with pytest.raises(GameError, match="对账"):
        notes(tmp_path, "unsettled")


def test_player_load_rewinds_inventory_but_remembers_attempt(tmp_path: Path) -> None:
    source = shot(tmp_path, 1)
    knowledge = Knowledge(tmp_path)
    knowledge.update({"party": {"bud": {"hp": 40}}, "bag": {"ball": {"count": 10}}}, source)
    knowledge.checkpoint("before-capture")
    knowledge.update(
        {
            "party": {"new_member": {"name": "captured"}},
            "bag": {"ball": {"count": 4}},
            "places": {"cave": {"item_taken": True}},
            "notes": {"attempt": {"lesson": "previous tactic"}},
        },
        source,
    )
    notes(tmp_path, "remember this attempt")
    _, _, workflow = record(tmp_path)
    knowledge.restore("before-capture", preserve_experience=True)
    state = knowledge.query(all_records=True)["sections"]
    assert set(state["party"]) == {"bud"}
    assert state["bag"]["ball"]["values"]["count"] == 10
    assert state["notes"]["attempt"]["values"]["lesson"] == "previous tactic"
    assert "stale" not in state["places"]["cave"]
    assert knowledge.query()["restore_context"]["experience_preserved"]
    assert notes(tmp_path)["text"] == "remember this attempt"
    assert save_workflow(tmp_path, workflow)["status"] == "recorded"
    with pytest.raises(GameError, match="时间线"):
        knowledge.update({"party": {"new_member": {"name": "captured"}}}, source)
    knowledge.restore("legacy", preserve_experience=True)
    assert knowledge.query("bag")["sections"]["bag"] == {}
    assert (
        knowledge.query("notes")["sections"]["notes"]["attempt"]["values"]["lesson"]
        == "previous tactic"
    )


def record(root: Path, *, kind: str = "move") -> tuple[str, str, dict[str, Any]]:
    start = shot(root, 1)
    end = shot(root, 2, cursor=20)
    trace_action(
        root,
        start,
        {"status": "completed", "observation_id": end},
        [{"keys": ["A"], "frames": 8}, {"keys": [], "frames": 24}],
    )
    workflow = {"id": "test", "kind": kind, "intent": "selected move", "stages": [{"to": end}]}
    return start, end, workflow


def test_workflow_requires_real_continuous_demonstration_and_inspection_return(
    tmp_path: Path,
) -> None:
    start, end, workflow = record(tmp_path)
    assert save_workflow(tmp_path, workflow)["stages"] == 1
    with pytest.raises(GameError, match="返回原界面"):
        save_workflow(tmp_path, {**workflow, "kind": "inspect"})
    with pytest.raises(GameError, match="路线"):
        save_workflow(tmp_path, {**workflow, "kind": "route"})
    with pytest.raises(GameError, match="连续"):
        save_workflow(tmp_path, {**workflow, "stages": [{"to": end}, {"to": end}]})
    with pytest.raises(GameError, match="成功记录"):
        save_workflow(tmp_path, {**workflow, "stages": [{"to": start}]})


def test_cursor_changes_fail_even_with_very_similar_images(tmp_path: Path) -> None:
    start, end, _ = record(tmp_path)
    a, b = (tmp_path / "screens" / f"{s}.png" for s in (start, end))
    assert matches(a, a, [0, 0, 240, 160])
    assert not matches(a, b, [0, 0, 240, 160])


def test_inspection_ignores_world_and_hp_but_preserves_choice_cursor(tmp_path: Path) -> None:
    a, b = tmp_path / "before.png", tmp_path / "after.png"
    im = Image.new("RGB", (240, 160), (80, 120, 80))
    draw = ImageDraw.Draw(im)
    draw.rectangle((170, 0, 239, 117), fill=(115, 107, 132))
    draw.rectangle((175, 5, 234, 112), fill="white")
    draw.rectangle((177, 27, 182, 32), fill="black")
    draw.rectangle((0, 120, 239, 159), fill=(0, 123, 198))
    im.save(a)
    draw.rectangle((0, 0, 169, 119), fill=(120, 80, 80))
    # Rounded panel corners expose the world; they are not menu/cursor pixels.
    draw.rectangle((170, 0, 173, 3), fill=(120, 80, 80))
    im.save(b)
    assert inspection_matches(b, a, [0, 0, 240, 160], exiting=False)
    draw.rectangle((177, 27, 182, 32), fill="white")
    draw.rectangle((177, 47, 182, 52), fill="black")
    im.save(b)
    assert not inspection_matches(b, a, [0, 0, 240, 160], exiting=False)
    draw.rectangle((0, 0, 119, 113), fill=(74, 173, 165))
    draw.rectangle((120, 0, 239, 113), fill=(57, 140, 140))
    im.save(a)
    draw.rectangle((30, 50, 75, 65), fill=(200, 20, 50))
    im.save(b)
    assert inspection_matches(b, a, [0, 0, 240, 160], exiting=True)
    assert not inspection_matches(b, a, [0, 0, 240, 160], exiting=False)


def test_move_guard_allows_hp_pp_changes_but_rejects_different_decisions(tmp_path: Path) -> None:
    a, b = tmp_path / "before.png", tmp_path / "after.png"
    im = Image.new("RGB", (240, 160), (80, 120, 80))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 114, 239, 159), fill=(115, 107, 132))
    draw.rectangle((4, 118, 155, 154), fill="white")
    draw.rectangle((164, 118, 234, 154), fill="white")
    draw.rectangle((7, 125, 10, 130), fill="black")
    draw.rectangle((15, 125, 35, 130), fill="black")
    draw.rectangle((141, 80, 165, 86), fill=(66, 66, 66))
    im.save(a)
    draw.rectangle((0, 0, 135, 110), fill=(120, 80, 80))
    draw.rectangle((180, 98, 220, 105), fill="red")
    draw.rectangle((194, 125, 230, 132), fill="black")
    draw.rectangle((166, 125, 179, 132), fill="yellow")
    im.save(b)
    assert not matches(b, a, [0, 0, 240, 160])
    assert move_matches(b, a, [0, 0, 240, 160])
    good = im.copy()
    shifted = good.copy()
    shifted_draw = ImageDraw.Draw(shifted)
    shifted_draw.rectangle((141, 80, 165, 86), fill=(80, 120, 80))
    shifted_draw.rectangle((141, 79, 165, 85), fill=(66, 66, 66))
    shifted.save(b)
    assert move_matches(b, a, [0, 0, 240, 160])
    for area in ((7, 141, 10, 146), (15, 125, 35, 130), (141, 80, 165, 86)):
        changed = good.copy()
        ImageDraw.Draw(changed).rectangle(area, fill="red")
        changed.save(b)
        assert not move_matches(b, a, [0, 0, 240, 160])
    # Caller-selected regions and unknown layouts retain their exact guards.
    good.save(b)
    assert not move_matches(b, a, [0, 0, 240, 100])
    changed = good.copy()
    ImageDraw.Draw(changed).rectangle((0, 114, 239, 159), fill="black")
    changed.save(b)
    assert not move_matches(b, a, [0, 0, 240, 160])


@pytest.mark.parametrize("stripe_height", [31, 21])
def test_party_recognition_does_not_depend_on_empty_slots(
    tmp_path: Path, stripe_height: int
) -> None:
    path = tmp_path / "party.png"
    im = Image.new("RGB", (240, 160), (80, 120, 80))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, 119, stripe_height - 1), fill=(74, 173, 165))
    draw.rectangle((120, 0, 239, stripe_height - 1), fill=(57, 140, 140))
    draw.rectangle((0, 114, 239, 159), fill=(115, 107, 132))
    draw.rectangle((4, 119, 235, 154), fill="white")
    im.save(path)
    assert inspection_layout(path) == "party"
    assert read_frame(path).kind != "battle_menu"
    draw.rectangle((0, 0, 239, stripe_height - 1), fill=(80, 120, 80))
    im.save(path)
    assert inspection_layout(path) is None


class ReplayClient(GameClient):
    def __init__(self, root: Path, sources: list[str]) -> None:
        self.root, self.sources = root, sources
        self.index = 0
        self.presses = 0

    def status(self) -> dict[str, Any]:
        return {"observation_id": self.sources[self.index], "paused": False}

    def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
        if kind == "act" and any(s["keys"] for s in kwargs["steps"]):
            self.presses += 1
            self.index = min(self.index + 1, len(self.sources) - 1)
        source = self.sources[self.index]
        return {
            "status": "completed",
            "observation_id": source,
            "screenshot": str(self.root / "screens" / f"{source}.png"),
        }


def test_replay_success_start_mismatch_and_post_action_stop(tmp_path: Path) -> None:
    start, end, workflow = record(tmp_path)
    save_workflow(tmp_path, workflow)
    client = ReplayClient(tmp_path, [start, end])
    result = replay(client, "test", start, "move")
    assert result["workflow"]["stop_reason"] == "completed"
    assert client.presses == 1
    wrong = shot(tmp_path, 3, cursor=30)
    client = ReplayClient(tmp_path, [wrong])
    assert (
        replay(client, "test", wrong, "move")["workflow"]["stop_reason"]
        == "start_or_cursor_mismatch"
    )
    assert client.presses == 0
    client = ReplayClient(tmp_path, [start, wrong])
    assert replay(client, "test", start, "move")["workflow"]["stop_reason"] == "result_mismatch"
    assert client.presses == 1


def test_ocr_unavailable_is_read_only_and_cached_source_is_current(tmp_path: Path) -> None:
    import hashlib

    source = shot(tmp_path, 1)
    assert read_text(tmp_path, source)["status"] == "unavailable"
    assert not (tmp_path / "ocr").exists()
    digest = hashlib.sha256((tmp_path / "screens" / f"{source}.png").read_bytes()).hexdigest()
    atomic_json(
        tmp_path / "ocr" / "results" / f"{digest}.json",
        {"source": "old", "lines": [{"text": "39/39", "confidence": 0.99}]},
    )
    result = read_text(tmp_path, source)
    assert result["source"] == source
    assert result["image_paths"] == [f"{source}.png", f"view-{source}-3x.png"]
    with Image.open(tmp_path / "screens" / result["image_paths"][1]) as zoom:
        assert zoom.size == (720, 480)
        with Image.open(tmp_path / "screens" / f"{source}.png") as original:
            assert zoom.getpixel((75, 255)) == original.getpixel((25, 85))
    assert not Knowledge(tmp_path).path.exists()


def test_move_workflow_waits_through_automatic_battle_text(tmp_path: Path) -> None:
    start, end, workflow = record(tmp_path)
    battle = shot(tmp_path, 3)
    image = Image.open(tmp_path / "screens" / f"{battle}.png").convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 114, 239, 159), fill=(206, 173, 74))
    draw.rectangle((6, 121, 233, 154), fill=(41, 82, 107))
    draw.rectangle((12, 126, 30, 135), fill="white")
    image.save(tmp_path / "screens" / f"{battle}.png")
    save_workflow(tmp_path, workflow)

    class DelayedBattle(ReplayClient):
        waits = 0

        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            if kind == "act" and self.index == 1 and not any(s["keys"] for s in kwargs["steps"]):
                self.waits += 1
                if self.waits >= 8:
                    self.index = 2
            return super().request(kind, **kwargs)

    client = DelayedBattle(tmp_path, [start, battle, end])
    result = replay(client, "test", start, "move")
    assert client.presses == 1 and client.waits >= 8
    assert result["workflow"]["stop_reason"] == "completed"
    assert f"{battle}.png" in result["image_paths"]


def test_personal_cli_connects_notes_and_knowledge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    atomic_json(tmp_path / "instance.json", {"schema": 1})
    binding = tmp_path / "binding.json"
    atomic_json(binding, {"instance_root": str(tmp_path)})
    source = shot(tmp_path, 1)
    assert main(binding, ["notes", "--text", "test"]) == 0
    assert notes(tmp_path)["text"] == "test"
    assert (
        main(
            binding,
            [
                "knowledge",
                "--source",
                source,
                "--data",
                json.dumps({"goals": {"current": {"target": "visible landmark"}}}),
            ],
        )
        == 0
    )
    assert main(binding, ["knowledge", "--section", "goals"]) == 0
    assert "visible landmark" in capsys.readouterr().out
    assert not (tmp_path / "request.txt").exists()
    assert main(binding, ["inspect", "--observation-id", source]) == 1
