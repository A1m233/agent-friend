"""Recognize quantity-spinner decoration from screenshot pixels only."""

from __future__ import annotations

UP_ARROW = (
    "......DD......",
    ".....DOOD.....",
    "....DOOOOD....",
    "...DOOOOOOD...",
    "..DOOOOOOOOD..",
    ".DOOOOOOOOOOD.",
    "DOOOODDDDOOOOD",
    "DOODD....DDOOD",
    ".DD........DD.",
)
COLORS = {"D": (132, 33, 0), "O": (255, 82, 0)}


def quantity_arrows(data: bytes) -> tuple[tuple[int, int], ...]:
    """Accept exactly one aligned up/down pair with the known sprite shape."""
    points = {
        (x, y)
        for y in range(114)
        for x in range(240)
        if tuple(data[(y * 240 + x) * 3 : (y * 240 + x) * 3 + 3]) in COLORS.values()
    }
    found: dict[str, list[tuple[int, int]]] = {"up": [], "down": []}
    while points:
        seed = points.pop()
        component, stack = {seed}, [seed]
        while stack:
            x, y = stack.pop()
            for p in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if p in points:
                    points.remove(p)
                    component.add(p)
                    stack.append(p)
        left = min(x for x, _ in component)
        top = min(y for _, y in component)
        for name, rows in (("up", UP_ARROW), ("down", UP_ARROW[::-1])):
            expected = {
                (left + x, top + y): COLORS[c]
                for y, row in enumerate(rows)
                for x, c in enumerate(row)
                if c != "."
            }
            if component == expected.keys() and all(
                tuple(data[(y * 240 + x) * 3 : (y * 240 + x) * 3 + 3]) == color
                for (x, y), color in expected.items()
            ):
                found[name].append((left, top))
    if len(found["up"]) != 1 or len(found["down"]) != 1:
        return ()
    up, down = found["up"][0], found["down"][0]
    return (up, down) if up[0] == down[0] and 24 <= down[1] - up[1] <= 100 else ()


def same_quantity_scene(
    left: bytes,
    right: bytes,
    arrows: tuple[tuple[int, int], ...],
    other: tuple[tuple[int, int], ...],
) -> bool:
    if not arrows or not other:
        return False
    # Animation moves the two arrows apart/together by at most two pixels.
    if (
        arrows[0][0] != other[0][0]
        or arrows[0][1] + arrows[1][1] != other[0][1] + other[1][1]
        or abs(arrows[0][1] - other[0][1]) > 2
    ):
        return False
    ignored = {
        (top + y) * 240 + start + x
        for pair in (arrows, other)
        for (start, top), rows in zip(pair, (UP_ARROW, UP_ARROW[::-1]), strict=True)
        for y, row in enumerate(rows)
        for x, c in enumerate(row)
        if c != "."
    }
    return all(
        a == b or i // 3 in ignored for i, (a, b) in enumerate(zip(left, right, strict=True))
    )
