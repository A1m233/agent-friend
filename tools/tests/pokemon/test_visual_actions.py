from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw
from tools.pokemon.runtime.actions import MAX_BATTLE_FRAMES, VisualActions
from tools.pokemon.runtime.client import GameClient
from tools.pokemon.runtime.motion import compare
from tools.pokemon.runtime.protocol import GameError
from tools.pokemon.runtime.vision import read_frame


def screen(path: Path, *, page: int = 0, dialogue: bool = True, choice: bool = False) -> None:
    im = Image.new("RGB", (240, 160), (100, 110, 90))
    draw = ImageDraw.Draw(im)
    if dialogue:
        draw.rectangle((0, 114, 239, 159), fill=(165, 214, 231))
        draw.rectangle((6, 120, 233, 156), fill="white")
        draw.rectangle((10, 125, 20 + page * 5, 134), fill="blue")
    if choice:
        draw.rectangle((160, 80, 228, 110), fill="white")
    if not dialogue:
        draw.rectangle((0, 0, 16, 16), fill="green")
    im.save(path)


class FakeClient(GameClient):
    def __init__(self, root: Path, screens: list[Path]) -> None:
        self.root = root
        self.screens = screens
        self.page = self.index = 0
        self.presses: list[list[str]] = []
        self.paused = False
        self.hex_ids = False

    def status(self) -> dict[str, Any]:
        return {
            "observation_id": f"{self.index:032x}" if self.hex_ids else str(self.index),
            "paused": self.paused,
        }

    def pause(self) -> dict[str, Any]:
        self.paused = True
        return {"paused": True}

    def resume(self) -> dict[str, Any]:
        self.paused = False
        return {"paused": False}

    def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
        observation_id = kwargs.get("observation_id")
        steps = kwargs.get("steps", [])
        if kind == "act":
            assert observation_id == self.status()["observation_id"]
            for step in steps:
                if step["keys"]:
                    self.presses.append(step["keys"])
                    self.page = min(self.page + 1, len(self.screens) - 1)
        self.index += 1
        return {
            "status": "completed",
            "operation_id": str(self.index),
            "observation_id": self.status()["observation_id"],
            "screenshot": str(self.screens[self.page]),
        }


@pytest.mark.parametrize("ending", ["closed", "choice"])
def test_dialogue_stops_without_retriggering_and_returns_pages(tmp_path: Path, ending: str) -> None:
    paths = [tmp_path / f"{i}.png" for i in range(3)]
    screen(paths[0], page=0)
    screen(paths[1], page=1)
    screen(paths[2], page=2, dialogue=ending == "choice", choice=ending == "choice")
    client = FakeClient(tmp_path, paths)
    result = VisualActions(client).dialogue("0")
    assert client.presses == [["B"], ["B"]]
    assert result["image_paths"] == [p.name for p in paths]
    assert result["dialogue"]["stop_reason"] == (
        "choice_or_overlay" if ending == "choice" else "dialogue_closed_or_unknown"
    )


def test_dialogue_refuses_choice_and_stops_repeated_page(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    screen(image, choice=True)
    client = FakeClient(tmp_path, [image])
    assert VisualActions(client).dialogue("0")["dialogue"]["advance_presses"] == 0
    screen(image)
    client = FakeClient(tmp_path, [image])
    result = VisualActions(client).dialogue("0")
    assert result["dialogue"]["stop_reason"] == "repeated_page"
    assert client.presses == [["B"]]


def test_dialogue_has_image_frame_and_expired_budget_limits(tmp_path: Path) -> None:
    paths = [tmp_path / f"{i}.png" for i in range(12)]
    for i, path in enumerate(paths):
        screen(path, page=i)
    client = FakeClient(tmp_path, paths)
    result = VisualActions(client).dialogue("0")
    assert len(result["image_paths"]) <= 8
    assert result["dialogue"]["frames_total"] <= 600
    assert len(client.presses) <= 7
    (tmp_path / "desktop-budget.json").write_text(json.dumps({"deadline": 0}))
    client = FakeClient(tmp_path, paths)
    with pytest.raises(GameError, match="时限"):
        VisualActions(client).dialogue("0")
    assert client.paused and not client.presses


def test_frame_rejects_unknown_size_and_waits_for_text(tmp_path: Path) -> None:
    p = tmp_path / "frame.png"
    screen(p)
    first = read_frame(p)
    screen(p, page=1)
    assert not first.similar(read_frame(p))
    Image.new("RGB", (480, 320)).save(p)
    with pytest.raises(GameError):
        read_frame(p)


def test_ready_menu_ignores_sprite_animation_but_not_menu_change(tmp_path: Path) -> None:
    path = tmp_path / "menu.png"
    im = Image.new("RGB", (240, 160), (100, 110, 90))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 114, 239, 159), fill=(115, 107, 132))
    draw.rectangle((5, 120, 234, 155), fill="white")
    im.save(path)
    first = read_frame(path)
    assert first.kind == "battle_menu"
    draw.rectangle((50, 70, 80, 100), fill="green")
    im.save(path)
    assert first.similar(read_frame(path))
    draw.rectangle((10, 125, 30, 134), fill="black")
    im.save(path)
    assert not first.similar(read_frame(path))


def test_small_map_animation_is_ready_but_scroll_is_not(tmp_path: Path) -> None:
    path = tmp_path / "world.png"
    im = Image.new("RGB", (240, 160), (100, 110, 90))
    draw = ImageDraw.Draw(im)
    draw.rectangle((20, 20, 70, 70), fill="green")
    im.save(path)
    first = read_frame(path)
    draw.rectangle((130, 120, 137, 127), fill="red")
    im.save(path)
    assert first.similar(read_frame(path))
    assert compare(first, read_frame(path))["status"] == "little_screen_change"
    draw.rectangle((20, 20, 70, 70), fill="blue")
    im.save(path)
    assert not first.similar(read_frame(path))
    assert compare(first, read_frame(path))["status"] == "screen_changed"


def test_directional_result_has_before_after_images_and_compact_history(tmp_path: Path) -> None:
    (tmp_path / "screens").mkdir()
    before = tmp_path / "screens" / ("0" * 32 + ".png")
    after = tmp_path / "screens" / "after.png"
    screen(before, dialogue=False)
    screen(after, dialogue=False)
    client = FakeClient(tmp_path, [before, after])
    client.hex_ids = True
    result = VisualActions(client).movement("0" * 32, [{"keys": ["UP"], "frames": 8}], "向上")
    assert result["image_paths"] == [before.name, after.name]
    assert result["visual"]["motion"]["status"] == "little_screen_change"
    assert "scene" not in result["visual"]["recent_attempts"][0]


def test_wait_only_releases_buttons_and_stops_on_ready_frame(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    screen(path)
    client = FakeClient(tmp_path, [path])
    result = VisualActions(client).movement(
        "0", [{"keys": [], "frames": 12}], "wait", wait_only=True
    )
    assert not client.presses
    assert result["visual"]["settled"]
    assert result["visual"]["frames_total"] == 36


def test_visual_smoke_restores_exploration_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.pokemon import visual_check

    path = tmp_path / "page.png"
    screen(path, dialogue=False)
    client = FakeClient(tmp_path, [path])
    client.paused = True
    history = tmp_path / "visual-history.json"
    history.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(visual_check, "GameClient", lambda root: client)
    report = visual_check.verify(tmp_path, movement=True)
    assert report["paused"]
    assert history.read_text(encoding="utf-8") == "[]\n"


def test_bookshelves_are_not_choices_and_battle_text_stops_at_menu(tmp_path: Path) -> None:
    paths = [tmp_path / f"{i}.png" for i in range(3)]
    screen(paths[0])
    with Image.open(paths[0]) as source:
        ImageDraw.Draw(source).rectangle((67, 66, 92, 75), fill="white")
        source.save(paths[0])
    assert read_frame(paths[0]).kind == "dialogue"
    im = Image.new("RGB", (240, 160), (100, 110, 90))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 114, 239, 159), fill=(206, 173, 74))
    draw.rectangle((6, 121, 233, 154), fill=(41, 82, 107))
    draw.rectangle((10, 125, 30, 134), fill="white")
    im.save(paths[1])
    assert read_frame(paths[1]).kind == "battle_text"
    draw.rectangle((0, 114, 239, 159), fill=(115, 107, 132))
    draw.rectangle((5, 120, 234, 155), fill="white")
    im.save(paths[2])

    class BattleClient(FakeClient):
        waits = 0

        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            if kind == "act" and self.page == 1:
                self.waits += 1
                if self.waits >= 8:
                    self.page = 2
            return super().request(kind, **kwargs)

    client = BattleClient(tmp_path, paths)
    result = VisualActions(client).dialogue("0")
    assert result["dialogue"]["advance_presses"] == 1
    assert client.presses == [["B"]]
    assert result["dialogue"]["stop_reason"] == "battle_menu"
    assert result["image_paths"] == [p.name for p in paths]


def test_narrow_yes_no_panel_stops_before_pressing_cancel(tmp_path: Path) -> None:
    path = tmp_path / "nurse.png"
    screen(path)
    with Image.open(path) as source:
        draw = ImageDraw.Draw(source)
        draw.rectangle((160, 70, 184, 107), fill="white")
        draw.rectangle((164, 77, 170, 83), fill="black")
        draw.rectangle((164, 94, 170, 100), fill="black")
        source.save(path)
    assert read_frame(path).kind == "choice_or_overlay"
    client = FakeClient(tmp_path, [path])
    result = VisualActions(client).dialogue("0")
    assert result["dialogue"]["advance_presses"] == 0
    assert result["dialogue"]["stop_reason"] == "choice_or_overlay"
    assert not client.presses


def test_repeated_older_page_preserves_intervening_content(tmp_path: Path) -> None:
    paths = [tmp_path / f"{i}.png" for i in range(3)]
    for path, page in zip(paths, [0, 1, 0], strict=True):
        screen(path, page=page)
    result = VisualActions(FakeClient(tmp_path, paths)).dialogue("0")
    assert result["image_paths"] == [path.name for path in paths]
    assert result["dialogue"]["stop_reason"] == "repeated_page"


def test_budget_expiry_returns_already_collected_pages(tmp_path: Path) -> None:
    paths = [tmp_path / f"{i}.png" for i in range(2)]
    for i, path in enumerate(paths):
        screen(path, page=i)

    class ExpiringClient(FakeClient):
        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            result = super().request(kind, **kwargs)
            if self.presses:
                (self.root / "desktop-budget.json").write_text('{"deadline":0}', encoding="utf-8")
            return result

    client = ExpiringClient(tmp_path, paths)
    result = VisualActions(client).dialogue("0")
    assert result["image_paths"] == [path.name for path in paths]
    assert result["paused"]
    assert result["dialogue"]["stop_reason"] == "budget_exhausted"
    assert client.presses == [["B"]]


def battle_screen(path: Path, *, menu: bool = False, arrow: bool = False, page: int = 0) -> None:
    im = Image.new("RGB", (240, 160), (100, 110, 90))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 114, 239, 159), fill=(115, 107, 132) if menu else (206, 173, 74))
    draw.rectangle((6, 121, 233, 154), fill="white" if menu else (41, 82, 107))
    draw.rectangle((10, 125, 30 + page * 5, 134), fill="black" if menu else "white")
    if arrow:
        for row in range(5):
            draw.line((210 + row, 145 + row, 218 - row, 145 + row), fill="red")
    im.save(path)


@pytest.mark.parametrize("ending", ["menu", "choice", "world", "dialogue"])
def test_confirm_text_collects_pages_and_never_selects_boundary(
    tmp_path: Path, ending: str
) -> None:
    paths = [tmp_path / f"{i}.png" for i in range(3)]
    for i in range(2):
        battle_screen(paths[i], arrow=True, page=i)
    if ending == "menu":
        battle_screen(paths[2], menu=True)
    else:
        screen(paths[2], dialogue=ending != "world", choice=ending == "choice")

    class PulseClient(FakeClient):
        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            for step in kwargs.get("steps", []):
                if step["keys"]:
                    assert step == {"keys": ["A"], "frames": 8}
            return super().request(kind, **kwargs)

    client = PulseClient(tmp_path, paths)
    result = VisualActions(client).confirm_text("0")
    assert client.presses == [["A"], ["A"]]
    assert result["image_paths"] == [p.name for p in paths]
    assert result["dialogue"]["advance_presses"] == 2
    assert (
        result["dialogue"]["stop_reason"]
        == {
            "menu": "battle_menu",
            "choice": "choice_or_overlay",
            "world": "visible_boundary",
            "dialogue": "visible_boundary",
        }[ending]
    )


def test_confirm_text_preserves_guards_and_stops_repeated_page(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    battle_screen(path, arrow=True)
    client = FakeClient(tmp_path, [path])
    with pytest.raises(GameError, match="新截图"):
        VisualActions(client).confirm_text("old")
    client.paused = True
    with pytest.raises(GameError, match="暂停"):
        VisualActions(client).confirm_text("0")
    client.paused = False
    result = VisualActions(client).confirm_text("0")
    assert result["dialogue"]["stop_reason"] == "repeated_page"
    assert client.presses == [["A"]]
    screen(path, choice=True)
    client = FakeClient(tmp_path, [path])
    assert VisualActions(client).confirm_text("0")["dialogue"]["stop_reason"] == "choice_or_overlay"
    assert not client.presses


def test_confirm_text_bounds_images_and_never_confirms_without_arrow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [tmp_path / f"{i}.png" for i in range(10)]
    for i, path in enumerate(paths):
        battle_screen(path, arrow=True, page=i)
    client = FakeClient(tmp_path, paths)
    result = VisualActions(client).confirm_text("0")
    assert result["dialogue"]["stop_reason"] == "image_budget"
    assert len(result["image_paths"]) <= 8 and len(client.presses) <= 7
    battle_screen(paths[0])
    clock = [0.0]
    monkeypatch.setattr("tools.pokemon.runtime.actions.time.monotonic", lambda: clock[0])

    class ClockClient(FakeClient):
        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            clock[0] += 0.5
            return super().request(kind, **kwargs)

    client = ClockClient(tmp_path, [paths[0]])
    result = VisualActions(client).confirm_text("0")
    assert result["dialogue"]["stop_reason"] == "text_unchanged"
    assert not client.presses


def test_gray_sign_dialogue_advances_once_and_stops_on_world(tmp_path: Path) -> None:
    sign, world = tmp_path / "sign.png", tmp_path / "world.png"
    screen(world, dialogue=False)
    with Image.open(world) as im:
        draw = ImageDraw.Draw(im)
        draw.rectangle((0, 114, 239, 159), fill=(206, 206, 222))
        draw.rectangle((3, 117, 236, 156), fill=(107, 115, 123))
        draw.rectangle((6, 120, 233, 153), fill="white")
        draw.rectangle((10, 125, 30, 134), fill="black")
        im.save(sign)
    assert read_frame(sign).kind == "dialogue"
    client = FakeClient(tmp_path, [sign, world])
    result = VisualActions(client).dialogue("0")
    assert client.presses == [["B"]]
    assert result["image_paths"] == [sign.name, world.name]
    assert result["dialogue"]["stop_reason"] == "dialogue_closed_or_unknown"


def test_party_hp_notice_is_not_a_battle_menu_or_auto_dialogue(tmp_path: Path) -> None:
    path = tmp_path / "party.png"
    battle_screen(path, menu=True)
    assert read_frame(path).kind == "battle_menu"
    with Image.open(path) as im:
        draw = ImageDraw.Draw(im)
        draw.rectangle((0, 0, 239, 54), fill=(74, 173, 165))
        draw.rectangle((0, 55, 239, 113), fill=(57, 140, 140))
        im.save(path)
    assert read_frame(path).kind == "other"
    client = FakeClient(tmp_path, [path])
    assert VisualActions(client).dialogue("0")["dialogue"]["advance_presses"] == 0
    assert not client.presses


def test_confirm_text_waits_for_exp_animation_before_pressing(tmp_path: Path) -> None:
    paths = [tmp_path / f"{i}.png" for i in range(7)]
    for i, path in enumerate(paths[:-1]):
        battle_screen(path, arrow=True)
        with Image.open(path) as im:
            ImageDraw.Draw(im).rectangle((140, 109, 150 + i * 8, 112), fill="cyan")
            im.save(path)
    screen(paths[-1], dialogue=False)

    class ExpClient(FakeClient):
        samples = 0

        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            steps = kwargs.get("steps", [])
            if kind == "act" and not self.presses:
                if any(step["keys"] for step in steps):
                    assert self.samples >= 10, "Do not press during EXP fill"
                else:
                    self.samples += 1
                    self.page = min(self.samples, 5)
            return super().request(kind, **kwargs)

    client = ExpClient(tmp_path, paths)
    result = VisualActions(client).confirm_text("0")
    assert client.presses == [["A"]]
    assert result["dialogue"]["stop_reason"] == "visible_boundary"


def test_confirm_text_returns_pages_and_pauses_when_budget_expires(tmp_path: Path) -> None:
    paths = [tmp_path / f"{i}.png" for i in range(2)]
    for i, path in enumerate(paths):
        battle_screen(path, arrow=True, page=i)

    class ExpiringClient(FakeClient):
        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            result = super().request(kind, **kwargs)
            if self.presses:
                (self.root / "desktop-budget.json").write_text('{"deadline":0}', encoding="utf-8")
            return result

    client = ExpiringClient(tmp_path, paths)
    result = VisualActions(client).confirm_text("0")
    assert result["dialogue"]["stop_reason"] == "budget_exhausted"
    assert result["paused"] and client.presses == [["A"]]
    assert result["image_paths"] == [p.name for p in paths]


def test_battle_wait_does_not_mistake_stable_text_for_decision(tmp_path: Path) -> None:
    paths = [tmp_path / f"{i}.png" for i in range(3)]
    for i, path in enumerate(paths):
        battle_screen(path, page=i, menu=i == 2)

    class AnimatedClient(FakeClient):
        ticks = 0

        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            if kind == "act":
                self.ticks += 1
                self.page = min(self.ticks // 8, 2)
            return super().request(kind, **kwargs)

    client = AnimatedClient(tmp_path, paths)
    result = VisualActions(client).movement("0", [{"keys": [], "frames": 12}], "", wait_only=True)
    assert not client.presses
    assert result["visual"]["decision_ready"]
    assert result["visual"]["stop_reason"] == "battle_menu"
    assert result["visual"]["frames_total"] >= 192
    assert result["image_paths"] == [p.name for p in paths]


def test_battle_confirmation_and_stuck_text_never_press_or_claim_ready(tmp_path: Path) -> None:
    path = tmp_path / "battle.png"
    battle_screen(path, arrow=True)
    client = FakeClient(tmp_path, [path])
    result = VisualActions(client).dialogue("0")
    assert result["dialogue"]["stop_reason"] == "battle_confirmation"
    assert not client.presses
    battle_screen(path)
    client = FakeClient(tmp_path, [path])
    result = VisualActions(client).movement("0", [{"keys": [], "frames": 12}], "", wait_only=True)
    assert not result["visual"]["decision_ready"]
    assert result["visual"]["frames_total"] <= MAX_BATTLE_FRAMES
    assert result["visual"]["stop_reason"] == "frame_budget"
    assert not client.presses


@pytest.mark.parametrize("ending", ["menu", "confirmation", "choice"])
def test_long_battle_wait_reaches_boundary_without_extra_input(tmp_path: Path, ending: str) -> None:
    paths = [tmp_path / "battle.png", tmp_path / "boundary.png"]
    battle_screen(paths[0])
    if ending == "choice":
        screen(paths[1], choice=True)
    else:
        battle_screen(paths[1], menu=ending == "menu", arrow=ending == "confirmation")

    class LongBattle(FakeClient):
        frames = 0

        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            if kind == "act":
                self.frames += sum(step["frames"] for step in kwargs["steps"])
                self.page = int(self.frames >= 660)
            return super().request(kind, **kwargs)

    client = LongBattle(tmp_path, paths)
    result = VisualActions(client).movement("0", [{"keys": [], "frames": 12}], "", wait_only=True)
    assert not client.presses
    assert 600 < result["visual"]["frames_total"] <= MAX_BATTLE_FRAMES
    assert result["visual"]["decision_ready"]
    assert (
        result["visual"]["stop_reason"]
        == {
            "menu": "battle_menu",
            "confirmation": "battle_confirmation",
            "choice": "choice_or_overlay",
        }[ending]
    )


def test_battle_extension_keeps_time_and_button_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "battle.png"
    battle_screen(path)
    clock = [0.0]
    monkeypatch.setattr("tools.pokemon.runtime.actions.time.monotonic", lambda: clock[0])

    class SlowBattle(FakeClient):
        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            if kind == "act":
                clock[0] += 7
            return super().request(kind, **kwargs)

    client = SlowBattle(tmp_path, [path])
    runner = VisualActions(client)
    result = runner.movement("0", [{"keys": [], "frames": 12}], "", wait_only=True)
    assert result["visual"]["stop_reason"] == "macro_time_limit"
    assert not result["visual"]["decision_ready"]
    assert not client.presses
    assert runner.frame_limit == MAX_BATTLE_FRAMES
    clock[0] = 0
    runner.frames = 600
    with pytest.raises(GameError, match="总帧数"):
        runner.act(result, [{"keys": ["A"], "frames": 8}])
    assert not client.presses


def test_small_framed_choice_and_battle_overlay_are_stop_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "choice.png"
    for battle in (False, True):
        if battle:
            battle_screen(path)
        else:
            screen(path)
        with Image.open(path) as im:
            draw = ImageDraw.Draw(im)
            draw.rectangle((155, 65, 174, 94), fill=(82, 82, 82))
            draw.rectangle((158, 68, 171, 91), fill="white")
            im.save(path)
        client = FakeClient(tmp_path, [path])
        result = VisualActions(client).dialogue("0")
        assert result["dialogue"]["stop_reason"] == "choice_or_overlay"
        assert not client.presses


@pytest.mark.parametrize("explicit", [False, True])
def test_changed_directional_scene_only_pairs_when_requested(
    tmp_path: Path, explicit: bool
) -> None:
    (tmp_path / "screens").mkdir()
    before = tmp_path / "screens" / ("0" * 32 + ".png")
    after = tmp_path / "screens" / "after.png"
    screen(before, dialogue=False)
    screen(after, dialogue=False)
    with Image.open(after) as im:
        ImageDraw.Draw(im).rectangle((20, 20, 100, 100), fill="blue")
        im.save(after)
    client = FakeClient(tmp_path, [before, after])
    client.hex_ids = True
    result = VisualActions(client).movement(
        "0" * 32, [{"keys": ["UP"], "frames": 8}], "", compare_images=explicit
    )
    assert result["image_paths"] == ([before.name, after.name] if explicit else [after.name])
    assert result["visual"]["before_image"] == before.name


@pytest.mark.parametrize("battle", [False, True])
def test_arrow_shadow_on_first_line_is_stable_without_hiding_changed_text(
    tmp_path: Path, battle: bool
) -> None:
    path = tmp_path / "arrow.png"
    frames = []
    for y in (123, 125):
        if battle:
            battle_screen(path)
        else:
            screen(path)
        with Image.open(path) as im:
            draw = ImageDraw.Draw(im)
            shadow = (41, 49, 49) if battle else (99, 99, 99)
            for row in range(5):
                draw.line((64 + row, y + row + 1, 73 - row, y + row + 1), fill=shadow)
            for row in range(5):
                draw.line((64 + row, y + row, 72 - row, y + row), fill=(231, 8, 8))
            im.save(path)
        frames.append(read_frame(path))
    assert all(frame.advance_arrow for frame in frames)
    assert frames[0].similar(frames[1])
    with Image.open(path) as im:
        ImageDraw.Draw(im).rectangle((100, 125, 105, 132), fill="red")
        im.save(path)
    assert not frames[1].similar(read_frame(path))


def test_battle_flash_is_not_a_framed_choice(tmp_path: Path) -> None:
    path = tmp_path / "flash.png"
    battle_screen(path)
    with Image.open(path) as im:
        ImageDraw.Draw(im).rectangle((0, 0, 239, 113), fill="white")
        im.save(path)
    assert read_frame(path).kind == "battle_text"


def test_area_intro_waits_for_playfield_and_does_not_match_normal_title(tmp_path: Path) -> None:
    intro, world = tmp_path / "intro.png", tmp_path / "world.png"
    screen(world, dialogue=False)
    with Image.open(world) as im:
        draw = ImageDraw.Draw(im)
        draw.rectangle((0, 0, 108, 23), fill=(247, 247, 255))
        draw.rectangle((0, 20, 108, 23), fill=(57, 82, 115))
        im.save(intro)
        assert read_frame(intro).kind == "other"
        draw.rectangle((112, 0, 239, 23), fill="black")
        im.save(intro)
    assert read_frame(intro).kind == "transition"
    assert not read_frame(intro).similar(read_frame(intro))

    class IntroClient(FakeClient):
        samples = 0

        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            if kind == "act":
                self.samples += 1
                self.page = int(self.samples >= 6)
            return super().request(kind, **kwargs)

    client = IntroClient(tmp_path, [intro, world])
    result = VisualActions(client).movement("0", [{"keys": [], "frames": 12}], "", wait_only=True)
    assert result["screenshot"] == str(world)
    assert result["visual"]["decision_ready"]
    assert client.samples >= 8 and not client.presses


@pytest.mark.parametrize("vanishes", [False, True])
def test_choice_waits_for_text_but_never_resumes_dialogue(tmp_path: Path, vanishes: bool) -> None:
    paths = [tmp_path / f"{i}.png" for i in range(3)]
    for i, path in enumerate(paths):
        screen(path, page=i, choice=True)
    if vanishes:
        screen(paths[1], dialogue=False)

    class TypingChoice(FakeClient):
        samples = 0

        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            if kind == "act":
                self.samples += 1
                self.page = 1 if vanishes else min(self.samples // 2, 2)
            return super().request(kind, **kwargs)

    client = TypingChoice(tmp_path, paths)
    result = VisualActions(client).dialogue("0")
    assert result["dialogue"]["stop_reason"] == "choice_or_overlay"
    assert result["dialogue"]["advance_presses"] == 0 and not client.presses
    assert result["screenshot"] == str(paths[1 if vanishes else 2])
    if not vanishes:
        assert client.samples >= 6


@pytest.mark.parametrize("blank", [False, True])
def test_long_unchanged_battle_text_is_uncertain_and_never_auto_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blank: bool
) -> None:
    path = tmp_path / "battle.png"
    battle_screen(path)
    if blank:
        with Image.open(path) as im:
            ImageDraw.Draw(im).rectangle((8, 121, 231, 154), fill=(41, 82, 107))
            im.save(path)
    clock = [0.0]
    monkeypatch.setattr("tools.pokemon.runtime.actions.time.monotonic", lambda: clock[0])

    class ClockClient(FakeClient):
        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            if kind == "act":
                clock[0] += 0.5
            return super().request(kind, **kwargs)

    client = ClockClient(tmp_path, [path])
    result = VisualActions(client).movement("0", [{"keys": [], "frames": 12}], "", wait_only=True)
    assert result["visual"]["stop_reason"] == ("macro_time_limit" if blank else "text_unchanged")
    assert not result["visual"]["decision_ready"] and not client.presses


def test_automatic_battle_text_can_stay_visible_six_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [tmp_path / f"{i}.png" for i in range(3)]
    for i, path in enumerate(paths):
        battle_screen(path, menu=i == 2, page=i)
    clock = [0.0]
    monkeypatch.setattr("tools.pokemon.runtime.actions.time.monotonic", lambda: clock[0])

    class ClockClient(FakeClient):
        frames = 0

        def request(self, kind: str, **kwargs: Any) -> dict[str, Any]:
            if kind == "act":
                clock[0] += 0.25
                self.frames += sum(step["frames"] for step in kwargs["steps"])
                self.page = 2 if self.frames >= 660 else int(self.frames >= 300)
            return super().request(kind, **kwargs)

    client = ClockClient(tmp_path, paths)
    result = VisualActions(client).movement("0", [{"keys": [], "frames": 12}], "", wait_only=True)
    assert result["visual"]["stop_reason"] == "battle_menu"
    assert result["visual"]["frames_total"] > 600 and not client.presses
