"""Conservative screen-only recognition for the current FireRed white dialogue skin.

This deliberately does not recognize story, coordinates, characters or hidden state.
Unknown skins and extra white panels are handed back to the player.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .protocol import GameError


@dataclass(frozen=True)
class Frame:
    kind: str
    signature: str
    pixels: bytes
    text_pixels: bytes
    blank: bool
    ready_pixels: bytes = b""
    scene_pixels: bytes = b""
    advance_arrow: bool = False
    text_ink_pixels: int = 0

    def similar(self, other: Frame) -> bool:
        if self.kind != other.kind or self.blank or other.blank:
            return False
        if self.kind in {"dialogue", "battle_text"}:
            return self.text_pixels == other.text_pixels
        if self.kind in {"battle_menu", "choice_or_overlay"}:
            return self.ready_pixels == other.ready_pixels
        # Coarse cells suppress grass/water/sprite animation, not a scrolling scene.
        changed = sum(
            max(
                abs(a - b)
                for a, b in zip(
                    self.scene_pixels[i : i + 3], other.scene_pixels[i : i + 3], strict=True
                )
            )
            > 20
            for i in range(0, len(self.scene_pixels), 3)
        )
        return changed <= 6


def extra_panel(points: set[tuple[int, int]], data: bytes, *, require_frame: bool = False) -> bool:
    """A second white panel is an uncertainty/choice signal, never permission to press."""
    while points:
        seed = points.pop()
        stack = [seed]
        count = 0
        left = right = seed[0]
        top = bottom = seed[1]
        while stack:
            x, y = stack.pop()
            count += 1
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if point in points:
                    points.remove(point)
                    stack.append(point)
        width, height = right - left + 1, bottom - top + 1
        # Bookcase faces in the lab are 26x10. A choice needs a panel-sized area.
        if (
            not require_frame
            and width >= 20
            and height >= 20
            and count >= 400
            and count / (width * height) >= 0.5
        ):
            return True
        # Smaller choices still have a rectangular dark frame. Require all four
        # sides, so isolated white book spines and text strokes do not qualify.
        if width >= 12 and height >= 18 and count / (width * height) >= 0.5:

            def dark(x: int, y: int) -> bool:
                if not (0 <= x < 240 and 0 <= y < 114):
                    return False
                offset = (y * 240 + x) * 3
                return max(data[offset : offset + 3]) <= 140

            horizontal = range(left, right + 1)
            vertical = range(top, bottom + 1)
            if all(
                ratio >= 0.7
                for ratio in (
                    sum(any(dark(x, top - d) for d in range(1, 5)) for x in horizontal) / width,
                    sum(any(dark(x, bottom + d) for d in range(1, 5)) for x in horizontal) / width,
                    sum(any(dark(left - d, y) for d in range(1, 5)) for y in vertical) / height,
                    sum(any(dark(right + d, y) for d in range(1, 5)) for y in vertical) / height,
                )
            ):
                return True
    return False


def advance_arrow_mask(data: bytes) -> set[tuple[int, int]]:
    """Recognize the 9/7/5/3/1 triangle and shadow on either text line.

    Match shape and palette rather than erasing all red dialogue text. Masking
    only red leaves the gray/dark shadow animated when the triangle moves.
    """
    points = {
        (x, y)
        for y in range(121, 155)
        for x in range(8, 232)
        if tuple(data[(y * 240 + x) * 3 : (y * 240 + x) * 3 + 3]) in {(231, 8, 8), (255, 0, 0)}
    }
    masked: set[tuple[int, int]] = set()
    while points:
        seed = points.pop()
        component = {seed}
        stack = [seed]
        while stack:
            x, y = stack.pop()
            for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if point in points:
                    points.remove(point)
                    component.add(point)
                    stack.append(point)
        if len(component) != 25:
            continue
        left = min(x for x, _ in component)
        top = min(y for _, y in component)
        if component != {
            (left + row + column, top + row) for row in range(5) for column in range(9 - row * 2)
        }:
            continue
        masked.update(component)
        for y in range(top, min(top + 6, 155)):
            for x in range(left, min(left + 10, 232)):
                i = (y * 240 + x) * 3
                if tuple(data[i : i + 3]) in {(99, 99, 99), (41, 49, 49)}:
                    masked.add((x, y))
    return masked


def area_intro(data: bytes) -> bool:
    """Match the current skin's light title card beside a black top band."""
    dark = sum(
        max(data[(y * 240 + x) * 3 : (y * 240 + x) * 3 + 3]) <= 8
        for y in range(24)
        for x in range(112, 240)
    )
    light = sum(
        min(data[(y * 240 + x) * 3 : (y * 240 + x) * 3 + 3]) >= 200
        for y in range(1, 18)
        for x in range(4, 100)
    )
    return dark >= 3040 and light >= 1100


def read_frame(path: Path) -> Frame:
    if path.stat().st_size > 1024 * 1024:
        raise GameError("invalid_frame", "游戏截图超过 1 MiB，停止识别。")
    with Image.open(path) as image:
        if image.size != (240, 160) or image.format != "PNG":
            raise GameError("invalid_frame", "只识别 240×160 的原始 GBA PNG；不猜测其它画面。")
        rgb = image.convert("RGB")
        data = rgb.tobytes()
    white_bottom = border = battle_blue = battle_gold = menu_border = 0
    sign_light = sign_dark = 0
    party_light = party_dark = 0
    white_above: set[tuple[int, int]] = set()
    text = bytearray()
    masked = bytearray(data)
    for y in range(160):
        for x in range(240):
            i = (y * 240 + x) * 3
            r, g, b = data[i : i + 3]
            if y < 114:
                party_light += (r, g, b) == (74, 173, 165)
                party_dark += (r, g, b) == (57, 140, 140)
                if (r, g, b) == (255, 255, 255):
                    white_above.add((x, y))
            else:
                white_bottom += (r, g, b) == (255, 255, 255)
                border += (r, g, b) == (165, 214, 231)
                sign_light += (r, g, b) == (206, 206, 222)
                sign_dark += (r, g, b) == (107, 115, 123)
                battle_blue += (r, g, b) == (41, 82, 107)
                battle_gold += (r, g, b) == (206, 173, 74)
                menu_border += (r, g, b) == (115, 107, 132)
    dialogue = white_bottom >= 5000 and (border >= 700 or (sign_light >= 700 and sign_dark >= 500))
    battle_text = battle_blue >= 5000 and battle_gold >= 1000
    # Party HP notifications share the purple/white bottom panel. Their
    # distinctive teal background must not turn them into battle menus.
    party_background = party_light >= 4000 and party_dark >= 4000
    battle_menu = white_bottom >= 2500 and menu_border >= 500 and not party_background
    kind = (
        "choice_or_overlay"
        if (dialogue or battle_text) and extra_panel(white_above, data, require_frame=battle_text)
        else "dialogue"
        if dialogue
        else "battle_text"
        if battle_text
        else "battle_menu"
        if battle_menu
        else "other"
    )
    if kind == "other" and area_intro(data):
        kind = "transition"
    arrow = advance_arrow_mask(data) if dialogue or battle_text else set()
    background = bytes((41, 82, 107)) if battle_text else b"\xff\xff\xff"
    for y in range(121, 155):
        for x in range(8, 232):
            i = (y * 240 + x) * 3
            if (x, y) in arrow:
                masked[i : i + 3] = background
            text.extend(masked[i : i + 3])
    return Frame(
        kind,
        hashlib.sha256(masked).hexdigest(),
        data,
        bytes(text),
        kind == "transition" or len(set(data)) < 5,
        rgb.crop((0, 114, 240, 160)).tobytes() if kind == "battle_menu" else data,
        rgb.resize((30, 20), Image.Resampling.BOX).tobytes(),
        bool(arrow),
        sum(min(text[i : i + 3]) >= 200 for i in range(0, len(text), 3)),
    )
