"""Screen-only change evidence; never claims player coordinates or walkability."""

from __future__ import annotations

from typing import Any

from .vision import Frame


def scene_difference(left: bytes, right: bytes) -> int:
    if len(left) != 1800 or len(right) != 1800:
        return 600
    return sum(
        max(abs(a - b) for a, b in zip(left[i : i + 3], right[i : i + 3], strict=True)) > 20
        for i in range(0, 1800, 3)
    )


def compare(before: Frame | None, after: Frame) -> dict[str, Any]:
    if (
        before is None
        or before.blank
        or after.blank
        or before.kind != "other"
        or after.kind != "other"
    ):
        return {"status": "not_comparable", "meaning": "场景切换或非地图候选；根据原图判断。"}
    changed = scene_difference(before.scene_pixels, after.scene_pixels)
    result: dict[str, Any] = {
        "status": "little_screen_change" if changed <= 6 else "screen_changed",
        "changed_cells": changed,
        "total_cells": 600,
        "meaning": "只比较画面，不代表角色移动成功、碰撞或可通行；结合前后图核对。",
    }

    # Estimate scrolling from coarse color patches. Repeating terrain is ambiguous.
    def score(dx: int, dy: int) -> float:
        matched = total = 0
        for y in range(4, 16):
            for x in range(4, 26):
                if 11 <= x <= 18 and 5 <= y <= 14:
                    continue  # usual player/sprite region, not a world coordinate
                bx, by = x - dx, y - dy
                if not (0 <= bx < 30 and 0 <= by < 20):
                    continue
                i, j = (y * 30 + x) * 3, (by * 30 + bx) * 3
                total += 1
                matched += (
                    max(
                        abs(a - b)
                        for a, b in zip(
                            after.scene_pixels[i : i + 3],
                            before.scene_pixels[j : j + 3],
                            strict=True,
                        )
                    )
                    <= 20
                )
        return matched / total

    candidates = sorted(
        ((score(dx, dy), dx, dy) for dx in range(-4, 5) for dy in range(-4, 5)), reverse=True
    )
    best, dx, dy = candidates[0]
    if (
        (dx or dy)
        and best >= 0.9
        and best - score(0, 0) >= 0.15
        and best - candidates[1][0] >= 0.04
    ):
        result.update(
            status="screen_scroll_estimate",
            background_shift_pixels=[dx * 8, dy * 8],
            match=round(best, 3),
        )
    return result
