"""``SessionManager`` —— 会话业务编排。

所有调用方（CLI / `agent_bridge` HTTP / 未来桌宠前端）通过本类访问会话能力：
**CRUD + 装配 Conversation**。Manager 不直接做文件 IO（委托 :class:`SessionStore`），
不直接调 LLM（委托 :class:`Conversation`）。

:meth:`start_conversation` 通过注入的 ``llm_client_factory`` /
``prompt_builder_factory`` / ``context_manager_factory`` 装配并返回
:class:`~agent.Conversation`。

详见 docs/requirements/002-engine-session-management/design.md §4.4。
"""

from __future__ import annotations

import builtins
import dataclasses
import logging
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from llm_providers import (
    LanguageModelFactory,
    ModelRuntimeConfig,
    default_model_runtime_config,
    validate_model_runtime_config,
)

from ..approvals.models import SessionApprovalModeState
from ..command_runtime.workspace import (
    reject_linked_path,
    remove_readonly_file,
    session_workspace_path,
)
from ..errors import AgentError
from ..prompt_cache_trace import PromptCacheTraceTracker
from ..time_provider import SystemTimeProvider, TimeProvider
from .cache_epoch import SessionCacheEpoch
from .changes import SessionChangeBroker
from .errors import SessionCorruptError
from .events import Event
from .metadata import SessionMetadataStore
from .model_context import SessionModelContext
from .model_history import SessionModelHistory
from .query import (
    CancelCheck,
    SessionHistoryPage,
    SessionQuerySnapshot,
    check_cancel,
    load_session,
    query_session,
    query_session_page,
)
from .recovery import ToolTransactionReconciler
from .residency import retained_bytes
from .rollout import SessionRolloutRecorder, finish_rollout_restore, restore_rollout
from .session import Session
from .store import (
    SessionStore,
    SessionSummary,
    SessionWriterLease,
    acquire_session_writer_lease,
    delete_session_writer_lease,
)
from .tool_execution_evidence import ToolExecutionEvidenceError, ToolExecutionEvidenceStore

if TYPE_CHECKING:
    from memory import Memory

    from ..context import ContextManager
    from ..conversation import (
        Conversation,
        MessageProjector,
        MessageRenderer,
        PostToolHook,
        PreToolHook,
        ToolHookInvoker,
    )
    from ..prompts import PromptBuilder
    from ..session_files.service import SessionFileService
    from ..skills import SkillCatalogService
    from ..tool_runtime import ToolRunContext, ToolRunPolicy
    from ..tools import ToolRegistry


TitleGenerator = Callable[[Session], str]
PromptBuilderFactory = Callable[[str], "PromptBuilder"]
ContextManagerFactory = Callable[[], "ContextManager"]
logger = logging.getLogger(__name__)


@dataclasses.dataclass
class _SessionRuntimeState:
    """Process-local execution state shared by reconstructed Conversations."""

    model_history: SessionModelHistory
    model_context: SessionModelContext
    context_manager: ContextManager
    prompt_cache_trace_tracker: PromptCacheTraceTracker
    cache_epoch: SessionCacheEpoch
    rollout_recorder: SessionRolloutRecorder
    writer_lease: SessionWriterLease
    recovery_disposition: str = "fresh"
    exact_continuation_candidate: bool = False
    runtime_instance_id: str = ""
    runtime_start_reason: str = "direct_start"


def _default_title(now: datetime | None = None) -> str:
    """占位标题：``"会话 YYYY-MM-DD HH:MM"``（local time）。"""
    dt = now or datetime.now(UTC).astimezone()
    return f"会话 {dt:%Y-%m-%d %H:%M}"


class SessionManager:
    """会话业务编排。

    Args:
        store: 持久化实现（如 :class:`JsonlSessionStore`）。
        llm_client_factory: 接 model 名 → :class:`LanguageModel`。M2.1 阶段可不传，
            M2.2 接 Conversation 后必须注入。
        prompt_builder_factory: 接 persona 名 → :class:`PromptBuilder`。同上。
        context_manager_factory: 接 0 参 → :class:`ContextManager`，**每个会话独立
            产一个实例**（009 起从单例改工厂，与 ``llm_client_factory`` /
            ``prompt_builder_factory`` 邻居一致）。摘要策略需持会话级 circuit breaker
            状态，单例会串话；FIFO / Naive 虽无状态，统一走工厂便于按里程碑切换默认。
            无状态策略可直接传类本身（如 ``context_manager_factory=NaiveContextManager``）。
        title_generator: 可选；若提供，:meth:`create` 在未显式传 ``title`` 时
            会调用它，否则用 :func:`_default_title` 兜底。
        tool_registry: 可选；若提供，:meth:`start_conversation` 装配出的
            :class:`Conversation` 会挂上同一个 registry，让 AI 能调用其中的工具。
            005 起新增。``None``（默认）等价于"不开放任何工具"，行为与 002~004
            完全一致。
        memory: 可选；若提供，:meth:`start_conversation` 装配出的
            :class:`Conversation` 会挂上**同一个** memory 实例（记忆是 user 维度的
            全局单例，跨会话共享）。008 起新增。``None``（默认）等价于"不启用记忆"，
            行为与 002~007 完全一致。
    """

    def __init__(
        self,
        store: SessionStore,
        llm_client_factory: LanguageModelFactory | None = None,
        prompt_builder_factory: PromptBuilderFactory | None = None,
        context_manager_factory: ContextManagerFactory | None = None,
        title_generator: TitleGenerator | None = None,
        tool_registry: ToolRegistry | None = None,
        memory: Memory | None = None,
        metadata_store: SessionMetadataStore | None = None,
        session_change_broker: SessionChangeBroker | None = None,
        message_renderer: MessageRenderer | None = None,
        message_budget_renderer: MessageRenderer | None = None,
        message_projector: MessageProjector | None = None,
        session_file_service: SessionFileService | None = None,
        skill_service: SkillCatalogService | None = None,
        command_workspaces_root: Path | None = None,
        time_provider: TimeProvider | None = None,
        transaction_reconciler: ToolTransactionReconciler | None = None,
        tool_execution_evidence_store: ToolExecutionEvidenceStore | None = None,
        runtime_instance_id: str = "",
        runtime_start_reason: str = "direct_start",
    ) -> None:
        self._store = store
        self._llm_client_factory = llm_client_factory
        self._prompt_builder_factory = prompt_builder_factory
        self._context_manager_factory = context_manager_factory
        self._title_generator = title_generator
        self._tool_registry = tool_registry
        self._memory = memory
        self._metadata_store = metadata_store
        self._session_change_broker = session_change_broker
        self._message_renderer = message_renderer
        self._message_budget_renderer = message_budget_renderer
        self._message_projector = message_projector
        self._session_file_service = session_file_service
        self._skill_service = skill_service
        self._time_provider = time_provider or SystemTimeProvider()
        self._runtime_states: dict[str, _SessionRuntimeState] = {}
        self._transaction_reconciler = transaction_reconciler
        self._tool_execution_evidence_store = tool_execution_evidence_store
        self._runtime_instance_id = runtime_instance_id
        self._runtime_start_reason = runtime_start_reason
        self._command_workspaces_root = (
            command_workspaces_root.resolve() if command_workspaces_root is not None else None
        )

    def runtime_state_diagnostics(self, session_id: str) -> dict[str, object] | None:
        """Return content-free process-local state for desktop reattachment."""

        state = self._runtime_states.get(session_id)
        if state is None:
            return None
        return {
            "resident": True,
            "modelHistoryGeneration": state.model_history.generation,
            "providerRequestCount": state.model_history.provider_request_count,
            "cacheEpochGeneration": state.cache_epoch.generation,
            "cacheEpochId": state.cache_epoch.epoch_id,
            "cacheEpochReason": state.cache_epoch.reason,
            "recoveryDisposition": state.recovery_disposition,
            "exactContinuationCandidate": state.exact_continuation_candidate,
            "runtimeInstanceId": state.runtime_instance_id,
            "runtimeStartReason": state.runtime_start_reason,
        }

    def close(self) -> None:
        """Release all resident writer leases during a clean Bridge shutdown."""

        states = tuple(self._runtime_states.values())
        self._runtime_states.clear()
        for state in states:
            state.writer_lease.release()

    def resident_states(self) -> dict[str, object]:
        return dict(self._runtime_states)

    def has_unresolved_tool_transactions(self, session_id: str) -> bool:
        state = self._runtime_states.get(session_id)
        return (
            state is not None and state.rollout_recorder.session.has_unresolved_tool_transactions()
        )

    @staticmethod
    def resident_revision(state: object) -> object:
        if not isinstance(state, _SessionRuntimeState):
            raise TypeError("not a session runtime")
        return (state.rollout_recorder.commit_id, len(state.rollout_recorder.session.events))

    @staticmethod
    def resident_size(state: object) -> int:
        if not isinstance(state, _SessionRuntimeState):
            raise TypeError("not a session runtime")
        recorder = state.rollout_recorder
        return retained_bytes(
            (
                recorder.session,
                state.model_history,
                state.model_context,
                state.cache_epoch,
                recorder._committed_serialized,
                recorder._pending,
                recorder._pending_witness,
                recorder._pending_cache_diagnostics,
                recorder.restored,
                recorder.latest_commit,
                vars(recorder._encoder),
                getattr(state.context_manager, "__dict__", {}),
                getattr(state.prompt_cache_trace_tracker, "__dict__", {}),
            )
        )

    def release_idle_runtime(self, session_id: str, expected: object) -> bool:
        """Caller owns the run/turn mutation reservation and current-use guard."""
        state = self._runtime_states.get(session_id)
        if state is None or state is not expected:
            return False
        state.writer_lease.release()
        if self._runtime_states.get(session_id) is not state:
            return False
        del self._runtime_states[session_id]
        return True

    # ----- 会话 CRUD -----

    def current_revision(self, session_id: str) -> int:
        if self._session_change_broker is None:
            return 0
        return self._session_change_broker.current_revision(session_id)

    def approval_mode_state(
        self, session: Session | SessionQuerySnapshot | SessionHistoryPage
    ) -> SessionApprovalModeState:
        """Read optional metadata for an already-bound persistent or transient session."""

        if self._metadata_store is None:
            return SessionApprovalModeState()
        metadata = self._metadata_store.get(session.session_id)
        return SessionApprovalModeState(
            metadata.approval_mode,
            metadata.approval_mode_version,
        )

    def create(
        self,
        persona: str,
        model: str,
        title: str | None = None,
        *,
        reasoning_preset: str | None = None,
        persona_id: str | None = None,
        session_id: str | None = None,
        channel: str = "text",
        origin_kind: str | None = None,
        origin_id: str | None = None,
        origin_occurrence_id: str | None = None,
    ) -> Session:
        """创建新会话并落盘首行 ``session_meta`` 事件。

        Args:
            persona: 初始 persona 名（slug；显示用）。
            model: 初始 model 名。
            title: 可选；不传则按以下顺序回落：
                ``title_generator(session)`` → :func:`_default_title`。
            persona_id: 初始 persona 的 UUID（003 起的主键）。``None`` 时为兼容
                老调用方接受，session_meta 不写 ``initial_persona_id`` 字段，
                效果与 002 完全一致。**新调用方推荐显式传**。
            session_id: 可选指定 session id（uuid 字符串）。``None``（默认）时
                由 :meth:`Session.new` 自动生成 uuid4。
                006 起新增以支持 ``agent-bridge`` 的 AG-UI 协议出口需要
                ``thread_id`` 与 ``session_id`` 一一对应的场景；该出口收到一个
                未知 ``thread_id`` 时会用它作为 ``session_id`` 创建新会话。
                **本参数仅当调用方有跨进程 / 跨协议的稳定 id 来源时使用**——
                CLI / 普通脚本继续不传，行为与 002 完全一致。
            channel: 初始 channel（007 起新增）。``"text"`` / ``"voice"``。
                ``"text"`` 时与 002~006 行为完全字节兼容（不写入 ``initial_channel``）。

        Returns:
            已落盘的新 Session 实例。

        Raises:
            SessionPersistError: 落盘失败（包括 ``session_id`` 已存在）。
            ValueError: ``channel`` 不在 ``("voice", "text")`` 中。
        """
        if channel not in ("voice", "text"):
            raise ValueError(f"channel 必须是 voice / text，实际: {channel!r}")
        if reasoning_preset is not None:
            runtime_config = ModelRuntimeConfig(model, reasoning_preset)
        else:
            try:
                runtime_config = default_model_runtime_config(model)
            except ValueError:
                runtime_config = ModelRuntimeConfig(model, "none")
        session = Session.new(
            title=title or _default_title(),
            persona=persona,
            model=runtime_config.model_id,
            reasoning_preset=runtime_config.reasoning_preset,
            persona_id=persona_id,
            session_id=session_id,
            channel=channel,  # type: ignore[arg-type]
            origin_kind=origin_kind,
            origin_id=origin_id,
            origin_occurrence_id=origin_occurrence_id,
        )
        if title is None and self._title_generator is not None:
            generated = self._title_generator(session)
            if isinstance(generated, str) and generated.strip():
                session.initial_title = generated
                head = session.events[0]
                new_payload: dict[str, Any] = {**head.payload, "initial_title": generated}
                session.events[0] = type(head)(
                    type=head.type,
                    uuid=head.uuid,
                    ts=head.ts,
                    payload=new_payload,
                    meta=head.meta,
                )
        self._store.create(session)
        return session

    def exists(self, session_id: str) -> bool:
        """Check persistence without loading or validating conversation history."""
        return self._store.exists(session_id)

    def open(self, session_id: str, *, cancel_check: CancelCheck = None) -> Session:
        """完整加载指定会话。

        Raises:
            SessionNotFoundError: 不存在。
            SessionCorruptError: 文件损坏。
        """
        return load_session(self._store, session_id, cancel_check=cancel_check)

    def query_page(
        self,
        session_id: str,
        *,
        turn_limit: int,
        before: str | None = None,
        history_epoch: str | None = None,
        cancel_check: CancelCheck = None,
    ) -> SessionHistoryPage:
        return query_session_page(
            self._store,
            session_id,
            turn_limit=turn_limit,
            before=before,
            history_epoch=history_epoch,
            cancel_check=cancel_check,
        )

    def query(self, session_id: str, *, cancel_check: CancelCheck = None) -> SessionQuerySnapshot:
        return query_session(self._store, session_id, cancel_check=cancel_check)

    def diagnostic_export(self, session_id: str, *, turn_limit: int | None) -> dict[str, Any]:
        method = getattr(self._store, "diagnostic_export", None)
        if method is None:
            raise NotImplementedError("store does not support bounded diagnostic export")
        return cast(dict[str, Any], method(session_id, turn_limit=turn_limit))

    def open_for_execution(self, session_id: str, *, cancel_check: CancelCheck = None) -> Session:
        """Acquire the session used by the serialized execution owner.

        A resident runtime owns its writer lease for its entire lifetime. Reading
        persistence before looking it up both wastes work and supplies stale
        configuration to attachment/client preparation. Explicit history reads
        continue to use ``open``.
        """
        check_cancel(cancel_check)
        state = self._runtime_states.get(session_id)
        if state is not None:
            validate = getattr(state.writer_lease, "validate", None)
            if callable(validate):
                validate()
            return state.rollout_recorder.session
        loader = getattr(self._store, "load_execution", None)
        if callable(loader):
            return cast(Session, loader(session_id, cancel_check=cancel_check))
        return self.open(session_id, cancel_check=cancel_check)

    def update_model_config(
        self, session_id: str, *, model: str | None, reasoning_preset: str | None
    ) -> ModelRuntimeConfig:
        """Persist next-turn settings without constructing or restoring a Conversation.

        The caller owns the session mutation reservation. A cold session also needs
        the cross-process writer lease; resident state already owns that lease.
        """
        state = self._runtime_states.get(session_id)
        lease = acquire_session_writer_lease(self._store, session_id) if state is None else None
        try:
            session = state.rollout_recorder.session if state is not None else None
            current = (
                session.current_model_config
                if session is not None
                else self.query(session_id).current_model_config
            )
            target = validate_model_runtime_config(
                ModelRuntimeConfig(model or current.model_id, reasoning_preset)
                if reasoning_preset is not None
                else default_model_runtime_config(model or current.model_id)
            )
            if current == target:
                return current
            if self._llm_client_factory is None:
                raise AgentError("model configuration requires llm_client_factory")
            self._llm_client_factory(target)
            event = Event(
                type="model_config_change",
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={
                    "from": {
                        "model": current.model_id,
                        "reasoningPreset": current.reasoning_preset,
                    },
                    "to": {"model": target.model_id, "reasoningPreset": target.reasoning_preset},
                },
            )
            self._store.append_event(session_id, event)
            if session is not None:
                session.append(event)
            return target
        finally:
            if lease is not None:
                lease.release()

    def list(
        self,
        *,
        archived: bool = False,
        query: str | None = None,
        include_scheduled: bool = False,
    ) -> list[SessionSummary]:
        """列出会话摘要（按 ``updated_at`` 倒序）。

        Args:
            archived: ``False`` 返回普通列表，``True`` 返回已归档列表。
            query: 可选标题搜索。搜索在合成后的展示标题上执行。
        """
        metadata = self._metadata_store.load_all() if self._metadata_store is not None else {}
        summaries = []
        for summary in self._store.list():
            item = metadata.get(summary.session_id)
            summaries.append(
                dataclasses.replace(
                    summary,
                    title=item.display_title or summary.title,
                    archived_at=item.archived_at,
                    approval_mode=item.approval_mode,
                    approval_mode_version=item.approval_mode_version,
                )
                if item is not None
                else summary
            )
        query_text = query.strip().casefold() if query else ""
        result: list[SessionSummary] = []
        for summary in summaries:
            if not include_scheduled and summary.origin_kind == "scheduled_task":
                continue
            if (summary.archived_at is not None) != archived:
                continue
            if query_text and query_text not in summary.title.casefold():
                continue
            result.append(summary)
        return result

    def rename(self, session_id: str, title: str) -> SessionSummary:
        """更新会话展示标题，不修改 JSONL 首行。"""
        normalized = title.strip()
        if not normalized:
            raise ValueError("title 不能为空")
        if len(normalized) > 80:
            raise ValueError("title 不能超过 80 个字符")
        self.query(session_id)
        if self._metadata_store is not None:
            self._metadata_store.set_display_title(session_id, normalized)
        if self._session_change_broker is not None:
            self._session_change_broker.record_change(
                session_id, change_kind="metadata", origin="session_management"
            )
        return self._summary(session_id)

    def is_archived(self, session_id: str) -> bool:
        return (
            self._metadata_store is not None
            and self._metadata_store.get(session_id).archived_at is not None
        )

    def archive(self, session_id: str) -> SessionSummary:
        """归档会话。"""
        self.query(session_id)
        if self._metadata_store is not None:
            self._metadata_store.set_archived_at(session_id, datetime.now(UTC))
        if self._session_change_broker is not None:
            self._session_change_broker.record_change(
                session_id, change_kind="archived", origin="session_management"
            )
        return self._summary(session_id)

    def unarchive(self, session_id: str) -> SessionSummary:
        """取消归档会话。"""
        self.query(session_id)
        if self._metadata_store is not None:
            self._metadata_store.set_archived_at(session_id, None)
        if self._session_change_broker is not None:
            self._session_change_broker.record_change(
                session_id, change_kind="unarchived", origin="session_management"
            )
        return self._summary(session_id)

    def delete(self, session_id: str) -> None:
        """删除指定会话（hard delete）。"""
        state = self._runtime_states.pop(session_id, None)
        lease = (
            state.writer_lease
            if state is not None
            else acquire_session_writer_lease(self._store, session_id)
        )
        try:
            if self._memory is not None:
                self._memory.discard_pending_session(session_id)
            self._store.delete(session_id)
        finally:
            lease.release()
        delete_session_writer_lease(self._store, session_id)
        if self._metadata_store is not None:
            self._metadata_store.delete(session_id)
        self._delete_command_workspace(session_id)
        if self._tool_execution_evidence_store is not None:
            try:
                self._tool_execution_evidence_store.delete_session(session_id)
            except ToolExecutionEvidenceError:
                logger.exception(
                    "failed to remove deleted session tool execution evidence",
                    extra={"session_id": session_id},
                )

    def _delete_command_workspace(self, session_id: str) -> None:
        root = self._command_workspaces_root
        if root is None:
            return
        try:
            target = session_workspace_path(root, session_id)
        except ValueError:
            logger.warning("skip empty command workspace session id")
            return
        try:
            if target.exists():
                reject_linked_path(target)
                shutil.rmtree(target, onexc=remove_readonly_file)
        except (OSError, ValueError):
            logger.exception(
                "failed to remove deleted session command workspace",
                extra={"session_id": session_id, "path": str(target)},
            )

    def delete_archived(self, *, query: str | None = None) -> builtins.list[str]:
        """删除当前已归档会话，返回成功删除的 session ids。"""
        targets = self.list(archived=True, query=query)
        deleted: builtins.list[str] = []
        for summary in targets:
            self.delete(summary.session_id)
            deleted.append(summary.session_id)
        return deleted

    def latest(self) -> Session | None:
        """最近活跃会话的执行投影；无会话则 ``None``。

        Note:
            CLI 的 ``--resume`` 默认值用，流式验证历史并恢复当前模型状态。
            列表展示场景请用 :meth:`list`（O(1) per 文件）。
        """
        for summary in self._store.list():
            if summary.origin_kind != "scheduled_task":
                return self.open_for_execution(summary.session_id)
        return None

    def _summary(self, session_id: str) -> SessionSummary:
        for summary in self._store.list():
            if summary.session_id == session_id:
                return self._apply_metadata(summary)
        # list() 与 load() 的一致性通常应成立；若中间被其它进程删除，交给 load 抛出。
        self._store.load(session_id)
        raise RuntimeError(f"session summary missing after load: {session_id}")

    def _apply_metadata(self, summary: SessionSummary) -> SessionSummary:
        if self._metadata_store is None:
            return summary
        metadata = self._metadata_store.get(summary.session_id)
        title = metadata.display_title or summary.title
        return dataclasses.replace(
            summary,
            title=title,
            archived_at=metadata.archived_at,
            approval_mode=metadata.approval_mode,
            approval_mode_version=metadata.approval_mode_version,
        )

    # ----- 装配 Conversation -----

    def start_conversation(
        self,
        session: Session,
        *,
        tool_hook_invoker: ToolHookInvoker | None = None,
        tool_pre_hook: PreToolHook | None = None,
        tool_post_hook: PostToolHook | None = None,
        tool_run_policy: ToolRunPolicy | None = None,
        tool_run_context_factory: Callable[[str], ToolRunContext] | None = None,
        post_turn_external: bool = False,
        cancel_check: CancelCheck = None,
    ) -> Conversation:
        """根据 ``session.current_persona_id / current_model`` 装配 :class:`Conversation`。

        Args:
            session: 目标 session（用 :meth:`create` 或 :meth:`open` 拿到）。
            tool_hook_invoker: 014 起新增；非 ``None`` 时 :meth:`Conversation._invoke_tool_safely`
                会把 tool 调用路由到 invoker（让 PreToolUse / PostToolUse hook 介入）。
                默认 ``None`` 保留 002~013 行为。
            post_turn_external: 014 起新增；``True`` 时 :meth:`Conversation.stream`
                finally 块不再调硬编码的 ``_observe_turn``——由外部（``AgentRuntime``
                的默认 PostTurn hook）接管 ``memory.observe``。默认 ``False`` 保留
                002~013 行为。

        Returns:
            完全装配好的 Conversation 实例，可直接调用 ``send`` / ``stream`` /
            ``switch_persona`` / ``switch_model``。

        Raises:
            AgentError: ``llm_client_factory`` / ``prompt_builder_factory`` /
                ``context_manager_factory`` 任一未注入。
            LLMAuthError / ValueError: ``llm_client_factory`` 抛出。
            PersonaNotFoundError: 当前 persona id（或老 session name fallback）
                在 catalog 找不到。
        """
        check_cancel(cancel_check)
        state = self._runtime_states.get(session.session_id)
        if state is not None:
            session = self.open_for_execution(session.session_id, cancel_check=cancel_check)
        if (
            self._llm_client_factory is None
            or self._prompt_builder_factory is None
            or self._context_manager_factory is None
        ):
            raise AgentError(
                "start_conversation 需要 llm_client_factory / prompt_builder_factory / "
                "context_manager_factory 全部注入；当前 SessionManager 未配置完整。"
            )
        from ..conversation import Conversation  # 避免顶层循环 import
        from ..personas import PersonaCatalog  # 同上

        opened_here = session.session_id not in self._runtime_states
        runtime_state = self._runtime_states.get(session.session_id)
        if runtime_state is None:
            writer_lease = acquire_session_writer_lease(self._store, session.session_id)
            try:
                session, runtime_state = self._restore_runtime_state(
                    session, writer_lease, cancel_check=cancel_check
                )
                check_cancel(cancel_check)
                validate = getattr(writer_lease, "validate", None)
                if callable(validate):
                    validate()
            except BaseException:
                writer_lease.release()
                raise
        else:
            session = runtime_state.rollout_recorder.session
        try:
            catalog = PersonaCatalog()

            # 解析当前 persona id：优先 session.current_persona_id；老 session（None）
            # fallback：按 current_persona_name 查 user 优先
            persona_id = session.current_persona_id
            if persona_id is None:
                from ..errors import PersonaNotFoundError

                try:
                    info = catalog.find_by_name(session.current_persona_name)
                    persona_id = info.id
                except PersonaNotFoundError:
                    # 老 session 引用的 persona 已不存在 → fallback 到 builtin default
                    import warnings

                    from ..personas import BUILTIN_DEFAULT_PERSONA_ID

                    warnings.warn(
                        f"老 session 引用的 persona name "
                        f"{session.current_persona_name!r} 在 catalog 找不到；"
                        f"fallback 到 builtin default",
                        stacklevel=2,
                    )
                    persona_id = BUILTIN_DEFAULT_PERSONA_ID

            check_cancel(cancel_check)
            llm_client = self._llm_client_factory(session.current_model_config)

            # 007 起：把 factory 包一层，让返回的 PromptBuilder 实例绑当前 session。
            # 动态 channel 已移出稳定 system prompt，但自定义 builder 仍可能依赖
            # ``with_session``，因此保留兼容绑定。
            base_factory = self._prompt_builder_factory

            def _session_aware_factory(pid: str) -> PromptBuilder:
                builder = base_factory(pid)
                with_session = getattr(builder, "with_session", None)
                if callable(with_session):
                    bound: PromptBuilder = with_session(session)
                    return bound
                return builder

            prompt_builder = _session_aware_factory(persona_id)
            check_cancel(cancel_check)
            conversation = Conversation(
                session=session,
                store=self._store,
                llm_client=llm_client,
                context_manager=runtime_state.context_manager,
                prompt_builder=prompt_builder,
                llm_client_factory=self._llm_client_factory,
                prompt_builder_factory=_session_aware_factory,
                catalog=catalog,
                memory=self._memory,
                tool_registry=self._tool_registry,
                tool_hook_invoker=tool_hook_invoker,
                tool_pre_hook=tool_pre_hook,
                tool_post_hook=tool_post_hook,
                tool_run_policy=tool_run_policy,
                tool_run_context_factory=tool_run_context_factory,
                post_turn_external=post_turn_external,
                message_renderer=self._message_renderer,
                message_budget_renderer=self._message_budget_renderer,
                message_projector=self._message_projector,
                session_file_service=self._session_file_service,
                skill_service=self._skill_service,
                session_model_history=runtime_state.model_history,
                session_model_context=runtime_state.model_context,
                prompt_cache_trace_tracker=runtime_state.prompt_cache_trace_tracker,
                cache_epoch=runtime_state.cache_epoch,
                rollout_recorder=runtime_state.rollout_recorder,
                time_provider=self._time_provider,
                approval_mode_provider=lambda: self.approval_mode_state(session),
                tool_execution_evidence_store=self._tool_execution_evidence_store,
            )
            check_cancel(cancel_check)
        except BaseException:
            if opened_here:
                runtime_state.writer_lease.release()
            raise
        self._runtime_states[session.session_id] = runtime_state
        return conversation

    def _restore_runtime_state(
        self,
        session: Session,
        writer_lease: SessionWriterLease,
        *,
        cancel_check: CancelCheck = None,
    ) -> tuple[Session, _SessionRuntimeState]:
        context_manager_factory = self._context_manager_factory
        check_cancel(cancel_check)
        if context_manager_factory is None:  # guarded by start_conversation
            raise AgentError("context_manager_factory 未配置")
        can_reuse = getattr(self._store, "can_reuse_loaded", None)
        revalidate_snapshot = getattr(self._store, "revalidate_execution_snapshot", None)
        source_unchanged = not callable(can_reuse) or can_reuse(session)
        if source_unchanged and callable(revalidate_snapshot):
            source_unchanged = revalidate_snapshot(session, cancel_check=cancel_check)
        if not source_unchanged:
            # Opening may precede the writer lease. Revalidate every cold source,
            # including conversations with no tool transactions to reconcile.
            session = self.open_for_execution(session.session_id, cancel_check=cancel_check)
        if self._transaction_reconciler is not None and any(
            event.type == "tool_call_request" for event in session.events
        ):
            session = self._transaction_reconciler.reconcile_session(
                session.session_id, cancel_check=cancel_check, loaded_session=session
            ).session
        check_cancel(cancel_check)
        restored = session.restored_rollout
        session.restored_rollout = None
        if restored is None:
            restored = restore_rollout(session, cancel_check=cancel_check)
        elif restored.disposition not in {
            "incompatible_rollout_schema",
            "corrupt_committed_history",
        }:
            restored = finish_rollout_restore(session, restored.commit, cancel_check=cancel_check)
        check_cancel(cancel_check)
        if restored.runtime_blocked:
            raise SessionCorruptError(
                f"会话 {session.session_id} 的 rollout 无有效提交点，"
                f"无法建立可写 runtime: {restored.reason}"
            )
        bind_projection = getattr(self._store, "bind_execution_projection", None)
        if callable(bind_projection):
            bind_projection(session)
        trace_tracker = PromptCacheTraceTracker()
        if restored.commit is not None and restored.commit.cache_diagnostics is not None:
            cache_diagnostics = restored.commit.cache_diagnostics
            finished_at = cache_diagnostics.get("finished_at_unix_ms")
            trace_tracker.restore(
                cache_diagnostics,
                finished_at_unix_ms=(finished_at if isinstance(finished_at, int) else 0),
            )
        if (
            restored.exact_continuation_candidate
            and restored.commit is not None
            and restored.commit.final_wire_witness is not None
        ):
            cache_diagnostics = restored.commit.cache_diagnostics or {}
            lineage = cache_diagnostics.get("cross_turn_lineage")
            lineage = lineage if isinstance(lineage, dict) else {}
            input_tokens = lineage.get("input_tokens")
            response_model = cache_diagnostics.get("response_model") or lineage.get(
                "response_model"
            )
            trace_tracker.restore_exact_witness(
                restored.commit.final_wire_witness,
                input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                response_model=(response_model if isinstance(response_model, str) else None),
            )
        recorder = SessionRolloutRecorder(
            store=self._store,
            session=session,
            restored=restored.commit,
            recovery_reason=restored.reason,
            starts_after_corruption=(restored.disposition == "truncated_tail"),
        )
        recovery_disposition = restored.disposition
        if restored.migration_required:
            recorder.commit_legacy_migration(
                history=restored.history,
                model_context=restored.model_context,
                cache_epoch=restored.cache_epoch,
            )
            recovery_disposition = "legacy_migrated"
        restored.cache_epoch.recovery_disposition = recovery_disposition
        restored.cache_epoch.exact_continuation_candidate = restored.exact_continuation_candidate
        return session, _SessionRuntimeState(
            model_history=restored.history,
            model_context=restored.model_context,
            context_manager=context_manager_factory(),
            prompt_cache_trace_tracker=trace_tracker,
            cache_epoch=restored.cache_epoch,
            rollout_recorder=recorder,
            writer_lease=writer_lease,
            recovery_disposition=recovery_disposition,
            exact_continuation_candidate=restored.exact_continuation_candidate,
            runtime_instance_id=self._runtime_instance_id,
            runtime_start_reason=self._runtime_start_reason,
        )
