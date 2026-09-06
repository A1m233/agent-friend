"""Fixed development examples using the real notification delivery pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from agent.notifications import NotificationConflict, NotificationFact
from agent.personas import BUILTIN_DEFAULT_PERSONA_ID

if TYPE_CHECKING:
    from .service import NotificationService

DEBUG_EVENT_PREFIX = "debug:"
DebugScenario = Literal["short", "long", "stack"]

_SHORT_TEXT = "这是一条短气泡调试通知。看看它是否出现在操作栏外侧，首次显示时是否响了一声。"
_LONG_TEXT = (
    "这是一条用于检查桌宠气泡布局的长通知。请观察文字换行、展开按钮、关闭按钮，"
    "以及气泡和操作栏之间是否保留了清楚的间距。把桌宠拖到屏幕左侧、右侧或上沿时，"
    "气泡应该与操作栏一起换位，始终排列在操作栏外侧。你还可以先关闭再开启提示音，"
    "确认只有新的气泡首次出现时才会响一次，展开或移动不应重复播放。长通知到这里完整结束。"
)


def create_debug_notifications(
    manager: NotificationService, scenario: DebugScenario
) -> list[dict[str, Any]]:
    if scenario == "stack":
        texts = [
            f"这是第 {index} 条调试通知。当前共有五条，请检查可见气泡与排队内容，关闭前面的气泡后继续观察。"
            for index in range(1, 6)
        ]
    else:
        texts = [_LONG_TEXT if scenario == "long" else _SHORT_TEXT]
    batch = uuid4().hex
    return [
        manager.accept(
            NotificationFact(
                event_key=f"{DEBUG_EVENT_PREFIX}{batch}:{index}",
                kind="ambient",
                title="气泡调试",
                persona_id=BUILTIN_DEFAULT_PERSONA_ID,
                created_at=manager.clock(),
            ),
            provided_text=text,
        ).to_wire()
        for index, text in enumerate(texts, start=1)
    ]


def clear_debug_notifications(manager: NotificationService) -> list[dict[str, Any]]:
    for item in manager.store.list():
        if not item.fact.event_key.startswith(DEBUG_EVENT_PREFIX):
            continue
        for attempt in range(3):
            current = manager.store.get(item.notification_id)
            if current.delivery["status"] in {"dismissed", "invalidated"}:
                break
            try:
                manager.dismiss(item.notification_id, version=current.version)
                break
            except NotificationConflict:
                # Preparation/presentation may commit between the read and close.
                if attempt == 2:
                    raise
    # Do not revalidate or otherwise mutate real notifications during debug cleanup.
    return [item.to_wire() for item in manager.store.list()]
