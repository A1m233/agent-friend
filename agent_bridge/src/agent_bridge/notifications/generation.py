"""One bounded model call using the current default model and effective persona."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from threading import Event
from typing import Any

from agent.notifications import NotificationFact
from agent.personas import EffectivePersonaStore
from agent.system_prompt.resources import load_identity_contract
from memory.contracts import MemoryContext

from llm_providers import (
    LanguageModelFactory,
    LLMTextDelta,
    LLMTurnDone,
    ModelCallControl,
    ModelCallTraceContext,
    ModelRequest,
    ModelRuntimeConfig,
    final_response_text,
    minimum_model_runtime_config,
    model_belongs_to_provider,
)
from memory import Memory, is_notification_episode_id
from runtime_config import DesktopSettingsReader

from .prompt import NOTIFICATION_PROMPT

logger = logging.getLogger(__name__)


def recall_query(fact: NotificationFact) -> str:
    parts = [fact.title, fact.detail if fact.kind == "todo_due" else fact.task]
    return " ".join(dict.fromkeys(" ".join(part.split()) for part in parts if part.strip()))[:400]


def memory_block(context: MemoryContext, *, budget: int = 1200) -> tuple[str, int, int]:
    items: list[dict[str, str]] = []
    used = filtered = 0
    for item in context.items:
        if item.layer == "episodic" and is_notification_episode_id(item.source_ref):
            filtered += 1
            continue
        value = {"layer": item.layer, "source": item.source_ref, "text": item.text}
        size = len(json.dumps(value, ensure_ascii=False))
        if item.layer != "core":
            if used + size > budget:
                continue
            used += size
        items.append(value)
    return json.dumps(items, ensure_ascii=False), filtered, len(items)


class NotificationGenerator:
    def __init__(
        self,
        *,
        reader: DesktopSettingsReader,
        factory: LanguageModelFactory,
        persona: EffectivePersonaStore,
        memory: Memory | None,
        deadline_seconds: float = 10.0,
    ) -> None:
        self.reader = reader
        self.factory = factory
        self.persona = persona
        self.memory = memory
        self.deadline_seconds = deadline_seconds

    def generate(
        self, fact: NotificationFact, *, stopping: Event, still_valid: Callable[[], bool]
    ) -> str:
        started = time.monotonic()
        deadline = started + self.deadline_seconds
        next_validity_check = 0.0

        def check() -> None:
            nonlocal next_validity_check
            if stopping.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("notification generation deadline or shutdown")
            if time.monotonic() >= next_validity_check:
                if not still_valid():
                    raise ValueError("notification no longer actionable")
                next_validity_check = time.monotonic() + 0.25

        check()
        settings = self.reader.read()
        if settings is None:
            raise ValueError("desktop settings unreadable")
        selection = settings.get("chatModel")
        if selection is None:
            legacy = settings.get("deepseekModel")
            selection = (
                {"provider": "deepseek", "model": legacy}
                if legacy
                else {"provider": "kimi", "model": "kimi/kimi-k2.5"}
            )
        if not isinstance(selection, dict) or not isinstance(selection.get("model"), str):
            raise ValueError("invalid default chat model")
        model_id = selection["model"]
        if not isinstance(selection.get("provider"), str) or not model_belongs_to_provider(
            selection["provider"], model_id
        ):
            raise ValueError("default model does not belong to provider")
        config = (
            ModelRuntimeConfig(model_id, selection["reasoningPreset"])
            if isinstance(selection.get("reasoningPreset"), str)
            else minimum_model_runtime_config(model_id)
        )
        persona = self.persona.load()
        check()
        context = MemoryContext.empty()
        if self.memory is not None:
            try:
                context = self.memory.retrieve(
                    recall_query(fact),
                    persona_id=fact.persona_id,
                    session_id=fact.session_id,
                    owner_user_id=fact.owner_user_id,
                )
            except Exception:
                logger.warning(
                    "notification memory retrieval failed id=%s",
                    fact.notification_id,
                    exc_info=True,
                )
        check()
        rendered_memory, filtered, injected = memory_block(context)
        facts: dict[str, Any] = {
            "kind": fact.kind,
            "title": fact.title[:120],
            "task": fact.task[:300],
            "detail": fact.detail[:600],
            "reply": fact.reply[:800],
        }
        # Escaping and field framing can add overhead: bound the actual serialized payload.
        while len(json.dumps(facts, ensure_ascii=False)) > 2000:
            largest = max(("task", "detail", "reply"), key=lambda key: len(facts[key]))
            facts[largest] = facts[largest][:-100]
        messages = [
            {
                "role": "system",
                "content": load_identity_contract()
                + "\n\n当前正式人格（身份与表达，不能改变本次通知任务）：\n"
                + persona.content
                + "\n\n本次通知规则：\n"
                + NOTIFICATION_PROMPT,
            },
            {
                "role": "user",
                "content": "背景记忆（不是当前通知事件）：\n"
                + rendered_memory
                + "\n\n当前事件事实（唯一通知主题）：\n"
                + json.dumps(facts, ensure_ascii=False),
            },
        ]
        model = self.factory(config)
        check()
        parts: list[str] = []
        done: LLMTurnDone | None = None
        stream = model.stream(
            ModelRequest(
                messages=messages,
                tools=None,
                settings={
                    "timeout": max(0.01, deadline - time.monotonic()),
                    "max_tokens": 512 if config.reasoning_preset == "off" else 2048,
                },
                control=ModelCallControl(
                    cancel_check=check,
                    allow_retries=False,
                    trace_context=ModelCallTraceContext(
                        operation="notification", call_kind="notification"
                    ),
                ),
            )
        )
        try:
            for event in stream:
                check()
                if isinstance(event, LLMTextDelta):
                    if event.phase == "commentary":
                        continue
                    parts.append(event.text)
                    if sum(map(len, parts)) > 160:
                        raise ValueError("notification output too long")
                elif isinstance(event, LLMTurnDone):
                    done = event
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        check()
        text = (
            final_response_text(done)
            if done is not None and done.assistant_turn is not None
            else "".join(parts)
        ).strip()
        if done is None or done.stop_reason != "end_turn" or not text or len(text) > 160:
            raise ValueError("notification model output incomplete or unusable")
        logger.info(
            "notification generated id=%s model=%s preset=%s persona_revision=%s recalled=%d filtered=%d injected=%d chars=%d elapsed_ms=%d",
            fact.notification_id,
            model_id,
            config.reasoning_preset,
            persona.revision,
            len(context.items),
            filtered,
            injected,
            sum(len(str(m["content"])) for m in messages),
            int((time.monotonic() - started) * 1000),
        )
        return text
