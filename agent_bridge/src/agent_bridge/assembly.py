"""bridge 启动期一次性装配。

把 ``agent-cli`` ``__main__.py`` 已经在用的装配模式抽取出来，让 bridge 进程
也能跑同一套：``SessionManager`` + ``PersonaCatalog`` + 上下文管理工厂
（``default_context_manager``，009 起默认摘要压缩 + FIFO 兜底）
+ ``ToolRegistry`` + ``LLMClientFactory`` + ``PromptBuilderFactory``。

bridge 进程是**单实例多请求**模型——这些组件在进程启动时装配一次，所有请求
共享同一份；不能在每个 HTTP 请求里重装。

详见 docs/requirements/006-agent-bridge/design.md §4.2 与 R-4.3.1。
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agent.runtime import AgentRuntime
from agent.tools import ToolRegistry

from agent import (
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NullSessionStore,
    PersonaCatalog,
    SessionManager,
    SessionStore,
    default_context_manager,
    make_default_registry,
)
from llm_providers import LLMClient, ProviderSpec
from memory import Memory, build_memory

from .agent_runtime_factory import build_agent_runtime
from .dev.recall_buffer import RecallBuffer
from .settings import BridgeSettings

if TYPE_CHECKING:
    from .protocols.im import IMRuntime, OnboardSessionRegistry


logger = logging.getLogger(__name__)


def _make_spec_with_thinking_off(model_override: str | None = None) -> ProviderSpec:
    """构造 :class:`ProviderSpec`，并对 DeepSeek V4 系列默认关闭 thinking 模式。

    与 ``tools/cli/__main__.py`` 中同名 helper 行为一致——抽取到 bridge 包内
    避免 bridge ↘ tools 反向依赖。后续若再有第 3 处需要可统一搬到 agent 核心库。
    """
    spec = ProviderSpec.from_env(prefix="DEEPSEEK")
    if model_override:
        spec = dataclasses.replace(spec, model=model_override)
    return _disable_deepseek_thinking(spec)


def _make_memory_spec_with_thinking_off() -> ProviderSpec:
    """构造记忆抽取专用 spec，默认走 DeepSeek V4 Pro。"""
    spec = ProviderSpec.from_env(
        prefix="DEEPSEEK",
        model_env_var="DEEPSEEK_MEMORY_MODEL",
        default_model_key="memory_model",
    )
    return _disable_deepseek_thinking(spec)


def _disable_deepseek_thinking(spec: ProviderSpec) -> ProviderSpec:
    """DeepSeek V4 默认关闭 thinking，保持主对话与记忆抽取延迟可控。"""
    if "deepseek" in spec.model.lower():
        return dataclasses.replace(
            spec,
            defaults={
                **spec.defaults,
                "extra_body": {"thinking": {"type": "disabled"}},
            },
        )
    return spec


def _llm_factory(model: str) -> LLMClient:
    return LLMClient(_make_spec_with_thinking_off(model))


def _memory_llm_client() -> LLMClient:
    return LLMClient(_make_memory_spec_with_thinking_off())


def _make_prompt_factory(personas_dir: Path) -> Callable[[str], MarkdownPromptBuilder]:
    """构造 prompt builder 工厂，绑定用户 persona 目录。

    让 meta REST 的 :class:`PersonaCatalog` 与对话期 prompt 构建读取**同一个**
    用户 persona 目录（默认系统用户数据目录，可被 ``AGENT_BRIDGE_PERSONAS_DIR``
    或 ``AGENT_FRIEND_DATA_DIR`` 覆盖），避免两者分叉。
    """

    def factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id, external_dir=personas_dir)

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
    memory: Memory | None = None
    recall_buffer: RecallBuffer | None = None
    agent_runtime: AgentRuntime | None = None
    """014 起新增：进程级 :class:`AgentRuntime`，main loop 调度内核。

    在 :func:`build_runtime` 中装配；由 FastAPI lifespan 调用
    :meth:`AgentRuntime.start` / :meth:`stop`。``None`` 仅在过渡期 /
    测试 fixture 不需要 main loop 时出现。
    """
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

    def make_transient_session_manager(self) -> SessionManager:
        """构造 OpenAI 无状态分支用的临时 :class:`SessionManager`。

        每个 OpenAI 请求都新建一个临时 :class:`SessionManager` + in-memory
        :class:`Session`，跑完即丢——所有 ``store.*`` 调用都是 no-op
        （:class:`NullSessionStore`）。装配开销 < 1ms，本期不缓存。

        **不挂记忆**：无状态分支没有持久身份，记下来也无处归属。
        """
        return SessionManager(
            store=self.transient_store,
            llm_client_factory=_llm_factory,
            prompt_builder_factory=self.prompt_builder_factory,
            context_manager_factory=default_context_manager,
            tool_registry=self.tool_registry,
        )

    def close(self) -> None:
        """进程退出时调用：drain 抽取队列并关库（幂等）。"""
        if self.memory is not None:
            self.memory.close()


def _make_persistent_session_manager(
    store: SessionStore,
    tool_registry: ToolRegistry,
    memory: Memory | None,
    prompt_builder_factory: Callable[[str], MarkdownPromptBuilder],
) -> SessionManager:
    return SessionManager(
        store=store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=prompt_builder_factory,
        context_manager_factory=default_context_manager,
        tool_registry=tool_registry,
        memory=memory,
    )


def build_runtime(settings: BridgeSettings) -> BridgeRuntime:
    """进程启动期一次性装配 :class:`BridgeRuntime`。

    Raises:
        agent.SessionPersistError: ``settings.sessions_dir`` 创建失败。
        llm_providers.LLMAuthError: ``DEEPSEEK_API_KEY`` 缺失或非法（仅做格式预检；
            真正调 LLM 时仍可能再次抛）。
    """
    persistent_store = JsonlSessionStore(settings.sessions_dir)
    transient_store = NullSessionStore()
    catalog = PersonaCatalog(external_dir=settings.personas_dir)
    prompt_builder_factory = _make_prompt_factory(settings.personas_dir)
    # 020:传 persistent_store → 注册 recall_past_chats 工具,让 LLM 能跨 session
    # 翻历史对话(否则 IM session 这种新 session 撞到"recall_past_chats 未注册"错误)。
    tool_registry = make_default_registry(session_store=persistent_store)
    default_persona = catalog.find_by_name("default").name
    default_model = _make_spec_with_thinking_off().model

    memory: Memory | None = None
    recall_buffer: RecallBuffer | None = None
    if settings.memory_enabled:
        # 022 hot-fix(issue 016):pinned_relevance_gate=False 让身份核心 pinned 始终注入。
        # 默认 lenient gate 在"你还记得我吗" / "我们之前聊过什么"这类只表达记忆查询意图、
        # 不含身份关键词的 query 上,会把"用户叫 Alex"误判为不相关过滤掉,导致 agent
        # 体感"失忆"(违反决策 0001 §1.3 "记忆是第一护城河"原则)。完整 root cause +
        # 长期方案见 docs/issues/016-memory-pinned-gate-misses-identity/。
        # 026: 保持 pinned_relevance_gate=False；inspector 的价值之一就是让 dev 能看到
        # 这一 hot-fix 状态。不要顺手改回 True，那是 issue 016 长期方案的范畴。
        recall_buffer = RecallBuffer()
        memory = build_memory(
            settings.memory_db,
            _memory_llm_client(),
            on_retrieved=recall_buffer.record,
            pinned_relevance_gate=False,
        )
        try:
            memory.warmup()
        except Exception:
            logger.warning("memory warmup failed; continuing startup", exc_info=True)

    persistent_session_manager = _make_persistent_session_manager(
        persistent_store, tool_registry, memory, prompt_builder_factory
    )

    agent_runtime = build_agent_runtime(
        settings=settings,
        session_manager=persistent_session_manager,
        memory=memory,
    )

    # 022:IM 通道装配(SessionBridge 复用现有 persistent path,session_id 由 IMRouter 决定)
    im_runtime: IMRuntime | None = None
    im_onboard_registry: OnboardSessionRegistry | None = None
    if settings.im_enabled:
        from .protocols.im import (
            CredentialStore,
            IMRouter,
        )
        from .protocols.im import IMRuntime as _IMRuntime
        from .protocols.im import OnboardSessionRegistry as _OnboardSessionRegistry
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
            default_persona=default_persona,
            default_model=default_model,
            memory=memory,
            recall_buffer=recall_buffer,
            agent_runtime=agent_runtime,
        )
        session_bridge = _SessionBridge(pre_runtime)
        im_router = IMRouter(
            session_bridge=session_bridge,
            default_persona=default_persona,
            default_model=default_model,
        )
        im_runtime = _IMRuntime(
            router=im_router,
            credentials=credentials,
            resume_base_dir=settings.im_resume_dir,
        )
        im_onboard_registry = _OnboardSessionRegistry(im_runtime=im_runtime)
        # final BridgeRuntime = pre_runtime + IM 字段
        return dataclasses.replace(
            pre_runtime,
            im_runtime=im_runtime,
            im_onboard_registry=im_onboard_registry,
        )

    return BridgeRuntime(
        settings=settings,
        persistent_store=persistent_store,
        transient_store=transient_store,
        persistent_session_manager=persistent_session_manager,
        catalog=catalog,
        tool_registry=tool_registry,
        prompt_builder_factory=prompt_builder_factory,
        default_persona=default_persona,
        default_model=default_model,
        memory=memory,
        recall_buffer=recall_buffer,
        agent_runtime=agent_runtime,
    )
