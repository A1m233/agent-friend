"""Bounded visual actions. Every underlying command keeps the durable protocol."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .client import GameClient
from .motion import compare, scene_difference
from .protocol import GameError, atomic_json
from .vision import Frame, read_frame

MAX_FRAMES = 600
MAX_BATTLE_FRAMES = 1200
MAX_SECONDS = 20
MAX_IMAGES = 8
SAMPLE_FRAMES = 12
TEXT_IDLE_SECONDS = 8.0


class VisualActions:
    def __init__(self, client: GameClient) -> None:
        self.client = client
        self.frames = 0
        self.frame_limit = MAX_FRAMES
        self.choice_seen = False
        self.started = time.monotonic()
        self.latest: dict[str, Any] | None = None

    def check(self) -> None:
        if time.monotonic() - self.started >= MAX_SECONDS:
            raise GameError("macro_time_limit", "本次视觉操作达到 20 秒上限；先观察，不重放。")
        budget = self.client.root / "desktop-budget.json"
        if (
            budget.exists()
            and time.time() >= json.loads(budget.read_text(encoding="utf-8"))["deadline"]
        ):
            self.client.pause()
            raise GameError("budget_exhausted", "试玩时限已到，已暂停；不能继续按键。")

    def act(self, result: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
        self.check()
        frames = sum(step["frames"] for step in steps)
        # A longer battle settlement may only release keys, never extend input.
        limit = MAX_FRAMES if any(step["keys"] for step in steps) else self.frame_limit
        if self.frames + frames > limit:
            raise GameError("macro_frame_limit", "本次操作总帧数达到上限；使用新画面继续。")
        self.frames += frames
        new = self.client.request("act", observation_id=result["observation_id"], steps=steps)
        if new["status"] != "completed":
            raise GameError("macro_interrupted", "操作已中断；保持停止，先 observe 对账，不重放。")
        self.latest = new
        return new

    def settle(
        self, result: dict[str, Any], limit: int = 180, *, confirmation_scene_stable: bool = False
    ) -> tuple[dict[str, Any], Frame, bool]:
        previous = read_frame(Path(result["screenshot"]))
        self.choice_seen |= previous.kind == "choice_or_overlay"
        stable = 0
        for _ in range(min(limit, self.frame_limit - self.frames) // SAMPLE_FRAMES):
            result = self.act(result, [{"keys": [], "frames": SAMPLE_FRAMES}])
            current = read_frame(Path(result["screenshot"]))
            self.choice_seen |= current.kind == "choice_or_overlay"
            if self.choice_seen and current.kind != "choice_or_overlay":
                return result, current, False
            # Choice borders appear before their explanatory text finishes.
            # Only wait with released keys; remember even a transient choice.
            same = (
                previous.kind == current.kind and previous.text_pixels == current.text_pixels
                if current.kind == "choice_or_overlay"
                else current.similar(previous)
            )
            confirmation = (
                confirmation_scene_stable
                and current.kind == "battle_text"
                and current.advance_arrow
            )
            if confirmation:
                # An arrow can coexist with a moving EXP bar. Ignore arrow
                # animation, but require the rest of the scene to settle too.
                same = previous.advance_arrow and current.signature == previous.signature
            stable = stable + 1 if same else 0
            if stable >= (5 if confirmation else 2):
                return result, current, True
            previous = current
        return result, previous, False

    def decision(
        self,
        result: dict[str, Any],
        limit: int = 180,
        *,
        max_images: int = MAX_IMAGES,
        confirmation_scene_stable: bool = False,
    ) -> tuple[dict[str, Any], Frame, bool, list[str], str]:
        """Wait without keys through battle narration; never select or confirm."""
        pages: list[str] = []
        signatures: set[bytes] = set()
        frame = read_frame(Path(result["screenshot"]))
        if frame.kind == "battle_text":
            self.frame_limit = MAX_BATTLE_FRAMES
        stable = False
        reason = "frame_budget"
        last_text: bytes | None = None
        text_changed_at: float | None = None
        try:
            while self.frames + SAMPLE_FRAMES <= self.frame_limit:
                result, frame, stable = self.settle(
                    result, limit, confirmation_scene_stable=confirmation_scene_stable
                )
                if frame.kind == "battle_text":
                    self.frame_limit = MAX_BATTLE_FRAMES
                if self.choice_seen or frame.kind == "choice_or_overlay":
                    reason = "choice_or_overlay"
                    break
                if not stable:
                    reason = (
                        "frame_budget"
                        if self.frames + SAMPLE_FRAMES > self.frame_limit
                        else "unstable"
                    )
                    break
                if frame.kind != "battle_text":
                    reason = "battle_menu" if frame.kind == "battle_menu" else "visible_boundary"
                    break
                if frame.text_pixels not in signatures:
                    if len(pages) >= max_images - 1:
                        reason = "image_budget"
                        break
                    pages.append(Path(result["screenshot"]).name)
                    signatures.add(frame.text_pixels)
                if frame.advance_arrow:
                    reason = "battle_confirmation"
                    break
                now = time.monotonic()
                if frame.text_ink_pixels < 40:
                    last_text, text_changed_at = None, None
                elif frame.text_pixels != last_text:
                    last_text, text_changed_at = frame.text_pixels, now
                elif text_changed_at is not None and now - text_changed_at >= TEXT_IDLE_SECONDS:
                    # No arrow is not proof of automatic progress. Hand unchanged
                    # text back as uncertain, never press A or claim decision-ready.
                    reason = "text_unchanged"
                    break
                if self.frames + SAMPLE_FRAMES > self.frame_limit:
                    break
                result = self.act(result, [{"keys": [], "frames": SAMPLE_FRAMES}])
        except GameError as exc:
            if exc.code not in {"macro_time_limit", "budget_exhausted"}:
                raise
            reason = exc.code
            result = self.latest or result
            frame = read_frame(Path(result["screenshot"]))
            stable = False
        final = Path(result["screenshot"]).name
        if not pages or pages[-1] != final:
            pages.append(final)
        return result, frame, stable, pages, reason

    def movement(
        self,
        observation_id: str,
        steps: list[dict[str, Any]],
        expectation: str,
        *,
        wait_only: bool = False,
        compare_images: bool = False,
    ) -> dict[str, Any]:
        before_path = (
            self.client.root / "screens" / f"{observation_id}.png"
            if re.fullmatch(r"[a-f0-9]{32}", observation_id)
            else None
        )
        before = (
            read_frame(before_path) if before_path is not None and before_path.is_file() else None
        )
        result = self.act({"observation_id": observation_id}, steps)
        result, frame, stable, paths, reason = self.decision(
            result, MAX_FRAMES if wait_only else 180
        )
        history_path = self.client.root / "visual-history.json"
        history = (
            json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
        )
        repeats = sum(
            scene_difference(bytes(item["scene"]), frame.scene_pixels) <= 6
            if frame.kind == "other" and item.get("kind") == "other" and "scene" in item
            else item["signature"] == frame.signature
            for item in history[-3:]
        )
        history.append(
            {
                "signature": frame.signature,
                "kind": frame.kind,
                "scene": list(frame.scene_pixels),
                "steps": steps,
                "expectation": expectation,
            }
        )
        atomic_json(history_path, history[-3:])
        directional = any(set(step["keys"]) & {"UP", "DOWN", "LEFT", "RIGHT"} for step in steps)
        motion = compare(before, frame) if directional else {"status": "not_directional"}
        paired = directional and (
            compare_images or repeats > 0 or motion["status"] == "little_screen_change"
        )
        comparison_included = False
        if paired and before is not None and before_path is not None and len(paths) < MAX_IMAGES:
            paths.insert(0, before_path.name)
            comparison_included = True
        return {
            **result,
            "image_paths": paths,
            "visual": {
                "kind": frame.kind,
                "settled": stable,
                "decision_ready": reason
                in {"battle_menu", "battle_confirmation", "choice_or_overlay", "visible_boundary"},
                "stop_reason": reason,
                "frames_total": self.frames,
                "frame_limit": self.frame_limit,
                "repeat_matches_recent": repeats,
                "recent_attempts": [
                    {"steps": item["steps"], "expectation": item["expectation"]}
                    for item in history[-3:]
                ],
                "motion": motion,
                "before_image": before_path.name if before_path is not None else None,
                "comparison_included": comparison_included,
                "image_order": "图片按时间排列；仅需要对照时附操作前图；末图为当前，战斗中间图为已出现文字。",
                "instruction": "相同画面不证明动作未执行；核对预期，重复无进展时缩短动作并换一种验证。",
            },
        }

    def fresh_observation(self, observation_id: str) -> dict[str, Any]:
        self.check()
        state = self.client.status()
        if state.get("observation_id") != observation_id:
            raise GameError("stale_observation", "先看新截图，再用对应编号推进对话。")
        if state["paused"]:
            raise GameError("paused", "游戏已暂停；用户明确恢复后再 begin。")
        # Observe again: game animations may have advanced while the model thought.
        return self.client.request("observe")

    def confirm_text(self, observation_id: str) -> dict[str, Any]:
        """Confirm recognized battle arrows only; all decisions remain with AF."""
        result = self.fresh_observation(observation_id)
        pages: list[str] = []
        confirmed: set[bytes] = set()
        presses = 0
        reason = "frame_budget"
        try:
            while True:
                result, frame, _, batch, reason = self.decision(
                    result,
                    max_images=MAX_IMAGES - len(pages),
                    confirmation_scene_stable=True,
                )
                pages.extend(batch)
                if reason != "battle_confirmation":
                    break
                if frame.text_pixels in confirmed:
                    reason = "repeated_page"
                    break
                if len(pages) >= MAX_IMAGES - 1:
                    reason = "image_budget"
                    break
                if self.frames + 32 > MAX_FRAMES:
                    reason = "frame_budget"
                    break
                confirmed.add(frame.text_pixels)
                # Use the same bounded pulse as manual tap. One-frame pulses
                # missed confirmed stationary pages in the real emulator.
                result = self.act(
                    result, [{"keys": ["A"], "frames": 8}, {"keys": [], "frames": 24}]
                )
                presses += 1
        except GameError as exc:
            if exc.code not in {"macro_time_limit", "budget_exhausted"}:
                raise
            reason = exc.code
            result = self.latest or result
        final = Path(result["screenshot"]).name
        if not pages or pages[-1] != final:
            pages.append(final)
        return {
            **result,
            "image_paths": pages,
            "paused": self.client.status()["paused"],
            "dialogue": {
                "stop_reason": reason,
                "advance_button": "A",
                "advance_presses": presses,
                "frames_total": self.frames,
                "frame_limit": self.frame_limit,
                "page_images": len(pages),
                "instruction": "只确认已识别的蓝底文字箭头；末图为当前，选择和未知页面由你决定。",
            },
        }

    def dialogue(self, observation_id: str) -> dict[str, Any]:
        result = self.fresh_observation(observation_id)
        pages: list[str] = []
        signatures: set[str] = set()
        last_signature = None
        presses = 0
        reason = "frame_budget"
        try:
            while self.frames + 36 <= MAX_FRAMES:
                result, frame, stable = self.settle(result)
                if self.choice_seen:
                    reason = "choice_or_overlay"
                    break
                if frame.kind == "battle_text":
                    if len(pages) >= MAX_IMAGES - 1:
                        reason = "image_budget"
                        break
                    result, frame, stable, battle_pages, reason = self.decision(
                        result, max_images=MAX_IMAGES - len(pages)
                    )
                    # This path only waits: a confirmation or menu belongs to AF.
                    pages.extend(battle_pages)
                    break
                if frame.kind not in {"dialogue", "battle_text"}:
                    reason = (
                        "choice_or_overlay"
                        if frame.kind in {"choice_or_overlay", "battle_menu"}
                        else "dialogue_closed_or_unknown"
                    )
                    break
                if not stable:
                    reason = "unstable"
                    break
                signature = frame.text_pixels.hex()
                if signature in signatures:
                    reason = "repeated_page"
                    break
                # Reserve a slot for the final screen, even when a menu appears next.
                if len(pages) >= MAX_IMAGES - 1:
                    reason = "image_budget"
                    break
                pages.append(Path(result["screenshot"]).name)
                last_signature = signature
                signatures.add(signature)
                if self.frames + 36 > MAX_FRAMES:
                    break
                # B advances ordinary text but doesn't initiate a fresh NPC interaction.
                result = self.act(
                    result, [{"keys": ["B"], "frames": 8}, {"keys": [], "frames": 24}]
                )
                presses += 1
        except GameError as exc:
            if exc.code not in {"macro_time_limit", "budget_exhausted"}:
                raise
            reason = exc.code
            result = self.latest or result
        final = Path(result["screenshot"]).name
        if pages and final == pages[-1]:
            pass
        elif pages and reason == "repeated_page" and signature == last_signature:
            pages[-1] = final
        else:
            pages.append(final)
        return {
            **result,
            "image_paths": pages,
            "paused": self.client.status()["paused"],
            "dialogue": {
                "stop_reason": reason,
                "advance_button": "B",
                "advance_presses": presses,
                "frames_total": self.frames,
                "frame_limit": self.frame_limit,
                "page_images": len(pages),
                "recognizer": "firered_decision_boundaries_v3",
                "ocr_used": False,
                "instruction": "图片按时间排列，最后一张是当前画面；状态不确定或有选项时由你决定，不能自动重放。",
            },
        }
