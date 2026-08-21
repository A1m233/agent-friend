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
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from agent.approvals import ApprovalBroker
from agent.command_runtime import CodexIdentityError, CommandRuntime, verify_codex_executable
from agent.filesystem import FilesystemApprovalBroker, ProtectedPathPolicy
from agent.filesystem.policy import canonical_path
from agent.memory_search import SessionHistorySource
from agent.paths import log_dir
from agent.runtime import AgentRuntime
from agent.todos import ReminderScheduler, TodoService, TodoStore
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
    SessionRevisionStore,
    SessionSearchService,
    SessionStore,
    SkillCatalogService,
    default_context_manager,
    make_default_registry,
    make_default_social_platform_service,
)
from llm_providers import LanguageModel, LanguageModelFactory, MultimodalInputAdapter
from memory import Memory, MemorySearchService, build_memory
from runtime_config import (
    DesktopSettingsReader,
    config_mode_from_env,
    credential_source_for_mode,
)

from .agent_runtime_factory import build_agent_runtime
from .dev.recall_buffer import RecallBuffer
from .presence import PresenceRegistry
from .reminders import ReminderDeliveryOrchestrator
from .runtime_config import (
    BridgeRuntimeConfig,
    DeferredLanguageModel,
    resolve_bridge_runtime_config,
)
from .session_models import SessionModelService
from .session_turns import SessionTurnCoordinator
from .settings import BridgeSettings
from .social_auth import SocialAuthOrchestrator

if TYPE_CHECKING:
    from agent.tools.builtin.social_platforms import SocialPlatformService

    from .protocols.im import IMRuntime, OnboardSessionRegistry

from .self_presentation import DesktopSelfPresentationModeProvider

logger = logging.getLogger(__name__)


def _llm_factory(model: str) -> LanguageModel:
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
    attachment_store: AttachmentStore | None = None
    llm_client_factory: LanguageModelFactory | None = None
    memory: Memory | None = None
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
    reminder_scheduler: ReminderScheduler | None = None
    """070：Bridge lifespan 内运行的可恢复 Reminder scheduler。"""
    social_platform_service: SocialPlatformService | None = None
    """072：共享的本地只读平台 connector 与 opaque resource 生命周期。"""
    social_auth_orchestrator: SocialAuthOrchestrator | None = None
    """072：桌面批准登录、Agent continuation 与原 surface 投递编排。"""
    filesystem_approval_broker: FilesystemApprovalBroker | None = None
    """078：桌面文件授权请求与一次性决定的进程级 broker。"""
    command_runtime: CommandRuntime | None = None
    """085：进程级 Codex exec-server 命令运行时；不可用时其它能力继续运行。"""

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
        )

    def close(self) -> None:
        """进程退出时调用：drain 抽取队列并关库（幂等）。"""
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
    self_presentation_mode_provider = DesktopSelfPresentationModeProvider(
        desktop_settings_reader or DesktopSettingsReader()
    )
    prompt_builder_factory = _make_prompt_factory(
        prompt_catalog,
        self_presentation_mode_provider,
    )
    if runtime_config is None:
        runtime_config = resolve_bridge_runtime_config(
            credential_source_for_mode(config_mode_from_env())
        )
    default_persona = catalog.find_by_name("default").name
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
    filesystem_approval_broker = ApprovalBroker()
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
    )
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
        log_dir(),
        default_settings_path(),
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

    memory: Memory | None = None
    recall_buffer: RecallBuffer | None = None
    memory_search = MemorySearchService([SessionHistorySource(persistent_store)])
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

    todo_store = TodoStore(settings.todos_db or settings.sessions_dir.parent / "todos" / "todos.db")
    todo_service = TodoService(todo_store)
    presence_registry = PresenceRegistry()
    social_platform_service = make_default_social_platform_service()

    # 048 M48.1: 工具只依赖统一 service；memory 关闭时 service 仍保留 raw history source。
    tool_registry = make_default_registry(
        persistent_store,
        memory_search=memory_search,
        todo_service=todo_service,
        session_catalog=session_catalog,
        tavily_api_key=runtime_config.tool_credentials.tavily_api_key,
        amap_webservice_key=runtime_config.tool_credentials.amap_webservice_key,
        qweather_api_key=runtime_config.tool_credentials.qweather_api_key,
        qweather_api_host=runtime_config.tool_credentials.qweather_api_host,
        social_platform_service=social_platform_service,
        skill_service=skill_service,
        filesystem_approval_broker=filesystem_approval_broker,
        filesystem_policy=filesystem_policy,
        command_runtime=command_runtime,
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
    )

    turn_coordinator = SessionTurnCoordinator()
    agent_runtime = build_agent_runtime(
        settings=settings,
        session_manager=persistent_session_manager,
        memory=memory,
        session_turn_guard=turn_coordinator.hold,
    )
    session_change_broker.set_publisher(agent_runtime.listeners.publish_session_change)
    filesystem_approval_broker.add_listener(agent_runtime.listeners.publish_approval_change)
    command_runtime.add_listener(agent_runtime.listeners.publish_command_process_change)
    command_runtime.add_sandbox_capability_listener(
        agent_runtime.listeners.publish_sandbox_capability_change
    )
    session_model_service = SessionModelService(
        persistent_session_manager,
        turn_coordinator,
        agent_runtime,
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
        from .protocols.im.dedupe import RecentMessageDeduper
        from .protocols.im.inbound_resolver import CompositeInboundResolver
        from .protocols.im.media import QQFaceResolver
        from .protocols.im.voice import QQVoiceMessageResolver, SafeAudioDownloader
        from .protocols.im.voice_bridge_client import VoiceBridgeTranscriptionClient
        from .session_bridge import SessionBridge as _SessionBridge

        credentials = CredentialStore(base_dir=settings.im_credentials_dir)
        # SessionBridge 用 partial BridgeRuntime 在下一步装配后立即引用;此处先
        # 构造 router/runtime,session_bridge 后续在装配 final BridgeRuntime 后注入。
        # 但 IMRouter 只读 (default_persona, default_model, session_bridge);
        # session_bridge 也只读 .persistent_session_manager + .catalog —— 都已就位。
        # 所以我们可以**预先**构造 session_bridge,把它的 _runtime 指向后面 final 的 BridgeRuntime。
        # 实现:用临时占位 BridgeRuntime 调 SessionBridge 构造,等 final 装配后,
        # 由于 session_bridge 内部 self._runtime 永远引用最初传入的对象,我们需要让那个
        # 对象就是 final BridgeRuntime。
        # 简化:直接 first 构造 final BridgeRuntime(不含 IM),再装 IM,最后用
        # dataclasses.replace 给 BridgeRuntime 加 IM 字段。
        pre_runtime = BridgeRuntime(
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
            attachment_store=attachment_store,
            memory=memory,
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
            presence_registry=presence_registry,
            social_platform_service=social_platform_service,
            filesystem_approval_broker=filesystem_approval_broker,
            command_runtime=command_runtime,
        )
        session_bridge = _SessionBridge(pre_runtime)
        im_router = IMRouter(
            session_bridge=session_bridge,
            default_persona=default_persona,
            default_model=default_model,
            agent_runtime=agent_runtime,
        )
        im_state_store = IMStateStore(settings.im_state_path)
        im_binding_coordinator = IMBindingCoordinator()
        im_control_service = IMControlService(
            sessions=persistent_session_manager,
            state_store=im_state_store,
            coordinator=im_binding_coordinator,
            model_service=session_model_service,
        )
        im_deduper = RecentMessageDeduper(
            ttl_seconds=settings.im_voice_dedupe_ttl_seconds,
            max_items=settings.im_voice_dedupe_max_items,
        )
        im_inbound_resolver = CompositeInboundResolver(
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
        )
        im_onboard_registry = _OnboardSessionRegistry(im_runtime=im_runtime)
        social_auth_orchestrator = SocialAuthOrchestrator(
            service=social_platform_service,
            agent_runtime=agent_runtime,
            im_runtime=im_runtime,
        )
        reminder_delivery = ReminderDeliveryOrchestrator(
            todo_store=todo_store,
            session_store=persistent_store,
            agent_runtime=agent_runtime,
            presence_registry=presence_registry,
            im_runtime=im_runtime,
        )
        reminder_scheduler = ReminderScheduler(todo_store, reminder_delivery.deliver_batch)
        todo_service.add_wake_listener(reminder_scheduler.wake)
        # final BridgeRuntime = pre_runtime + IM 字段
        return dataclasses.replace(
            pre_runtime,
            im_runtime=im_runtime,
            im_onboard_registry=im_onboard_registry,
            reminder_scheduler=reminder_scheduler,
            social_auth_orchestrator=social_auth_orchestrator,
        )

    reminder_delivery = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=persistent_store,
        agent_runtime=agent_runtime,
        presence_registry=presence_registry,
    )
    reminder_scheduler = ReminderScheduler(todo_store, reminder_delivery.deliver_batch)
    todo_service.add_wake_listener(reminder_scheduler.wake)
    social_auth_orchestrator = SocialAuthOrchestrator(
        service=social_platform_service,
        agent_runtime=agent_runtime,
    )
    return BridgeRuntime(
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
        attachment_store=attachment_store,
        memory=memory,
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
        presence_registry=presence_registry,
        reminder_scheduler=reminder_scheduler,
        social_platform_service=social_platform_service,
        social_auth_orchestrator=social_auth_orchestrator,
        filesystem_approval_broker=filesystem_approval_broker,
        command_runtime=command_runtime,
    )
