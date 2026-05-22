"""bridge 启动期一次性装配。

把 ``agent-cli`` ``__main__.py`` 已经在用的装配模式抽取出来，让 bridge 进程
也能跑同一套：``SessionManager`` + ``PersonaCatalog`` + ``NaiveContextManager``
+ ``ToolRegistry`` + ``LLMClientFactory`` + ``PromptBuilderFactory``。

bridge 进程是**单实例多请求**模型——这些组件在进程启动时装配一次，所有请求
共享同一份；不能在每个 HTTP 请求里重装。

详见 docs/requirements/006-agent-bridge/design.md §4.2 与 R-4.3.1。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from agent.tools import ToolRegistry

from agent import (
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    NullSessionStore,
    PersonaCatalog,
    SessionManager,
    SessionStore,
    make_default_registry,
)
from llm_providers import LLMClient, ProviderSpec

from .settings import BridgeSettings


def _make_spec_with_thinking_off(model_override: str | None = None) -> ProviderSpec:
    """构造 :class:`ProviderSpec`，并对 DeepSeek V4 系列默认关闭 thinking 模式。

    与 ``tools/cli/__main__.py`` 中同名 helper 行为一致——抽取到 bridge 包内
    避免 bridge ↘ tools 反向依赖。后续若再有第 3 处需要可统一搬到 agent 核心库。
    """
    spec = ProviderSpec.from_env(prefix="DEEPSEEK")
    if model_override:
        spec = dataclasses.replace(spec, model=model_override)
    if "deepseek" in spec.model.lower():
        spec = dataclasses.replace(
            spec,
            defaults={
                **spec.defaults,
                "extra_body": {"thinking": {"type": "disabled"}},
            },
        )
    return spec


def _llm_factory(model: str) -> LLMClient:
    return LLMClient(_make_spec_with_thinking_off(model))


def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
    return MarkdownPromptBuilder(persona_id=persona_id)


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
    """

    settings: BridgeSettings
    persistent_store: SessionStore
    transient_store: SessionStore
    persistent_session_manager: SessionManager
    catalog: PersonaCatalog
    tool_registry: ToolRegistry
    default_persona: str
    default_model: str

    def make_transient_session_manager(self) -> SessionManager:
        """构造 OpenAI 无状态分支用的临时 :class:`SessionManager`。

        每个 OpenAI 请求都新建一个临时 :class:`SessionManager` + in-memory
        :class:`Session`，跑完即丢——所有 ``store.*`` 调用都是 no-op
        （:class:`NullSessionStore`）。装配开销 < 1ms，本期不缓存。
        """
        return SessionManager(
            store=self.transient_store,
            llm_client_factory=_llm_factory,
            prompt_builder_factory=_prompt_factory,
            context_manager=NaiveContextManager(),
            tool_registry=self.tool_registry,
        )


def _make_persistent_session_manager(
    store: SessionStore, tool_registry: ToolRegistry
) -> SessionManager:
    return SessionManager(
        store=store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager=NaiveContextManager(),
        tool_registry=tool_registry,
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
    catalog = PersonaCatalog()
    tool_registry = make_default_registry()
    persistent_session_manager = _make_persistent_session_manager(persistent_store, tool_registry)
    default_persona = catalog.find_by_name("default").name
    default_model = _make_spec_with_thinking_off().model

    return BridgeRuntime(
        settings=settings,
        persistent_store=persistent_store,
        transient_store=transient_store,
        persistent_session_manager=persistent_session_manager,
        catalog=catalog,
        tool_registry=tool_registry,
        default_persona=default_persona,
        default_model=default_model,
    )
