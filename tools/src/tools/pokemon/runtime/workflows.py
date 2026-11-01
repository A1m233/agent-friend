"""Replay only player-demonstrated actions, guarded by visible image regions."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw

from .actions import MAX_IMAGES, VisualActions
from .client import GameClient
from .knowledge import TOKEN, Knowledge, require_settled_controller, screenshot
from .protocol import GameError, atomic_json, controller_lock, steps_wire
from .vision import party_palette, read_frame

KINDS = {"inspect", "item", "switch", "move", "route"}


def inspection_layout(path: Path) -> str | None:
    """Only the two already observed overview layouts; never infer menu meaning."""
    with Image.open(path) as source:
        if source.size != (240, 160):
            return None
        image = source.convert("RGB")
        colors = Counter(zip(*[iter(image.crop((0, 0, 240, 114)).tobytes())] * 3, strict=True))
        if party_palette(colors[(74, 173, 165)], colors[(57, 140, 140)]):
            return "party"
        right = Counter(zip(*[iter(image.crop((170, 0, 240, 118)).tobytes())] * 3, strict=True))
        bottom = Counter(zip(*[iter(image.crop((0, 120, 240, 160)).tobytes())] * 3, strict=True))
        if (
            right[(255, 255, 255)] >= 4500
            and right[(115, 107, 132)] >= 500
            and bottom[(0, 123, 198)] >= 6000
        ):
            return "main_menu"
    return None


def inspection_matches(actual: Path, reference: Path, box: list[int], *, exiting: bool) -> bool:
    if box == [0, 0, 240, 160]:
        layout = inspection_layout(reference)
        if layout is not None and layout == inspection_layout(actual):
            if layout == "main_menu":
                # Include every menu label and the cursor, exclude the world behind it.
                box = [174, 4, 236, 114]
            elif exiting:
                # For overview output or B-only exit, HP/names need not match old values.
                # Selecting a member still uses the full view, including its selection.
                box = [0, 130, 240, 160]
    return matches(actual, reference, box)


def region(value: object) -> tuple[int, int, int, int]:
    if not isinstance(value, list) or len(value) != 4 or any(type(v) is not int for v in value):
        raise GameError("invalid_region", "region 是原图坐标 [left,top,right,bottom]。")
    left, top, right, bottom = value
    if (
        not (0 <= left < right <= 240 and 0 <= top < bottom <= 160)
        or (right - left) * (bottom - top) < 2400
    ):
        raise GameError("invalid_region", "检查区域需在 240×160 原图内，至少 2400 像素。")
    return left, top, right, bottom


def matches(actual: Path, reference: Path, box: list[int]) -> bool:
    crop = region(box)
    with Image.open(actual) as a, Image.open(reference) as b:
        if a.size != (240, 160) or b.size != (240, 160):
            return False
        left, right = a.convert("RGB").crop(crop), b.convert("RGB").crop(crop)
        # Nearly exact, not a perceptual hash: a moved cursor must fail the guard.
        values = ImageChops.difference(left, right).tobytes()
        changed = sum(max(values[i : i + 3]) > 8 for i in range(0, len(values), 3))
        return changed <= (left.width * left.height) * 0.001


def move_matches(actual: Path, reference: Path, box: list[int]) -> bool:
    """Match demonstrated battle controls, not changing HP, PP or opponent sprites."""
    if box != [0, 0, 240, 160]:
        return matches(actual, reference, box)
    if any(read_frame(path).kind != "battle_menu" for path in (actual, reference)):
        return matches(actual, reference, box)
    views: list[tuple[str, bytes, bytes]] = []
    for path in (actual, reference):
        with Image.open(path) as source:
            image = source.convert("RGB")
            pixels = image.crop((4, 118, 116, 155)).tobytes()
            left = Counter(zip(*[iter(pixels)] * 3, strict=True))
            if left[(41, 82, 107)] >= 2500:
                layout = "commands"
            elif left[(255, 255, 255)] >= 2500:
                layout = "moves"
            else:
                return matches(actual, reference, box)
            # Preserve the active player's visible name/gender, every move label,
            # type, panel and cursor. Exclude the PP row, including the label's
            # yellow/red low-PP tint; the unmodified output still shows it.
            # The active status panel bobs vertically by a pixel. Align on the
            # first row of name ink, preserving the name and gender themselves.
            ink_rows = [
                y
                for y in range(77, 89)
                if any(image.getpixel((x, y)) == (66, 66, 66) for x in range(141, 199))
            ]
            if not ink_rows:
                return matches(actual, reference, box)
            top = ink_rows[0]
            identity = image.crop((141, top - 1, 199, top + 8)).tobytes()
            if layout == "moves":
                ImageDraw.Draw(image).rectangle((164, 122, 235, 135), fill="white")
            views.append((layout, identity, image.crop((0, 114, 240, 160)).tobytes()))
    return views[0] == views[1]


def save_workflow(root: Path, value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"id", "kind", "intent", "stages"}:
        raise GameError("invalid_workflow", "需要 id、kind、intent、stages。")
    name, kind, intent, stages = (value[k] for k in ("id", "kind", "intent", "stages"))
    if (
        not isinstance(name, str)
        or not TOKEN.fullmatch(name)
        or not isinstance(kind, str)
        or kind not in KINDS
    ):
        raise GameError(
            "invalid_workflow", "id 使用稳定英文名；kind 为 inspect/item/switch/move/route。"
        )
    if not isinstance(intent, str) or not 1 <= len(intent) <= 200:
        raise GameError("invalid_workflow", "intent 必须说明观察目标、道具与对象、招式或起终点。")
    if not isinstance(stages, list) or not 1 <= len(stages) <= 7:
        raise GameError("workflow_limit", "一段流程最多 7 个已有 act/wait 调用；较长流程分段保存。")
    compiled: list[dict[str, Any]] = []
    total = 0
    for i, stage in enumerate(stages):
        if (
            not isinstance(stage, dict)
            or set(stage) - {"to", "region", "end_region"}
            or "to" not in stage
        ):
            raise GameError("invalid_stage", "每步指定 to 截图编号，可选 region/end_region。")
        screenshot(root, stage["to"])
        path = root / "skill-traces" / f"{stage['to']}.json"
        if not path.is_file():
            raise GameError(
                "missing_demonstration", "没有该截图对应的个人 skill act/wait 成功记录。"
            )
        trace = json.loads(path.read_text(encoding="utf-8"))
        screenshot(root, trace["from"])
        if i and trace["from"] != compiled[-1]["to"]:
            raise GameError("discontinuous_workflow", "步骤必须是连续成功操作，不能跳过中间按键。")
        knowledge = Knowledge(root).load()
        if trace["timeline"] not in {
            knowledge.get("timeline", "initial"),
            *knowledge.get("experience_timelines", []),
        }:
            raise GameError("old_timeline", "不能从已回退的时间线录制流程。")
        steps_wire(trace["steps"])
        total += sum(s["frames"] for s in trace["steps"])
        if kind == "route":
            if sum(bool(s["keys"]) for s in trace["steps"]) > 1:
                raise GameError("route_input", "路线每个检查点只录制一次方向按住，可附松键等待。")
            if any(set(s["keys"]) - {"UP", "DOWN", "LEFT", "RIGHT", "B"} for s in trace["steps"]):
                raise GameError("route_input", "路线只复用方向和跑步，不包含交互、菜单或战斗决定。")
            if any(s["keys"] and s["frames"] > 32 for s in trace["steps"]):
                raise GameError("route_input", "路线单段按键最多 32 帧，录制时增加可见检查点。")
        before = stage.get("region", [0, 0, 240, 160])
        after = stage.get("end_region", [0, 0, 240, 160])
        region(before)
        region(after)
        if kind == "route" and (before != [0, 0, 240, 160] or after != before):
            raise GameError("route_region", "路线检查全图，不能通过裁掉角色或障碍物绕过位置检查。")
        compiled.append({**trace, "region": before, "end_region": after})
    if total > 400:
        raise GameError("workflow_limit", "已录制输入超过 400 帧，请分段；余量留给无按键等待。")
    if kind == "inspect" and not inspection_matches(
        screenshot(root, compiled[0]["from"]),
        screenshot(root, compiled[-1]["to"]),
        [0, 0, 240, 160],
        exiting=False,
    ):
        raise GameError(
            "inspection_not_returned", "inspect 录制需包含返回原界面；否则拆成普通 act 操作。"
        )
    with controller_lock(root):
        require_settled_controller(root)
        data = Knowledge(root).load()
        if name not in data["workflows"] and len(data["workflows"]) >= 64:
            raise GameError("workflow_limit", "最多 64 个流程；使用原 ID 更新已有流程。")
        data["workflows"][name] = {"kind": kind, "intent": intent, "stages": compiled}
        data["revision"] += 1
        atomic_json(root / "knowledge.json", data)
    return {
        "status": "recorded",
        "workflow": name,
        "stages": len(compiled),
        "revision": data["revision"],
    }


def replay(client: GameClient, name: str, observation: str, kind: str) -> dict[str, Any]:
    workflow = Knowledge(client.root).load()["workflows"].get(name)
    if not workflow or workflow["kind"] != kind:
        raise GameError("workflow_missing", "先手动走通并 workflow-save；不能猜一个未录制的流程。")
    actions = VisualActions(client)
    current = actions.fresh_observation(observation)
    images: list[str] = []
    completed = 0
    reason = "completed"
    try:
        for stage in workflow["stages"]:
            if len(images) >= MAX_IMAGES - 1:
                reason = "image_budget"
                break
            current_path = Path(current["screenshot"])
            frame = read_frame(current_path)
            if kind == "route" and frame.kind != "other":
                reason = "route_interrupted"
                break
            reference = screenshot(client.root, stage["from"])
            before_matches = (
                inspection_matches(
                    current_path,
                    reference,
                    stage["region"],
                    exiting=all(not s["keys"] or s["keys"] == ["B"] for s in stage["steps"]),
                )
                if kind == "inspect"
                else move_matches(current_path, reference, stage["region"])
                if kind == "move"
                else matches(current_path, reference, stage["region"])
            )
            if not before_matches:
                reason = "start_or_cursor_mismatch"
                break
            # Keep menu decisions as separate taps, with an observation after each
            # recorded stage. Never select a new target or repeat a failed stage.
            current = actions.act(current, stage["steps"])
            actions.choice_seen = False
            if kind == "route":
                current, _, stable = actions.settle(current, 60)
                images.append(Path(current["screenshot"]).name)
            else:
                current, _, stable, pages, _ = actions.decision(
                    current, max_images=MAX_IMAGES - len(images)
                )
                images.extend(pages)
            reference = screenshot(client.root, stage["to"])
            after_matches = (
                inspection_matches(
                    Path(current["screenshot"]), reference, stage["end_region"], exiting=True
                )
                if kind == "inspect"
                else move_matches(Path(current["screenshot"]), reference, stage["end_region"])
                if kind == "move"
                else matches(Path(current["screenshot"]), reference, stage["end_region"])
            )
            if not stable or not after_matches:
                reason = "result_mismatch"
                break
            completed += 1
    except GameError as exc:
        if exc.code not in {"macro_time_limit", "macro_frame_limit", "budget_exhausted"}:
            raise
        current = actions.latest or current
        reason = exc.code
    final = Path(current["screenshot"]).name
    if not images or images[-1] != final:
        images.append(final)
    return {
        **current,
        "image_paths": images[-8:],
        "workflow": {
            "id": name,
            "kind": kind,
            "intent": workflow["intent"],
            "completed_stages": completed,
            "stop_reason": reason,
            "restored_start_ui": kind == "inspect" and reason == "completed",
        },
        "record_update": "根据返回图确认实际结果后 knowledge 局部更新；按键完成不等于道具使用成功。",
    }


def trace_action(
    root: Path, source: str, result: dict[str, Any], steps: list[dict[str, Any]]
) -> None:
    if result.get("status") != "completed" or result.get("input_applied") is False:
        return
    destination = result["observation_id"]
    atomic_json(
        root / "skill-traces" / f"{destination}.json",
        {
            "from": source,
            "to": destination,
            "steps": steps,
            "timeline": Knowledge(root).load().get("timeline", "initial"),
        },
    )
