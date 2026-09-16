"""bridge 启动期一次性装配。

把 ``agent-cli`` ``__main__.py`` 已经在用的装配模式抽取出来，让 bridge 进程
也能跑同一套：``SessionManager`` + ``PersonaCatalog`` + 上下文管理工厂
（``default_context_manager``，009 起默认摘要压缩 + FIFO 兜底）
+ ``ToolRegistry`` + ``LanguageModelFactory`` + ``PromptBuilderFactory``。

bridge 进程是**单实例多请求**模型——这些组件在进程启动时装配一次，所有请求
共享同一份；不能在每个 HTTP 请求里重装。

详见 docs/requirements/006-agent-bridge/design.md §4.2 与 R-4.3.1。
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import platform
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from agent.approvals import ApprovalBroker, CatastrophicActionPolicy
from agent.command_runtime import CodexIdentityError, CommandRuntime, verify_codex_executable
from agent.desktop_applications import NativeApplicationLauncher
from agent.execution_environment import (
    ExecutionEnvironmentManager,
    ManagedEnvironmentError,
    ProviderIdentity,
    PythonEnvironmentProvider,
)
from agent.filesystem import ProtectedPathPolicy
from agent.filesystem.policy import canonical_path
from agent.memory_search import SessionHistorySource, SessionStoreSourceOrderProvider
from agent.notifications import NotificationStore
from agent.paths import (
    command_workspaces_dir,
    log_dir,
    notifications_db_path,
    tool_execution_evidence_dir,
)
from agent.runtime import (
    PRE_TURN_PROCEED,
    AgentEvent,
    AgentRuntime,
    HookKind,
    PostTurnContext,
    PreTurnDecision,
    RuntimeInstanceIdentity,
)
from agent.scheduled_tasks import (
    ScheduledTaskDriver,
    ScheduledTaskService,
    ScheduledTaskStore,
)
from agent.scheduling import ScheduleCoordinator
from agent.sessions import ToolExecutionEvidenceStore, ToolTransactionReconciler
from agent.time_provider import SystemTimeProvider, TimeProvider
from agent.todos import ReminderDriver, TodoService, TodoStore
from agent.tools import ToolRegistry
from runtime_config.build_identity import runtime_build_identity
from runtime_config.sources import default_settings_path

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    AttachmentStore,
    EffectivePersonaCatalog,
    EffectivePersonaStore,
    FirstPartySkillStore,
    FirstPartySkillUpdateService,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    NotifyingSessionStore,
    NullSessionStore,
    PersonaCatalog,
    PersonaEditorPromptBuilder,
    PersonaEditorWorkspace,
    ProposePersonaTool,
    SessionCatalog,
    SessionChangeBroker,
    SessionManager,
    SessionMetadataStore,
    SessionNotFoundError,
    SessionRevisionStore,
    SessionSearchService,
    SessionStore,
    SkillCatalogService,
    default_context_manager,
    make_default_registry,
    make_default_social_platform_service,
)
from llm_providers import (
    LanguageModel,
    LanguageModelFactory,
    ModelRuntimeConfig,
    MultimodalInputAdapter,
)
from memory import Memory, MemorySearchService, build_memory
from runtime_config import (
    DesktopSettingsReader,
    config_mode_from_env,
    credential_source_for_mode,
)

from .agent_runtime_factory import build_agent_runtime
from .dev.recall_buffer import RecallBuffer
from .model_connections.runtime import ConnectionRuntime
from .notifications import NotificationService
from .notifications.generation import NotificationGenerator
from .presence import PresenceRegistry
from .reminder_settings import DesktopReminderNotificationModeProvider
from .reminders import ReminderDeliveryOrchestrator
from .runtime_config import (
    BridgeRuntimeConfig,
    DeferredLanguageModel,
    resolve_bridge_runtime_config,
)
from .scheduled_tasks import ScheduledRunTracker, ScheduledTaskRunOrchestrator
from .session_models import SessionModelService
from .session_turns import SessionTurnCoordinator
from .settings import BridgeSettings
from .social_auth import SocialAuthOrchestrator

if TYPE_CHECKING:
    from agent.tools.builtin.social_platforms import SocialPlatformService

    from .protocols.im import IMRuntime, OnboardSessionRegistry

from .self_presentation import DesktopSelfPresentationModeProvider

logger = logging.getLogger(__name__)

RUNTIME_START_REASON_ENV = "AGENT_FRIEND_BRIDGE_START_REASON"
RUNTIME_SUPERVISOR_GENERATION_ENV = "AGENT_FRIEND_BRIDGE_SUPERVISOR_GENERATION"


def _runtime_identity_from_env() -> RuntimeInstanceIdentity:
    raw_generation = os.environ.get(RUNTIME_SUPERVISOR_GENERATION_ENV)
    generation: int | None = None
    if raw_generation:
        try:
            parsed = int(raw_generation)
        except ValueError:
            logger.warning("invalid bridge supervisor generation: %r", raw_generation)
        else:
            if parsed >= 0:
                generation = parsed
            else:
                logger.warning("negative bridge supervisor generation: %r", raw_generation)
    return RuntimeInstanceIdentity.create(
        supervisor_generation=generation,
        start_reason=os.environ.get(RUNTIME_START_REASON_ENV),
    )


def _llm_factory(model: ModelRuntimeConfig) -> LanguageModel:
    raise RuntimeError("BridgeRuntime.llm_client_factory is required before creating sessions")


def _make_prompt_factory(
    catalog: PersonaCatalog | EffectivePersonaCatalog,
    self_presentation_mode_provider: DesktopSelfPresentationModeProvider,
) -> Callable[[str], MarkdownPromptBuilder]:
    """构造 prompt builder 工厂，绑定用户 persona 目录。

    让 meta REST 的 :class:`PersonaCatalog` 与对话期 prompt 构建读取**同一个**
    用户 persona 目录（默认系统用户数据目录，可被 ``AGENT_BRIDGE_PERSONAS_DIR``
    或 ``AGENT_FRIEND_DATA_DIR`` 覆盖），避免两者分叉。
    """

    def factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(
            persona_id=persona_id,
            catalog=cast(PersonaCatalog, catalog),
            self_presentation_mode_provider=self_presentation_mode_provider,
        )

    return factory


@dataclass(frozen=True)
class BridgeRuntime:
    """bridge 进程级共享运行时。

    所有 HTTP 请求处理函数通过 :func:`get_runtime` 拿到本对象；它在进程启动期
    由 :func:`build_runtime` 构造一次。

    Attributes:
        settings: 解析后的 :class:`BridgeSettings`。
        persistent_store: 持久化 store（JSONL），AG-UI / OpenAI 升级模式用。
            ``persistent_session_manager`` 持有它的引用——本字段保留供 meta
            路由直接做 :meth:`SessionStore.list` / :meth:`load` 等只读查询。
        transient_store: ``NullSessionStore`` 实例；OpenAI 默认无状态分支用。
        persistent_session_manager: 进程级唯一的持久化 :class:`SessionManager`
            （注入 ``persistent_store``）。AG-UI 出口 + meta 路由共享同一实例，
            保证 ``in-memory session cache``、``store`` 文件锁行为在多请求间一致。
        catalog: persona catalog。
        tool_registry: 全局工具注册表。
        default_persona: 自动创建 session 时的默认 persona name（slug）。
        default_model: 没指定 model 时的默认值（从 ``DEEPSEEK_MODEL`` 解析）。
        memory: 进程级长期记忆门面（008）；``None`` 表示本进程未启用记忆
            （``settings.memory_enabled=False``）。**只挂在持久化分支**——
            OpenAI 无状态分支跑完即丢，不接记忆。退出时由 :meth:`close` drain。
        recall_buffer: 026 起 dev 期 inspector 的召回 trace ring buffer；
            ``memory_enabled=False`` 时为 ``None``。
    """

    settings: BridgeSettings
    persistent_store: SessionStore
    transient_store: SessionStore
    persistent_session_manager: SessionManager
    catalog: PersonaCatalog
    tool_registry: ToolRegistry
    prompt_builder_factory: Callable[[str], MarkdownPromptBuilder]
    default_persona: str
    default_model: str
    runtime_identity: RuntimeInstanceIdentity = field(
        default_factory=RuntimeInstanceIdentity.create
    )
    time_provider: TimeProvider = field(default_factory=SystemTimeProvider)
    attachment_store: AttachmentStore | None = None
    llm_client_factory: LanguageModelFactory | None = None
    connection_runtime: ConnectionRuntime | None = None
    memory: Memory | None = None
    transaction_reconciler: ToolTransactionReconciler | None = None
    recall_buffer: RecallBuffer | None = None
    skill_service: SkillCatalogService | None = None
    first_party_skill_updater: FirstPartySkillUpdateService | None = None
    """092：固定首方 Skill 的低频 release updater；源码运行时默认禁用网络。"""
    turn_coordinator: SessionTurnCoordinator = field(default_factory=SessionTurnCoordinator)
    """Per-session turn coordinator for persistent mutating channels."""
    agent_runtime: AgentRuntime | None = None
    """014 起新增：进程级 :class:`AgentRuntime`，main loop 调度内核。

    在 :func:`build_runtime` 中装配；由 FastAPI lifespan 调用
    :meth:`AgentRuntime.start` / :meth:`stop`。``None`` 仅在过渡期 /
    测试 fixture 不需要 main loop 时出现。
    """
    session_model_service: SessionModelService | None = None
    """Shared model mutation path used by desktop REST and IM controls."""
    session_search_service: SessionSearchService | None = None
    """074：进程级全局会话搜索派生索引；None 只用于旧测试 fixture。"""
    im_runtime: IMRuntime | None = None
    """022 起新增:进程级 :class:`agent_bridge.protocols.im.IMRuntime`,
    管理 IM 长连(QQ / 未来飞书 / TG)。``None`` 表示本进程未启用 IM 通道
    (例如 OpenAI 无状态分支或测试 fixture)。

    在 :func:`build_runtime` 装配,由 FastAPI lifespan 调用 :meth:`IMRuntime.start`
    /:meth:`stop`,与 :attr:`agent_runtime` 生命周期对齐。
    """
    im_onboard_registry: OnboardSessionRegistry | None = None
    """022 起新增:进程级 IM 扫码 onboard 异步 task 注册表。
    跟 :attr:`im_runtime` 一起装配,供 ``/v1/im/onboard/*`` 路由用。
    """
    persona_store: EffectivePersonaStore | None = None
    """050：默认 persona 的唯一用户覆盖存储。None 仅用于旧测试 fixture。"""
    persona_editor_session_manager: SessionManager | None = None
    """050：独立于正式历史与记忆的人格编辑 Agent 会话。"""
    persona_editor_workspace: PersonaEditorWorkspace | None = None
    """050：持久化编辑会话绑定的正式人格基准 revision。"""
    todo_store: TodoStore | None = None
    """070：Todo/Reminder 独立 SQLite store。"""
    todo_service: TodoService | None = None
    """070：Agent tool、REST 与调度共享的领域服务。"""
    presence_registry: PresenceRegistry | None = None
    """070：桌面/IM 活跃信号和最近路由诊断。"""
    scheduled_task_store: ScheduledTaskStore | None = None
    """099：全局 Scheduled Agent Task SQLite store。"""
    scheduled_task_service: ScheduledTaskService | None = None
    """099：Agent tool、REST 与调度共享的 Scheduled Task 应用服务。"""
    scheduled_run_tracker: ScheduledRunTracker | None = None
    """099：把 AgentRuntime handle 终态持久化回 occurrence。"""
    schedule_coordinator: ScheduleCoordinator | None = None
    """099：Reminder 与 Scheduled Task 共享的进程级时间 coordinator。"""
    notification_service: NotificationService | None = None
    """117: Durable notification generation and desktop presentation lifecycle."""
    social_platform_service: SocialPlatformService | None = None
    """072：共享的本地只读平台 connector 与 opaque resource 生命周期。"""
    social_auth_orchestrator: SocialAuthOrchestrator | None = None
    """072：桌面批准登录、Agent continuation 与原 surface 投递编排。"""
    approval_broker: ApprovalBroker | None = None
    """桌面文件与命令授权请求、会话决策模式及高危策略的进程级 broker。"""
    command_runtime: CommandRuntime | None = None
    application_launch_evidence: ToolExecutionEvidenceStore | None = None
    """085：进程级 Codex exec-server 命令运行时；不可用时其它能力继续运行。"""
    execution_environment_manager: ExecutionEnvironmentManager | None = None
    """096：共享 base、session overlay 与 run snapshot 的进程级 owner。"""

    def attachment_capabilities(self, model_id: str):  # type: ignore[no-untyped-def]
        from llm_providers import model_capabilities

        return (
            self.connection_runtime.directory().model(model_id).capabilities
            if self.connection_runtime
            else model_capabilities(model_id)
        )

    def make_transient_session_manager(self) -> SessionManager:
        """构造 OpenAI 无状态分支用的临时 :class:`SessionManager`。

        每个 OpenAI 请求都新建一个临时 :class:`SessionManager` + in-memory
        :class:`Session`，跑完即丢——所有 ``store.*`` 调用都是 no-op
        （:class:`NullSessionStore`）。装配开销 < 1ms，本期不缓存。

        **不挂记忆**：无状态分支没有持久身份，记下来也无处归属。
        """
        return SessionManager(
            store=self.transient_store,
            llm_client_factory=self.llm_client_factory or _llm_factory,
            prompt_builder_factory=self.prompt_builder_factory,
            context_manager_factory=default_context_manager,
            tool_registry=self.tool_registry,
            skill_service=self.skill_service,
            time_provider=self.time_provider,
        )

    def close(self) -> None:
        """进程退出时调用：drain 抽取队列并关库（幂等）。"""
        if self.notification_service is not None:
            self.notification_service.stop()
        self.persistent_session_manager.close()
        if self.persona_editor_session_manager is not None:
            self.persona_editor_session_manager.close()
        if self.first_party_skill_updater is not None:
            self.first_party_skill_updater.stop()
        if self.skill_service is not None:
            self.skill_service.stop()
        if self.social_auth_orchestrator is not None:
            self.social_auth_orchestrator.close()
        if self.social_platform_service is not None:
            self.social_platform_service.close()
        if self.command_runtime is not None:
            self.command_runtime.close()
        if self.execution_environment_manager is not None:
            self.execution_environment_manager.close()
        if self.memory is not None:
            self.memory.close()


def _make_persistent_session_manager(
    store: SessionStore,
    tool_registry: ToolRegistry,
    memory: Memory | None,
    prompt_builder_factory: Callable[[str], MarkdownPromptBuilder],
    llm_client_factory: LanguageModelFactory,
    metadata_store: SessionMetadataStore | None = None,
    session_change_broker: SessionChangeBroker | None = None,
    multimodal_adapter: MultimodalInputAdapter | None = None,
    skill_service: SkillCatalogService | None = None,
    time_provider: TimeProvider | None = None,
    transaction_reconciler: ToolTransactionReconciler | None = None,
    tool_execution_evidence_store: ToolExecutionEvidenceStore | None = None,
    runtime_identity: RuntimeInstanceIdentity | None = None,
) -> SessionManager:
    from agent.paths import command_workspaces_dir

    return SessionManager(
        store=store,
        llm_client_factory=llm_client_factory,
        prompt_builder_factory=prompt_builder_factory,
        context_manager_factory=default_context_manager,
        tool_registry=tool_registry,
        memory=memory,
        metadata_store=metadata_store,
        session_change_broker=session_change_broker,
        message_renderer=(multimodal_adapter.render_message if multimodal_adapter else None),
        skill_service=skill_service,
        command_workspaces_root=command_workspaces_dir(),
        time_provider=time_provider,
        transaction_reconciler=transaction_reconciler,
        tool_execution_evidence_store=tool_execution_evidence_store,
        runtime_instance_id=(runtime_identity.bridge_instance_id if runtime_identity else ""),
        runtime_start_reason=(
            runtime_identity.start_reason.value if runtime_identity else "direct_start"
        ),
    )


def make_persona_editor_session_manager(
    *,
    store: EffectivePersonaStore,
    sessions_dir: Path,
    llm_client_factory: LanguageModelFactory,
    workspace: PersonaEditorWorkspace,
) -> SessionManager:
    """构造人格编辑专用 manager；目录、prompt、工具均不与正式会话共享。"""
    editor_store = JsonlSessionStore(sessions_dir)
    return SessionManager(
        store=editor_store,
        llm_client_factory=llm_client_factory,
        prompt_builder_factory=lambda _persona_id: PersonaEditorPromptBuilder(store),
        context_manager_factory=NaiveContextManager,
        tool_registry=ToolRegistry(
            [ProposePersonaTool(store, base_revision_provider=workspace.base_revision)]
        ),
        memory=None,
    )


def build_runtime(
    settings: BridgeSettings,
    *,
    runtime_config: BridgeRuntimeConfig | None = None,
    desktop_settings_reader: DesktopSettingsReader | None = None,
) -> BridgeRuntime:
    """进程启动期一次性装配 :class:`BridgeRuntime`。

    Raises:
        agent.SessionPersistError: ``settings.sessions_dir`` 创建失败。
        llm_providers.LLMAuthError: ``DEEPSEEK_API_KEY`` 缺失或非法（仅做格式预检；
            真正调 LLM 时仍可能再次抛）。
    """
    runtime_identity = _runtime_identity_from_env()
    logger.info(
        "[RuntimeContinuity] bridge_runtime_started runtime_instance_id=%s "
        "supervisor_generation=%s start_reason=%s",
        runtime_identity.bridge_instance_id,
        runtime_identity.supervisor_generation,
        runtime_identity.start_reason.value,
    )
    base_persistent_store = JsonlSessionStore(settings.sessions_dir)
    attachment_store = AttachmentStore(settings.attachments_dir)
    multimodal_adapter = MultimodalInputAdapter(
        open_asset=attachment_store.open,
        cache_dir=settings.attachments_dir / "provider-cache",
    )
    session_change_broker = SessionChangeBroker(
        SessionRevisionStore(settings.sessions_dir / "session-revisions.json")
    )
    persistent_store = NotifyingSessionStore(base_persistent_store, session_change_broker)
    session_metadata_store = SessionMetadataStore(settings.sessions_dir / "metadata.json")
    session_catalog = SessionCatalog(persistent_store, session_metadata_store)
    session_search_service = SessionSearchService(
        persistent_store,
        session_catalog,
        session_change_broker,
    )
    transient_store = NullSessionStore()
    catalog = PersonaCatalog(external_dir=settings.personas_dir)
    persona_store = EffectivePersonaStore(
        user_file=settings.personas_dir / "default" / "persona.md",
        internal_dir=settings.personas_dir / ".internal" / "default",
        builtin_loader=lambda: catalog.read_content(BUILTIN_DEFAULT_PERSONA_ID),
    )
    # 050：persona.md 是唯一公开人格正文。Bridge 启动时即创建，避免把文件存在与否
    # 暴露成用户需要理解的“默认 / 自定义”产品状态。
    persona_store.materialize()
    prompt_catalog = EffectivePersonaCatalog(persona_store, catalog)
    resolved_desktop_settings_reader = desktop_settings_reader or DesktopSettingsReader()
    self_presentation_mode_provider = DesktopSelfPresentationModeProvider(
        resolved_desktop_settings_reader
    )
    reminder_notification_mode_provider = DesktopReminderNotificationModeProvider(
        resolved_desktop_settings_reader
    )
    prompt_builder_factory = _make_prompt_factory(
        prompt_catalog,
        self_presentation_mode_provider,
    )
    if runtime_config is None:
        runtime_config = resolve_bridge_runtime_config(
            credential_source_for_mode(config_mode_from_env())
        )
    default_persona_info = catalog.find_by_name("default")
    default_persona = default_persona_info.name
    default_model = runtime_config.default_model
    personal_skills_root = settings.sessions_dir.parent / "skills"
    first_party_skill_store = FirstPartySkillStore(personal_skills_root)

    def builtin_overrides() -> dict[str, Path | None]:
        root, _source, _reference, _state = first_party_skill_store.resolve_effective()
        return {first_party_skill_store.config.skill_name: root}

    skill_service = SkillCatalogService(
        personal_root=personal_skills_root,
        builtin_override_provider=builtin_overrides,
    )
    skill_service.initialize()
    try:
        build_identity = runtime_build_identity()
    except RuntimeError:
        logger.warning("invalid runtime build identity; first-party Skill updates disabled")
        build_identity = None
    raw_product_version = build_identity.get("productVersion") if build_identity else None
    product_version = raw_product_version if isinstance(raw_product_version, str) else None
    first_party_skill_updater = FirstPartySkillUpdateService(
        store=first_party_skill_store,
        catalog=skill_service,
        product_version=product_version,
    )
    codex_path_value = os.environ.get("AGENT_FRIEND_CODEX_EXECUTABLE")
    codex_executable: Path | None = None
    if codex_path_value:
        try:
            codex_executable = verify_codex_executable(Path(codex_path_value))
        except CodexIdentityError:
            logger.warning("governed Codex payload rejected", exc_info=True)
    command_runtime_root = settings.sessions_dir.parent / "command-runtime"
    command_runtime = CommandRuntime(
        codex_executable,
        codex_home=command_runtime_root / "codex-home",
        execution_root=Path(tempfile.gettempdir()) / "agent-friend-command-execution",
        runtime_instance_id=runtime_identity.bridge_instance_id,
        diagnostics_path=log_dir() / "command-runtime-diagnostics.json",
        build_id=str(build_identity.get("buildId", "")) if build_identity else "",
        payload_status="validated"
        if codex_executable
        else "rejected"
        if codex_path_value
        else "unknown",
    )
    tool_evidence_store = ToolExecutionEvidenceStore(
        tool_execution_evidence_dir(settings.sessions_dir.parent)
    )
    command_runtime.add_evidence_listener(tool_evidence_store.observe_process_snapshot)
    execution_environment_root = settings.sessions_dir.parent / "execution-environments"
    try:
        python_environment_provider = PythonEnvironmentProvider.from_runtime(
            data_root=execution_environment_root
        )
    except ManagedEnvironmentError:
        logger.warning(
            "managed Python payload rejected; ordinary chat remains available", exc_info=True
        )
        python_environment_provider = PythonEnvironmentProvider(
            data_root=execution_environment_root,
            base_executable=execution_environment_root / "unavailable" / "python",
            uv_executable=None,
            identity=ProviderIdentity(
                provider="python",
                runtime_version="unknown",
                identity="unavailable",
                platform=sys.platform,
                architecture=platform.machine().casefold(),
            ),
        )
    execution_environment_manager = ExecutionEnvironmentManager((python_environment_provider,))
    execution_environment_manager.start()
    builtin_skill_roots = (canonical_path(skill_service.personal_root / ".system"),)
    product_paths = [
        settings.sessions_dir,
        settings.memory_db,
        settings.personas_dir,
        settings.attachments_dir,
        settings.im_credentials_dir,
        settings.im_resume_dir,
        settings.im_state_path,
        settings.todos_db or settings.sessions_dir.parent / "todos" / "todos.db",
        settings.scheduled_tasks_db
        or settings.sessions_dir.parent / "scheduled-tasks" / "scheduled-tasks.db",
        log_dir(),
        default_settings_path(),
        tool_execution_evidence_dir(settings.sessions_dir.parent),
    ]
    if getattr(sys, "frozen", False):
        product_paths.append(Path(sys.executable).resolve().parent)
    product_roots = tuple(canonical_path(path) for path in product_paths)
    filesystem_policy = ProtectedPathPolicy(
        product_roots=product_roots,
        personal_skill_roots=(
            canonical_path(skill_service.personal_root),
            canonical_path(skill_service.shared_root),
        ),
        builtin_skill_roots=builtin_skill_roots,
    )

    def session_exists(session_id: str) -> bool:
        try:
            persistent_store.load(session_id)
            return True
        except SessionNotFoundError:
            return False

    def session_workspace_root(session_id: str) -> Path | None:
        try:
            from agent.command_runtime.workspace import session_workspace_path

            return session_workspace_path(command_workspaces_dir(), session_id)
        except ValueError:
            return None

    catastrophic_action_policy = CatastrophicActionPolicy(
        critical_roots=product_roots,
        workspace_root_provider=session_workspace_root,
    )

    def record_approval_mode_change(session_id: str, _state: object) -> None:
        session_change_broker.record_change(
            session_id,
            change_kind="metadata",
            origin="approval_mode",
        )

    approval_broker = ApprovalBroker(
        mode_store=session_metadata_store,
        risk_policy=catastrophic_action_policy,
        session_exists=session_exists,
        on_mode_changed=record_approval_mode_change,
    )

    memory: Memory | None = None
    recall_buffer: RecallBuffer | None = None
    memory_search = MemorySearchService(
        [SessionHistorySource(persistent_store)],
        source_order_provider=SessionStoreSourceOrderProvider(persistent_store),
    )
    if settings.memory_enabled:
        # 048：core 使用独立小预算始终在场，priority / normal 统一走相关性召回；
        # bridge 与 CLI 不再通过 pinned gate hot-fix 产生入口差异。
        recall_buffer = RecallBuffer()
        memory = build_memory(
            settings.memory_db,
            DeferredLanguageModel(runtime_config.memory_client_factory),
            on_retrieved=recall_buffer.record,
            search_service=memory_search,
            dense_model_dir=settings.memory_model_dir,
        )
        try:
            memory.warmup()
        except Exception:
            logger.warning("memory warmup failed; continuing startup", exc_info=True)

    time_provider = SystemTimeProvider()
    todo_store = TodoStore(settings.todos_db or settings.sessions_dir.parent / "todos" / "todos.db")
    todo_service = TodoService(todo_store, clock=time_provider.now)
    scheduled_task_store = ScheduledTaskStore(
        settings.scheduled_tasks_db
        or settings.sessions_dir.parent / "scheduled-tasks" / "scheduled-tasks.db"
    )
    scheduled_task_service = ScheduledTaskService(scheduled_task_store, clock=time_provider.now)
    presence_registry = PresenceRegistry()
    social_platform_service = make_default_social_platform_service()

    # 048 M48.1: 工具只依赖统一 service；memory 关闭时 service 仍保留 raw history source。
    tool_registry = make_default_registry(
        persistent_store,
        memory_search=memory_search,
        todo_service=todo_service,
        scheduled_task_service=scheduled_task_service,
        scheduled_task_persona_id=default_persona_info.id,
        scheduled_task_persona_name=default_persona,
        scheduled_task_model_id=default_model,
        session_catalog=session_catalog,
        tavily_api_key=runtime_config.tool_credentials.tavily_api_key,
        amap_webservice_key=runtime_config.tool_credentials.amap_webservice_key,
        qweather_api_key=runtime_config.tool_credentials.qweather_api_key,
        qweather_api_host=runtime_config.tool_credentials.qweather_api_host,
        social_platform_service=social_platform_service,
        skill_service=skill_service,
        approval_broker=approval_broker,
        filesystem_policy=filesystem_policy,
        command_runtime=command_runtime,
        execution_environment_manager=execution_environment_manager,
        tool_execution_evidence_store=tool_evidence_store,
        desktop_application_launcher=NativeApplicationLauncher(
            Path(os.environ["AGENT_FRIEND_APP_LAUNCHER"])
            if os.environ.get("AGENT_FRIEND_APP_LAUNCHER")
            else (
                Path(sys.executable).resolve().parent.parent
                / "desktop-applications"
                / "agent-friend-app-launcher.exe"
                if getattr(sys, "frozen", False)
                else None
            ),
            build_identity=json.dumps(build_identity) if build_identity else None,
        ),
        attachment_store=attachment_store,
    )

    llm_client_factory = runtime_config.llm_client_factory
    persona_editor_workspace = PersonaEditorWorkspace(
        persona_store,
        settings.personas_dir / ".internal" / "default" / "editor-base-revision.txt",
    )
    persona_editor_session_manager = make_persona_editor_session_manager(
        store=persona_store,
        sessions_dir=settings.personas_dir / ".internal" / "default" / "editor-sessions",
        llm_client_factory=llm_client_factory,
        workspace=persona_editor_workspace,
    )
    transaction_reconciler = ToolTransactionReconciler(
        persistent_store,
        runtime_instance_id=runtime_identity.bridge_instance_id,
        tool_execution_evidence_store=tool_evidence_store,
    )
    persistent_session_manager = _make_persistent_session_manager(
        persistent_store,
        tool_registry,
        memory,
        prompt_builder_factory,
        llm_client_factory,
        session_metadata_store,
        session_change_broker,
        multimodal_adapter,
        skill_service,
        time_provider,
        transaction_reconciler,
        tool_evidence_store,
        runtime_identity,
    )

    turn_coordinator = SessionTurnCoordinator()
    agent_runtime = build_agent_runtime(
        settings=settings,
        session_manager=persistent_session_manager,
        memory=memory,
        session_turn_guard=turn_coordinator.hold,
        runtime_identity=runtime_identity,
    )

    def prepare_execution_environment(event: AgentEvent) -> PreTurnDecision:
        if event.session_id:
            execution_environment_manager.prepare_before_turn(event.session_id)
        return PRE_TURN_PROCEED

    def release_execution_environment(context: PostTurnContext) -> None:
        if context.event.session_id and context.run_id:
            execution_environment_manager.release_run(
                context.event.session_id,
                context.run_id,
            )

    agent_runtime.register_hook(HookKind.PRE_TURN, prepare_execution_environment)
    agent_runtime.register_hook(HookKind.POST_TURN, release_execution_environment)
    session_change_broker.set_publisher(agent_runtime.listeners.publish_session_change)
    approval_broker.add_listener(agent_runtime.listeners.publish_approval_change)
    command_runtime.add_listener(agent_runtime.listeners.publish_command_process_change)
    command_runtime.add_sandbox_capability_listener(
        agent_runtime.listeners.publish_sandbox_capability_change
    )
    session_model_service = SessionModelService(
        persistent_session_manager,
        turn_coordinator,
        agent_runtime,
    )

    notification_service = NotificationService(
        store=NotificationStore(notifications_db_path(settings.sessions_dir.parent)),
        sessions=persistent_store,
        presence=presence_registry,
        reader=resolved_desktop_settings_reader,
        generator=NotificationGenerator(
            reader=resolved_desktop_settings_reader,
            factory=llm_client_factory,
            persona=persona_store,
            memory=memory,
        ),
        publisher=agent_runtime.listeners.publish_notification_change,
        memory=memory,
        todos=todo_store,
        approvals=approval_broker,
        session_metadata_store=session_metadata_store,
    )
    agent_runtime.add_terminal_observer(notification_service.on_terminal)
    agent_runtime.listeners.manage_ambient_notifications()
    transaction_reconciler.add_terminal_observer(notification_service.on_recovered)
    approval_broker.add_listener(notification_service.on_approval)

    def make_schedule_runtime(
        reminder_delivery: ReminderDeliveryOrchestrator,
    ) -> tuple[ScheduledRunTracker, ScheduleCoordinator]:
        tracker = ScheduledRunTracker(scheduled_task_store)
        orchestrator = ScheduledTaskRunOrchestrator(
            store=scheduled_task_store,
            sessions=persistent_session_manager,
            agent_runtime=agent_runtime,
            tracker=tracker,
            skill_service=skill_service,
        )
        coordinator = ScheduleCoordinator(
            (
                ScheduledTaskDriver(
                    scheduled_task_store,
                    orchestrator.dispatch_batch,
                ),
                ReminderDriver(todo_store, reminder_delivery.deliver_batch),
            )
        )
        scheduled_task_service.add_wake_listener(coordinator.wake)
        todo_service.add_wake_listener(coordinator.wake)
        return tracker, coordinator

    base_runtime = BridgeRuntime(
        connection_runtime=runtime_config.connection_runtime,
        settings=settings,
        persistent_store=persistent_store,
        transient_store=transient_store,
        persistent_session_manager=persistent_session_manager,
        catalog=catalog,
        tool_registry=tool_registry,
        prompt_builder_factory=prompt_builder_factory,
        llm_client_factory=llm_client_factory,
        default_persona=default_persona,
        default_model=default_model,
        runtime_identity=runtime_identity,
        time_provider=time_provider,
        attachment_store=attachment_store,
        memory=memory,
        transaction_reconciler=transaction_reconciler,
        recall_buffer=recall_buffer,
        skill_service=skill_service,
        first_party_skill_updater=first_party_skill_updater,
        turn_coordinator=turn_coordinator,
        agent_runtime=agent_runtime,
        session_model_service=session_model_service,
        session_search_service=session_search_service,
        persona_store=persona_store,
        persona_editor_session_manager=persona_editor_session_manager,
        persona_editor_workspace=persona_editor_workspace,
        todo_store=todo_store,
        todo_service=todo_service,
        scheduled_task_store=scheduled_task_store,
        scheduled_task_service=scheduled_task_service,
        presence_registry=presence_registry,
        social_platform_service=social_platform_service,
        approval_broker=approval_broker,
        command_runtime=command_runtime,
        execution_environment_manager=execution_environment_manager,
        application_launch_evidence=tool_evidence_store,
    )

    # 022:IM 通道装配(SessionBridge 复用现有 persistent path,session_id 由 IMRouter 决定)
    im_runtime: IMRuntime | None = None
    im_onboard_registry: OnboardSessionRegistry | None = None
    if settings.im_enabled:
        from .protocols.im import (
            CredentialStore,
            IMBindingCoordinator,
            IMControlService,
            IMRouter,
            IMStateStore,
        )
        from .protocols.im import IMRuntime as _IMRuntime
        from .protocols.im import OnboardSessionRegistry as _OnboardSessionRegistry
        from .protocols.im.attachments import IMAttachmentResolver
        from .protocols.im.coalescing import QQAttachmentTextCoalescer
        from .protocols.im.dedupe import RecentMessageDeduper
        from .protocols.im.inbound_resolver import CompositeInboundResolver
        from .protocols.im.media import QQFaceResolver
        from .protocols.im.qq_references import QQReferenceResolver
        from .protocols.im.reference_aliases import IMReferenceAliasIndex
        from .protocols.im.voice import QQVoiceMessageResolver, SafeAudioDownloader
        from .protocols.im.voice_bridge_client import VoiceBridgeTranscriptionClient
        from .session_bridge import SessionBridge as _SessionBridge

        credentials = CredentialStore(base_dir=settings.im_credentials_dir)
        # IM consumes the same common runtime as the desktop routes.
        session_bridge = _SessionBridge(base_runtime)
        im_reference_aliases = IMReferenceAliasIndex(settings.im_reference_alias_path)
        im_router = IMRouter(
            session_bridge=session_bridge,
            default_persona=default_persona,
            default_model=default_model,
            agent_runtime=agent_runtime,
            reference_aliases=im_reference_aliases,
            capability_resolver=base_runtime.attachment_capabilities,
        )
        im_state_store = IMStateStore(settings.im_state_path)
        im_binding_coordinator = IMBindingCoordinator()
        im_inbound_coalescer = QQAttachmentTextCoalescer(
            im_binding_coordinator,
            window_seconds=3.0,
        )
        from .model_connections.catalog import connection_model_catalog

        im_control_service = IMControlService(
            sessions=persistent_session_manager,
            state_store=im_state_store,
            coordinator=im_binding_coordinator,
            model_service=session_model_service,
            catalog=lambda: connection_model_catalog(base_runtime.connection_runtime),
        )
        im_deduper = RecentMessageDeduper(
            ttl_seconds=settings.im_voice_dedupe_ttl_seconds,
            max_items=settings.im_voice_dedupe_max_items,
        )
        im_inbound_resolver = CompositeInboundResolver(
            QQReferenceResolver(
                im_reference_aliases,
                persistent_session_manager.open,
            ),
            QQFaceResolver(),
            QQVoiceMessageResolver(
                downloader=SafeAudioDownloader(),
                transcriber=VoiceBridgeTranscriptionClient(settings.voice_bridge_base_url),
                max_audio_bytes=settings.im_voice_max_audio_bytes,
            ),
            IMAttachmentResolver(attachment_store),
        )
        im_runtime = _IMRuntime(
            router=im_router,
            credentials=credentials,
            resume_base_dir=settings.im_resume_dir,
            inbound_resolver=im_inbound_resolver,
            deduper=im_deduper,
            state_store=im_state_store,
            coordinator=im_binding_coordinator,
            control_service=im_control_service,
            session_exists=session_bridge.session_exists,
            inbound_coalescer=im_inbound_coalescer,
        )
        im_onboard_registry = _OnboardSessionRegistry(im_runtime=im_runtime)
    reminder_delivery = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=persistent_store,
        agent_runtime=agent_runtime,
        presence_registry=presence_registry,
        im_runtime=im_runtime,
        notification_mode_provider=reminder_notification_mode_provider,
        pet_notification=notification_service.on_todo,
    )
    scheduled_run_tracker, schedule_coordinator = make_schedule_runtime(reminder_delivery)
    social_auth_orchestrator = SocialAuthOrchestrator(
        service=social_platform_service,
        agent_runtime=agent_runtime,
        im_runtime=im_runtime,
    )
    return dataclasses.replace(
        base_runtime,
        im_runtime=im_runtime,
        im_onboard_registry=im_onboard_registry,
        scheduled_run_tracker=scheduled_run_tracker,
        schedule_coordinator=schedule_coordinator,
        notification_service=notification_service,
        social_auth_orchestrator=social_auth_orchestrator,
    )
