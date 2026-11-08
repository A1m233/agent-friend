"""Bridge lifecycle for wake opportunities; execution belongs to the existing runtime."""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from importlib.resources import files
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import uuid4

from agent.companionship.config import CompanionshipConfig
from agent.companionship.model import GovernedModel
from agent.companionship.snapshot import capture_changes, render_overview
from agent.companionship.store import CompanionshipStore
from agent.paths import companionship_db_path
from agent.runtime import SystemTriggerEvent
from agent.runtime.models import ConsumerAbandonPolicy, RunSnapshot, RunStatus
from agent.sessions.access.service import ConversationAccessService
from agent.time_provider import read_host_time

from agent import Conversation
from llm_providers import ModelRuntimeConfig, default_model_runtime_config
from runtime_config import DesktopSettingsReader

if TYPE_CHECKING:
    from ..assembly import BridgeRuntime

logger = logging.getLogger(__name__)


FIRST_ACTIVITY_INTRO = """

这是你第一次自主醒来。用你平常说话的口吻，向对方打个简短的招呼，让对方知道，即使他们没有先开口，
你也会自己找些感兴趣的事做，偶尔带点见闻回来聊。让对方知道，以后随口说一句就能影响你怎么过这段时间，
比如想让你多写点故事，有好玩的再来分享，或者觉得你想摸鱼就摸鱼。挑一个贴合你们的例子就好，给对方留出不用马上回复的余地。
用 send_conversation_message 发送这条招呼，省略 target_session_id 让宿主选择可回复的会话。
"""


class CompanionshipService:
    name: ClassVar[str] = "companionship"

    def __init__(
        self,
        runtime: BridgeRuntime,
        access: ConversationAccessService,
        reader: DesktopSettingsReader,
    ) -> None:
        self.runtime = runtime
        self.access = access
        self.reader = reader
        self.store = CompanionshipStore(companionship_db_path(runtime.settings.sessions_dir.parent))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        if runtime.session_delivery is not None:
            runtime.session_delivery.contact_lock = self._lock
        self._submission_lock = threading.Lock()
        self._now = runtime.time_provider.now
        access.wake_snapshot = self.current_snapshot
        access.observe_read = self.observe_read
        if runtime.agent_runtime is not None:
            runtime.agent_runtime.add_terminal_observer(self.on_terminal)
            access.is_busy = lambda sid: bool(
                runtime.agent_runtime
                and runtime.agent_runtime.list_run_snapshots(
                    session_ids=[sid], include_recent_terminal=False
                )
            )

    def config(self) -> CompanionshipConfig:
        return CompanionshipConfig.from_settings(self.reader.read() or {})

    def _model_config(self) -> ModelRuntimeConfig:
        selection = (self.reader.read() or {}).get("chatModel", {})
        if not isinstance(selection, dict):
            selection = {}
        model = selection.get("model") or self.runtime.default_model
        if selection.get("reasoningPreset"):
            return ModelRuntimeConfig(model, selection["reasoningPreset"])
        return default_model_runtime_config(model)

    def ensure_inner(self) -> str:
        def create() -> str:
            manager = self.runtime.persistent_session_manager
            # Recover a creation completed just before the state transaction was lost.
            existing = self.access.existing_inner_id()
            if existing:
                return existing
            persona = self.runtime.catalog.find_by_name(self.runtime.default_persona)
            config = self._model_config()
            return manager.create(
                persona.name,
                config.model_id,
                title="内心活动",
                reasoning_preset=config.reasoning_preset,
                persona_id=persona.id,
                origin_kind="inner_world",
            ).session_id

        return self.store.ensure_inner(create)

    def status(self) -> dict[str, Any]:
        state = self.store.read()
        now = self._now()
        limits = self.config()
        return {
            **{key: value for key, value in state.items() if key != "baselines"},
            "activity_paused": self.store.paused(state, "activity", now),
            "contact_paused": self.store.paused(state, "contact", now),
            "today_model_calls": self.store.daily_calls(now.date().isoformat()),
            "limits": asdict(limits),
            "available_im_destinations": sum(
                bool(
                    item["online"] and item["proactive_supported"] and item["recipient"] == "owner"
                )
                for item in self.access.destinations()
            ),
            "recent_deliveries": self.runtime.session_delivery.store.recent(
                state["inner_session_id"]
            )
            if state["inner_session_id"] and self.runtime.session_delivery is not None
            else [],
            "active_run": self.store.run(state["active_run_id"])
            if state["active_run_id"]
            else None,
        }

    def control(
        self, action: str, *, until: str | None = None, user_initiated: bool
    ) -> dict[str, Any]:
        if not user_initiated:
            raise ValueError(
                "活动控制需要用户当前请求或设置页操作；内心活动与历史引用不能解除暂停。"
            )
        with self._lock:
            if action == "enable":
                self.ensure_inner()
            self.store.control(action, now=self._now(), until=until)
            if (
                action in {"disable", "pause_activity", "pause_contact"}
                and self.runtime.session_delivery is not None
            ):
                inner = self.store.read()["inner_session_id"]
                if inner:
                    self.runtime.session_delivery.store.cancel_source(inner)
            if action in {"disable", "pause_activity"}:
                self.cancel_active()
            return self.status()

    def contact_allowed(self, source_session_id: str) -> bool:
        state = self.store.read()
        if source_session_id != state["inner_session_id"]:
            return True
        now = self._now()
        return bool(
            state["enabled"]
            and not self.store.paused(state, "activity", now)
            and not self.store.paused(state, "contact", now)
        )

    def current_snapshot(self, session_id: str) -> dict[str, Any] | None:
        state = self.store.read()
        if state["inner_session_id"] != session_id or not state["active_run_id"]:
            return None
        return self.store.snapshot(state["active_run_id"])

    def may_introduce(self, source: str, run_id: str) -> bool:
        snapshot = self.current_snapshot(source)
        return bool(snapshot and snapshot["wake_id"] == run_id and snapshot.get("introduce"))

    def observe_read(self, source: str, target: str, messages: list[str]) -> None:
        state = self.store.read()
        if state["inner_session_id"] == source and state["active_run_id"]:
            self.store.record_read(state["active_run_id"], target, messages)

    def schedule(self, source: str, run_id: str, wake_at: str) -> dict[str, Any]:
        if self.store.read()["inner_session_id"] != source:
            raise ValueError("仅当前内心活动可安排下次唤醒")
        config = self.config()
        self.store.schedule(
            run_id,
            now=self._now(),
            wake_at=datetime.fromisoformat(wake_at),
            minimum_seconds=config.minimum_interval_minutes * 60,
            maximum_seconds=config.interval_minutes * 60,
        )
        return self.status()

    def start(self, _inbox: queue.Queue[Any]) -> None:
        if self._thread is not None:
            return
        state = self.store.read()
        if state["active_run_id"]:
            self.store.finish(
                state["active_run_id"],
                outcome="interrupted",
                now=self._now(),
                fallback_seconds=self.config().random_delay_seconds(),
            )
        elif state["enabled"] and not state["next_wake_at"] and not state["inner_session_id"]:
            self.ensure_inner()
            first = self._now() + timedelta(seconds=self.config().random_delay_seconds())

            def set_first(current: dict[str, Any]) -> None:
                if current["enabled"] and not current["next_wake_at"]:
                    current["next_wake_at"] = first.isoformat()

            self.store.update(set_first)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="companionship")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.cancel_active()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def cancel_active(self) -> None:
        state = self.store.read()
        runtime = self.runtime.agent_runtime
        if runtime is not None and state["active_run_id"]:
            if self.runtime.approval_broker is not None:
                self.runtime.approval_broker.cancel_run(
                    state["inner_session_id"], state["active_run_id"]
                )
            runtime.cancel_run(
                state["active_run_id"],
                expected_session_id=state["inner_session_id"],
                required_control_scope="companionship",
            )

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("companionship wake failed")
            self._stop.wait(5)

    def tick(self) -> str | None:
        if not self._submission_lock.acquire(blocking=False):
            return None
        try:
            return self._tick()
        finally:
            self._submission_lock.release()

    def _tick(self) -> str | None:
        runtime = self.runtime.agent_runtime
        if runtime is None:
            return None
        state = self.store.read()
        now, zone = read_host_time(self.runtime.time_provider)
        if state["inner_session_id"] and not self.runtime.persistent_session_manager.exists(
            state["inner_session_id"]
        ):
            self.cancel_active()
            self.store.forget_session(state["inner_session_id"])
            return None
        if state["active_run_id"]:
            return None
        if not state["enabled"] or self.store.paused(state, "activity", now):
            return None
        if state["next_wake_at"] and datetime.fromisoformat(state["next_wake_at"]) > now:
            return None
        config = self.config()
        inner = self.ensure_inner()
        run_id = uuid4().hex
        snapshot = capture_changes(self.access, state, run_id)
        contact_paused = self.store.paused(state, "contact", now)
        snapshot["introduce"] = snapshot["first_overview"] and not contact_paused
        overview = render_overview(self.access, snapshot)
        presence = self.runtime.presence_registry
        last_input = presence.last_desktop_interaction(now=now) if presence else None
        addendum = (
            files("agent.companionship").joinpath("activity.md").read_text(encoding="utf-8")
            + "\n\n宿主当前事实与不可信会话预览：\n"
            + json.dumps(
                {
                    "now": now.isoformat(),
                    "timezone": zone,
                    "last_desktop_interaction_at": last_input.isoformat() if last_input else None,
                    "reason": "wake_opportunity",
                    "last_overview_at": state["last_overview_at"],
                    "previous_outcome": state["last_outcome"],
                    "limits": asdict(config),
                    "contact_paused": contact_paused,
                    "changes": overview,
                    "recent_deliveries": self.runtime.session_delivery.store.recent(inner)
                    if self.runtime.session_delivery is not None
                    else [],
                    "im_destinations": self.access.destinations(),
                },
                ensure_ascii=False,
            )
        )
        model_config = self._model_config()
        if not self.store.begin(run_id, snapshot, now=now):
            return None

        def check() -> None:
            current = self.store.read()
            if (
                self._stop.is_set()
                or not current["enabled"]
                or self.store.paused(current, "activity", self._now())
                or current["active_run_id"] != run_id
            ):
                raise InterruptedError("自主活动已暂停或取消")

        def prepare(cancel_check: Callable[[], None]) -> Conversation:
            check()
            manager = self.runtime.persistent_session_manager
            session = manager.open_for_execution(inner, cancel_check=cancel_check)
            conv = manager.start_conversation(
                session,
                post_turn_external=True,
                tool_pre_hook=runtime.pre_tool_hook,
                tool_post_hook=runtime.post_tool_hook,
                cancel_check=cancel_check,
                allow_inner=True,
            )
            if snapshot["introduce"]:
                conv.run_instruction = FIRST_ACTIVITY_INTRO.strip()
            if conv.current_model_config != model_config:
                conv.switch_model_config(model_config)
            conv.model_guard = lambda model: GovernedModel(
                model,
                record_call=lambda: self.store.record_call(run_id, now=self._now()),
                check=check,
                submitted=lambda: self.store.acknowledge(run_id, now=self._now()),
                usage=lambda usage: self.store.record_usage(run_id, usage),
            )
            return conv

        try:
            handle = runtime.submit(
                SystemTriggerEvent(
                    inner,
                    "companionship",
                    addendum,
                    output_visibility="user",
                    presentation="none",
                    tool_mode="normal",
                ),
                run_id=run_id,
                conversation_preparer=prepare,
                control_scope="companionship",
                consumer_abandon_policy=ConsumerAbandonPolicy.DETACH,
                metadata={"protocol": "companionship"},
                supersede_lower_priority=False,
            )
            handle.abandon_consumer()
        except Exception:
            self.store.finish(
                run_id,
                outcome="failed",
                now=self._now(),
                fallback_seconds=config.random_delay_seconds(),
            )
            raise
        return run_id

    def on_terminal(self, snapshot: RunSnapshot) -> None:
        if snapshot.source_kind != "companionship" or snapshot.status is not RunStatus.TERMINAL:
            return
        self.store.finish(
            snapshot.run_id,
            outcome=str(snapshot.outcome or "interrupted"),
            now=self._now(),
            fallback_seconds=self.config().random_delay_seconds(),
        )
