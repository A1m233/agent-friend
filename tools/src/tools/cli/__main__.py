"""agent-friend CLI 入口（M2.3 · 引擎层会话管理，懒加载会话）。

用法::

    ./scripts/cli/run.sh                          # 启动后**未绑定会话**，首条消息触发创建
    ./scripts/cli/run.sh --persona cute_friend   # 指定初始人设（pending，首消息生效）
    ./scripts/cli/run.sh --model deepseek/deepseek-v4-pro  # 指定初始 model
    ./scripts/cli/run.sh --resume                # 用户显式想恢复：立刻 open 最近会话
    ./scripts/cli/run.sh --resume <session_id>   # 显式 id（支持前缀）：立刻 open

    # bridge 模式：远端 agent-bridge 作 backend
    ./scripts/cli/run.sh --bridge http://127.0.0.1:18800

需要在项目根 ``.env`` 中配置 ``DEEPSEEK_API_KEY``（**仅 in-process 模式**——
bridge 模式 LLM 凭证在 bridge 进程那边，CLI 不需要）。

M2 范围（详见 002 design.md §4.8 + progress.md 实施日志）：

- 会话作为引擎层一等公民：CLI 全程通过 :class:`SessionManager` 操作会话生命周期
- **懒加载策略**：不像 M0.3 那样"启动即建"会话。用户没发消息 + 没 ``/new`` /
  ``/open`` 就 ``/quit``，**不会留下空 session 文件**。``/persona`` ``/model``
  在未绑定时只更新 pending 默认值，首条消息时一次性应用
- 持久化：每条消息 / 切换都**实时**落盘到系统标准用户数据目录下的
  ``sessions/{session_id}.jsonl``（路径见 :mod:`agent.paths`，决策 0002 §3.19）
- 新 slash 命令：``/sessions`` ``/open`` ``/persona`` ``/model`` ``/new``
- ``/reset`` **已删除**（语义与 ``/new`` 重复，统一用 ``/new``）
- 持久化错误（``SessionPersistError``）红字提示但不中断主循环

M6.3 bridge 模式（详见 006 design.md §4.7）：

- ``--bridge URL`` 或 ``AGENT_BRIDGE_URL`` 启用 bridge 模式
- 此模式下 CLI **不**初始化 :class:`SessionManager` / :class:`LLMClient` /
  :class:`PersonaCatalog`，所有状态在远端 bridge
- ``/sessions`` / ``/open`` / ``/persona`` / ``/model`` 全部走 bridge meta REST
- 对话走 AG-UI ``POST /ag-ui/run``，client 反解 SSE 事件成 ConversationEvent，
  现有渲染层原样复用
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import clear as pt_clear
from rich.console import Console
from rich.table import Table

from agent import (
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    PersonaCatalog,
    PersonaInfo,
    PersonaNotFoundError,
    Session,
    SessionCorruptError,
    SessionManager,
    SessionNotFoundError,
    SessionPersistError,
    SessionSummary,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    TurnDone,
    cli_history_path,
    default_context_manager,
    make_default_registry,
    memory_db_path,
    random_fallback,
    sessions_dir,
    user_data_dir,
)
from llm_providers import (
    LLMAuthError,
    LLMBadRequestError,
    LLMClient,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
    ProviderSpec,
)
from memory import ExtractionResult, Memory, build_memory

from .bridge_client import BridgeClient, BridgeRunError, BridgeSessionSummary

# 用户数据落系统标准目录（决策 0002 §3.19），可用 AGENT_FRIEND_DATA_DIR 覆盖。
DATA_DIR = user_data_dir()
SESSIONS_DIR = sessions_dir()
MEMORY_DB = memory_db_path()
HISTORY_FILE = cli_history_path()

MEMORY_DEBUG_ENV = "AGENT_FRIEND_MEMORY_DEBUG"

RESUME_LATEST_SENTINEL = "__latest__"

stdout = Console()
stderr = Console(stderr=True)


# ===== argparse / 工厂 =====


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="agent-friend",
        description="agent-friend CLI · M2 引擎层会话管理（懒加载会话）",
    )
    p.add_argument(
        "--persona",
        default="default",
        help="初始 persona（pending；未绑定时为默认值），对应用户数据目录下的 "
        'personas/{name}.md 或内置 personas（默认 "default"）',
    )
    p.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help="初始 model（pending；未绑定时为默认值），不传则用 .env 的 DEEPSEEK_MODEL 或内置默认",
    )
    p.add_argument(
        "--resume",
        nargs="?",
        const=RESUME_LATEST_SENTINEL,
        default=None,
        metavar="SESSION_ID",
        help="**显式恢复**已有会话；不带参数取最近活跃；带 id（支持前缀）则打开指定。"
        "未指定 --resume 时启动后处于**未绑定**状态，首条消息才创建新会话",
    )
    p.add_argument(
        "--bridge",
        default=None,
        metavar="URL",
        help="bridge 模式：把对话与会话管理委托给远端 agent-bridge "
        "(如 http://127.0.0.1:18800)；不传则走 in-process 模式（默认）。"
        "环境变量 ``AGENT_BRIDGE_URL`` 同义，CLI 参数优先。",
    )
    return p.parse_args()


def make_spec_with_thinking_off(model_override: str | None = None) -> ProviderSpec:
    """构造 :class:`ProviderSpec`，并对 DeepSeek V4 系列默认关闭 thinking 模式。"""
    spec = ProviderSpec.from_env(prefix="DEEPSEEK")
    if model_override:
        spec = dataclasses.replace(spec, model=model_override)
    return _disable_deepseek_thinking(spec)


def make_memory_spec_with_thinking_off() -> ProviderSpec:
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
    return LLMClient(make_spec_with_thinking_off(model))


def _memory_llm_client() -> LLMClient:
    return LLMClient(make_memory_spec_with_thinking_off())


def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
    return MarkdownPromptBuilder(persona_id=persona_id)


# ===== CliContext =====


@dataclass
class _CliContext:
    """CLI 运行时上下文，承载"会话绑定状态机"。

    状态：

    - **未绑定**（``session is None and conv is None``）：用户尚未开始任何对话。
      此时 :attr:`pending_persona_id` / :attr:`pending_persona_name` /
      :attr:`pending_model` 持有"待应用"的默认值，首条消息（或显式 ``/new``）
      触发 ``mgr.create`` + ``start_conversation``。
    - **已绑定**：``session`` 和 ``conv`` 都非 None。
    """

    mgr: SessionManager
    catalog: PersonaCatalog
    session: Session | None
    conv: Conversation | None
    pending_persona_id: str
    pending_persona_name: str
    pending_model: str
    memory_debug: bool = False

    def is_bound(self) -> bool:
        return self.session is not None and self.conv is not None

    def ensure_bound(self) -> None:
        """未绑定时用 pending_* 触发 create + start_conversation。

        Raises:
            SessionPersistError: 落盘失败。
            PersonaNotFoundError: pending persona id 在 catalog 找不到。
            LLMAuthError: 工厂构造 LLMClient 失败。
        """
        if self.is_bound():
            return
        new_session = self.mgr.create(
            persona=self.pending_persona_name,
            model=self.pending_model,
            persona_id=self.pending_persona_id,
        )
        new_conv = self.mgr.start_conversation(new_session)
        self.session = new_session
        self.conv = new_conv
        _print_state_line(
            "新建会话",
            f"{new_session.session_id[:8]} (persona={new_session.current_persona}, "
            f"model={new_session.current_model})",
        )

    def bind(self, session: Session, conv: Conversation) -> None:
        """显式绑定到指定 session + conv（``/open`` / ``/new`` 用）。"""
        self.session = session
        self.conv = conv


# ===== 启动阶段：解析 --resume 决定初始状态 =====


def _select_initial_state(
    mgr: SessionManager,
    catalog: PersonaCatalog,
    args: argparse.Namespace,
) -> tuple[Session | None, str, str, str]:
    """根据 ``--resume`` 决定 CLI 启动时的初始 session（可能为 None）+ pending 默认值。

    Returns:
        ``(initial_session, pending_persona_id, pending_persona_name, pending_model)``
        四元组。``initial_session`` 为 None 表示启动时**未绑定**，等首条消息触发创建。

    Raises:
        SessionNotFoundError / SessionCorruptError: ``--resume <id>`` 时。
        PersonaNotFoundError: ``--persona`` 指定的名字在 catalog 找不到。
    """
    initial_model = args.model or make_spec_with_thinking_off().model

    # 解析 --persona name 为 (id, name)
    pending_info = _resolve_persona_input(args.persona, catalog)

    if args.resume is None:
        return None, pending_info.id, pending_info.name, initial_model

    if args.resume == RESUME_LATEST_SENTINEL:
        latest = mgr.latest()
        if latest is None:
            stderr.print(
                "[yellow]找不到任何已保存的会话。发首条消息或 /new 即可创建新会话。[/yellow]"
            )
            return None, pending_info.id, pending_info.name, initial_model
        # 已恢复会话；pending 仍保留作为 /new 时的默认值
        return latest, pending_info.id, pending_info.name, latest.current_model

    matched = _match_session_prefix(mgr, args.resume)
    if matched is None:
        sys.exit(1)
    session = mgr.open(matched.session_id)
    return session, pending_info.id, pending_info.name, session.current_model


def _resolve_persona_input(raw: str, catalog: PersonaCatalog) -> PersonaInfo:
    """解析 ``/persona`` 或 ``--persona`` 后的参数。

    支持的形态：

    - ``foo``: 按 "user 优先" 查 user 然后 builtin
    - ``user:foo``: 显式仅查 user
    - ``builtin:foo``: 显式仅查 builtin

    Raises:
        PersonaNotFoundError: 没找到，或前缀非法。
    """
    if ":" in raw:
        src, _, name = raw.partition(":")
        if src not in ("user", "builtin"):
            raise PersonaNotFoundError(f"无效的 source 前缀: {src!r}（合法：user / builtin）")
        return catalog.find_by_name(name, source=src)  # type: ignore[arg-type]
    return catalog.find_by_name(raw)


def _match_session_prefix(mgr: SessionManager, prefix: str) -> SessionSummary | None:
    """对 ``mgr.list()`` 做 session_id 前缀匹配。"""
    candidates = [s for s in mgr.list() if s.session_id.startswith(prefix)]
    if not candidates:
        stderr.print(f"[red]找不到匹配的会话: {prefix}[/red]")
        return None
    if len(candidates) > 1:
        stderr.print(f"[red]前缀 {prefix!r} 匹配到 {len(candidates)} 个会话，请用更长的 id：[/red]")
        for s in candidates[:5]:
            stderr.print(f"  [dim]{s.session_id[:12]} - {s.title}[/dim]")
        return None
    return candidates[0]


# ===== 显示 =====


def _brief(args: dict[str, object], max_value_len: int = 60) -> str:
    """简短化 tool 调用入参用于一行展示。

    单个 string value 超长截断为 ``"..."`` 后缀；其他类型 ``str()`` 后同样规则。
    多个字段用 ``", "`` 拼接。返回串**不带外层括号**——调用方按
    ``"name(brief)"`` 模板自己加。
    """
    if not args:
        return ""
    parts: list[str] = []
    for k, v in args.items():
        if isinstance(v, str):
            s = v if len(v) <= max_value_len else v[:max_value_len] + "..."
            parts.append(f'{k}="{s}"')
        else:
            s = str(v)
            if len(s) > max_value_len:
                s = s[:max_value_len] + "..."
            parts.append(f"{k}={s}")
    return ", ".join(parts)


def _print_tool_request(tool_name: str, args: dict[str, object]) -> None:
    """统一渲染 tool 调用的"请求"行（实时 + 重放共用）。"""
    stderr.print(
        f"[tool] {tool_name}({_brief(args)})",
        style="dim",
        markup=False,
        highlight=False,
    )


def _print_tool_result(
    *,
    is_error: bool,
    text: str,
    duration_seconds: float | None,
) -> None:
    """统一渲染 tool 调用的"结果"行（实时 + 重放共用）。

    ``duration_seconds`` 为 ``None`` 时（例如 replay 时 meta 缺字段），
    显示 ``done`` 不带耗时；``is_error=True`` 时用红字 + 截断文本。
    """
    if is_error:
        stderr.print(
            f"[tool] ✗ {text[:80]}",
            style="red",
            markup=False,
            highlight=False,
        )
        return
    if duration_seconds is not None:
        line = f"[tool] → done, {duration_seconds:.1f}s"
    else:
        line = "[tool] → done"
    stderr.print(line, style="dim", markup=False, highlight=False)


def _clear_screen() -> None:
    """软清屏：清当前可见屏幕 + 光标回顶，**不动 terminal scrollback buffer**。

    用 ``prompt_toolkit.shortcuts.clear()``——它通过 prompt_toolkit 内部的
    ``Output`` 抽象写 ANSI 控制序列，与 ``PromptSession`` 共享同一个 output
    通道。实测下，若改用 ``sys.stdout.write("\\x1b[2J\\x1b[H")`` 或 rich
    ``Console.clear()``，prompt_toolkit 接管/释放 TTY 的过程会让清屏只清掉
    "光标之后"的部分（变成 ``\\x1b[J`` 的行为），导致 banner 留底、每次
    切会话叠一层。

    Non-TTY（pipe）场景下主动跳过——避免污染 pipe 数据流。
    """
    if not sys.stdout.isatty():
        return
    pt_clear()


def _replay_history(session: Session) -> None:
    """重放 session 的事件流，让用户看到完整的"对话历史"。

    渲染规则（与正常对话循环视觉一致）：

    - ``user_message`` → ``你: <content>``
    - ``assistant_message`` → ``AI: <content>`` ；若 ``partial`` 加 ``(中断)`` 后缀
    - ``persona_change`` → ``→ persona: from → to`` 灰字
    - ``model_change`` → ``→ model: from → to`` 灰字
    - ``tool_call_request`` → ``[tool] <name>(<args>)`` 灰字 stderr
    - ``tool_call_result`` → ``[tool] → done, X.Xs`` / ``[tool] ✗ <text>`` stderr

    工具事件不计入"消息计数"——但若整个 session 只有工具事件没有任何
    user/assistant 消息（极端损坏数据），保持原有 no-op 语义不变。

    末尾打一行分隔线：``─── 以上是历史 (N 条消息) ───``。
    """
    msg_count = 0
    lines_to_print: list[tuple[str, object]] = []
    for ev in session.events:
        if ev.type == "user_message":
            lines_to_print.append(("user", ev.payload.get("content", "")))
            msg_count += 1
        elif ev.type == "assistant_message":
            lines_to_print.append(
                (
                    "assistant",
                    (
                        ev.payload.get("content", ""),
                        bool(ev.payload.get("partial")),
                    ),
                )
            )
            msg_count += 1
        elif ev.type == "persona_change":
            lines_to_print.append(
                (
                    "persona_change",
                    f"{ev.payload.get('from', '?')} → {ev.payload.get('to', '?')}",
                )
            )
        elif ev.type == "model_change":
            lines_to_print.append(
                (
                    "model_change",
                    f"{ev.payload.get('from', '?')} → {ev.payload.get('to', '?')}",
                )
            )
        elif ev.type == "tool_call_request":
            lines_to_print.append(
                (
                    "tool_request",
                    (
                        str(ev.payload.get("tool_name", "")),
                        dict(ev.payload.get("args", {}) or {}),
                    ),
                )
            )
        elif ev.type == "tool_call_result":
            duration_raw = ev.meta.get("duration_seconds")
            duration: float | None = (
                float(duration_raw) if isinstance(duration_raw, int | float) else None
            )
            lines_to_print.append(
                (
                    "tool_result",
                    (
                        bool(ev.payload.get("is_error", False)),
                        str(ev.payload.get("content", "")),
                        duration,
                    ),
                )
            )

    if msg_count == 0:
        return

    for kind, data in lines_to_print:
        if kind == "user":
            assert isinstance(data, str)
            stdout.print("[ansiblue]你:[/ansiblue] ", end="")
            stdout.print(data, markup=False, highlight=False)
        elif kind == "assistant":
            assert isinstance(data, tuple)
            text, partial = data
            stdout.print("[bold green]AI:[/bold green] ", end="")
            stdout.print(text, end="", style="green", markup=False, highlight=False)
            if partial:
                stdout.print(" [dim](中断)[/dim]")
            else:
                stdout.print()
        elif kind == "persona_change":
            assert isinstance(data, str)
            stdout.print(f"[dim]→ persona: {data}[/dim]")
        elif kind == "model_change":
            assert isinstance(data, str)
            stdout.print(f"[dim]→ model: {data}[/dim]")
        elif kind == "tool_request":
            assert isinstance(data, tuple)
            tool_name, args = data
            _print_tool_request(tool_name, args)
        elif kind == "tool_result":
            assert isinstance(data, tuple)
            is_error, text, duration = data
            _print_tool_result(is_error=is_error, text=text, duration_seconds=duration)

    stdout.print(f"[dim]─── 以上是历史 ({msg_count} 条消息) ───[/dim]\n")


def _print_banner(ctx: _CliContext) -> None:
    stdout.print("[bold]agent-friend · M2 demo[/bold]")
    if ctx.is_bound():
        s = ctx.session
        assert s is not None
        stdout.print(
            f"[dim]session: {s.session_id[:8]} ({s.initial_title}) · "
            f"persona: {s.current_persona} · model: {s.current_model}[/dim]"
        )
    else:
        stdout.print(
            "[dim]未绑定会话（发首条消息或 /new 即可创建；/open <id> 打开已有；"
            "/sessions 看所有）[/dim]"
        )
        stdout.print(
            f"[dim]默认 persona: {ctx.pending_persona_name} · 默认 model: {ctx.pending_model}[/dim]"
        )
    stdout.print(
        "[dim]命令：/sessions /open <id> /persona <name> /personas /model <name> "
        "/new /quit  ·  Ctrl+D 退出 · Ctrl+C 取消当前输入[/dim]\n"
    )


def _print_state_line(label: str, value: str) -> None:
    stdout.print(f"[dim]→ {label}: {value}[/dim]")


def _print_memory_recall(conv: Conversation) -> None:
    """调试区：展示本轮 ``retrieve`` 召回了哪些记忆（仅 memory debug 模式）。"""
    mc = conv.last_memory_context
    if mc is None or mc.is_empty():
        return
    n_pinned = sum(1 for i in mc.items if i.layer == "pinned")
    n_other = len(mc.items) - n_pinned
    stderr.print(f"[dim][记忆] 想起 {len(mc.items)} 条（常驻 {n_pinned} · 召回 {n_other}）[/dim]")
    for item in mc.items:
        stderr.print(f"  - ({item.layer}) {item.text}", style="dim", markup=False, highlight=False)


def _make_memory_debug_callback() -> Callable[[ExtractionResult], None]:
    """构造抽取落库回调：把"记住 / 更新了什么"打到调试区。"""

    def _cb(result: ExtractionResult) -> None:
        parts: list[str] = []
        if result.added_semantic:
            parts.append("记住 " + "；".join(result.added_semantic))
        if result.superseded_semantic:
            parts.append("更新（旧：" + "；".join(result.superseded_semantic) + "）")
        if result.episodic_ids:
            n = len(result.episodic_ids)
            parts.append(f"记下 {n} 条经历" if n > 1 else "记下这段经历")
        if parts:
            stderr.print(f"[dim][记忆] {' · '.join(parts)}[/dim]")

    return _cb


# ===== Slash 命令 =====


def _cmd_sessions(mgr: SessionManager) -> None:
    """``/sessions`` —— 列出所有会话。"""
    summaries = mgr.list()
    if not summaries:
        stdout.print("[dim]（暂无会话）[/dim]")
        return
    table = Table(show_header=True, header_style="bold", show_lines=False)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("title", style="white")
    table.add_column("初始 persona", style="dim")
    table.add_column("初始 model", style="dim")
    table.add_column("最近活跃 (UTC)", style="dim")
    for s in summaries:
        table.add_row(
            s.session_id[:8],
            s.title,
            s.persona,
            s.model,
            s.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
    stdout.print(table)


def _cmd_open(ctx: _CliContext, prefix: str) -> None:
    """``/open <id>`` —— 前缀匹配并切换到目标会话。"""
    if not prefix:
        stdout.print("[yellow]用法：/open <session_id 前缀>[/yellow]")
        return
    matched = _match_session_prefix(ctx.mgr, prefix)
    if matched is None:
        return
    if ctx.session is not None and matched.session_id == ctx.session.session_id:
        stdout.print("[dim]当前已经在这个会话中。[/dim]")
        return
    try:
        new_session = ctx.mgr.open(matched.session_id)
    except SessionCorruptError as e:
        stderr.print(rf"[red]\[会话文件损坏] {e}[/red]")
        return
    try:
        new_conv = ctx.mgr.start_conversation(new_session)
    except PersonaNotFoundError as e:
        stderr.print(rf"[red]\[人设错误] {e}[/red]")
        return
    except LLMAuthError as e:
        stderr.print(rf"[red]\[配置错误] {e}[/red]")
        return
    ctx.bind(new_session, new_conv)
    _clear_screen()
    _print_banner(ctx)
    _replay_history(new_session)
    _print_state_line(
        "切换会话",
        f"{new_session.session_id[:8]} (persona={new_session.current_persona}, "
        f"model={new_session.current_model}, {len(new_session.messages)} 条历史)",
    )


def _cmd_new(ctx: _CliContext) -> None:
    """``/new`` —— 用当前 persona/model（绑定后）或 pending（未绑定）显式创建新会话。"""
    if ctx.is_bound():
        assert ctx.conv is not None and ctx.session is not None
        persona_name = ctx.session.current_persona_name
        persona_id = ctx.session.current_persona_id or ctx.pending_persona_id
        model = ctx.conv.current_model
    else:
        persona_name = ctx.pending_persona_name
        persona_id = ctx.pending_persona_id
        model = ctx.pending_model
    try:
        new_session = ctx.mgr.create(
            persona=persona_name,
            model=model,
            persona_id=persona_id,
        )
    except SessionPersistError as e:
        stderr.print(rf"[red]\[新建会话失败] {e}[/red]")
        return
    try:
        new_conv = ctx.mgr.start_conversation(new_session)
    except PersonaNotFoundError as e:
        stderr.print(rf"[red]\[人设错误] {e}[/red]")
        return
    except LLMAuthError as e:
        stderr.print(rf"[red]\[配置错误] {e}[/red]")
        return
    ctx.bind(new_session, new_conv)
    _clear_screen()
    _print_banner(ctx)
    _print_state_line("新建会话", f"{new_session.session_id[:8]}")


def _cmd_persona(ctx: _CliContext, raw: str) -> None:
    """``/persona <name|user:name|builtin:name>`` —— 切换 / 更新 pending。

    支持消歧前缀：``user:foo`` / ``builtin:foo``；无前缀按 "user 优先" 解析。
    """
    if not raw:
        stdout.print(
            "[yellow]用法：/persona <name>"
            "（user/builtin 同名时用 user:name 或 builtin:name 显式消歧）[/yellow]"
        )
        return
    try:
        info = _resolve_persona_input(raw, ctx.catalog)
    except PersonaNotFoundError as e:
        stderr.print(rf"[red]\[人设错误] {e}[/red]")
        return

    if ctx.is_bound():
        assert ctx.conv is not None
        try:
            ctx.conv.switch_persona(info.id)
        except PersonaNotFoundError as e:
            stderr.print(rf"[red]\[人设错误] {e}[/red]")
            return
        except SessionPersistError as e:
            stderr.print(rf"[red]\[会话保存失败] {e}[/red]")
            return
        _print_state_line(
            "persona",
            f"{info.name} ({info.source}, id={info.id[:8]})",
        )
    else:
        ctx.pending_persona_id = info.id
        ctx.pending_persona_name = info.name
        _print_state_line(
            "默认 persona",
            f"{info.name} ({info.source}, id={info.id[:8]}) — 将在首条消息时生效",
        )


def _cmd_personas(ctx: _CliContext) -> None:
    """``/personas`` —— 列出所有 user + builtin persona。"""
    infos = ctx.catalog.list()
    if not infos:
        stdout.print("[dim]（暂无 persona）[/dim]")
        return
    table = Table(show_header=True, header_style="bold", show_lines=False)
    table.add_column("name", style="cyan", no_wrap=True)
    table.add_column("source", style="dim")
    table.add_column("id (short)", style="dim")
    table.add_column("description", style="white")
    for info in infos:
        desc = info.description if info.description else "[dim](无描述)[/dim]"
        table.add_row(info.name, info.source, info.id[:8], desc)
    stdout.print(table)


def _cmd_model(ctx: _CliContext, name: str) -> None:
    """``/model <name>`` —— 已绑定则 ``switch_model``，未绑定则更新 pending（无客户端预检）。"""
    if not name:
        stdout.print("[yellow]用法：/model <model_name>[/yellow]")
        return
    if ctx.is_bound():
        assert ctx.conv is not None
        try:
            ctx.conv.switch_model(name)
        except LLMAuthError as e:
            stderr.print(rf"[red]\[配置错误] {e}[/red]")
            return
        except (ValueError, LLMBadRequestError) as e:
            stderr.print(rf"[red]\[model 切换失败] {e}[/red]")
            return
        except SessionPersistError as e:
            stderr.print(rf"[red]\[会话保存失败] {e}[/red]")
            return
        _print_state_line("model", ctx.conv.current_model)
    else:
        ctx.pending_model = name
        _print_state_line("默认 model", f"{name}（将在首条消息时生效）")


def _handle_slash_command(cmd_line: str, ctx: _CliContext) -> None:
    parts = cmd_line.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/sessions":
        _cmd_sessions(ctx.mgr)
    elif cmd == "/open":
        _cmd_open(ctx, arg)
    elif cmd == "/new":
        _cmd_new(ctx)
    elif cmd == "/persona":
        _cmd_persona(ctx, arg)
    elif cmd == "/personas":
        _cmd_personas(ctx)
    elif cmd == "/model":
        _cmd_model(ctx, arg)
    elif cmd == "/reset":
        stdout.print("[yellow]/reset 已删除（与 /new 语义重复）。请用 /new 新建会话。[/yellow]")
    else:
        stdout.print(
            f"[yellow]未知命令: {cmd}"
            f"（可用：/sessions /open /persona /personas /model /new /quit）[/yellow]"
        )


# ===== 主循环 =====


def _interactive_loop(ctx: _CliContext) -> int:
    prompt_session: PromptSession[str] = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        auto_suggest=AutoSuggestFromHistory(),
    )

    while True:
        try:
            user_input = prompt_session.prompt(HTML("<ansiblue>你: </ansiblue>")).strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            break

        if not user_input:
            continue
        if user_input.lower() in ("q", "exit", "quit", "/quit"):
            break

        if user_input.startswith("/"):
            _handle_slash_command(user_input, ctx)
            continue

        # 懒加载：未绑定时用 pending_* 触发 create + start_conversation
        try:
            ctx.ensure_bound()
        except PersonaNotFoundError as e:
            stderr.print(rf"[red]\[人设错误] {e}[/red]")
            stderr.print("[dim]提示：/persona <name> 切换 pending persona 后再发消息[/dim]\n")
            continue
        except LLMAuthError as e:
            stderr.print(rf"[red]\[配置错误] {e}[/red]")
            return 1
        except SessionPersistError as e:
            stderr.print(rf"[red]\[新建会话失败] {e}[/red]\n")
            continue

        assert ctx.conv is not None

        stdout.print("[bold green]AI:[/bold green] ", end="")
        text_emitted_in_segment = False
        try:
            for ev in ctx.conv.stream(user_input):
                if isinstance(ev, TextDelta):
                    stdout.print(
                        ev.text,
                        end="",
                        style="green",
                        markup=False,
                        highlight=False,
                    )
                    text_emitted_in_segment = True
                elif isinstance(ev, ToolCallRequest):
                    if text_emitted_in_segment:
                        stdout.print()
                    text_emitted_in_segment = False
                    _print_tool_request(ev.tool_name, ev.args)
                elif isinstance(ev, ToolCallResult):
                    _print_tool_result(
                        is_error=ev.is_error,
                        text=ev.text,
                        duration_seconds=ev.duration_seconds,
                    )
                elif isinstance(ev, TurnDone):
                    if text_emitted_in_segment:
                        stdout.print()
                # 未知 type：前向兼容，静默忽略
        except LLMAuthError as e:
            stdout.print()
            stderr.print(rf"[red]\[配置错误] {e}[/red]")
            stderr.print("[red]API key 看起来失效了，CLI 即将退出。[/red]")
            return 1
        except LLMBadRequestError as e:
            stdout.print()
            stdout.print(f"[yellow]请求出错（多半是 bug 或上下文超长）：{e}[/yellow]\n")
        except (LLMRateLimitError, LLMNetworkError, LLMProviderError):
            stdout.print()
            stdout.print(f"[green]{random_fallback()}[/green]\n")
        except KeyboardInterrupt:
            stdout.print("\n[dim][已中断本轮回复][/dim]\n")
        except SessionPersistError as e:
            stdout.print()
            stderr.print(rf"[red]\[会话保存失败] {e}（本轮已回复但未持久化）[/red]\n")

        if ctx.memory_debug and ctx.conv is not None:
            _print_memory_recall(ctx.conv)

    stdout.print("[dim]再见！[/dim]")
    return 0


def main() -> int:
    load_dotenv()
    args = parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    bridge_url = args.bridge or os.environ.get("AGENT_BRIDGE_URL")
    if bridge_url:
        return _run_bridge_mode(args, bridge_url)

    try:
        make_spec_with_thinking_off()
    except LLMAuthError as e:
        stderr.print(rf"[red]\[配置错误] {e}[/red]")
        stderr.print("[dim]提示：复制 .env.example 为 .env 并填入 DEEPSEEK_API_KEY[/dim]")
        return 1

    try:
        store = JsonlSessionStore(SESSIONS_DIR)
    except SessionPersistError as e:
        stderr.print(rf"[red]\[初始化失败] 无法创建会话目录: {e}[/red]")
        return 1

    tool_registry = make_default_registry(session_store=store)

    memory_debug = bool(os.environ.get(MEMORY_DEBUG_ENV))
    memory_obj: Memory | None = None
    try:
        memory_obj = build_memory(
            MEMORY_DB,
            _memory_llm_client(),
            on_extracted=_make_memory_debug_callback() if memory_debug else None,
        )
    except Exception as e:
        stderr.print(rf"[yellow]\[记忆] 初始化失败，本次不启用记忆：{e}[/yellow]")
        memory_obj = None

    mgr = SessionManager(
        store=store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=default_context_manager,
        tool_registry=tool_registry,
        memory=memory_obj,
    )
    catalog = PersonaCatalog()

    try:
        (
            initial_session,
            pending_persona_id,
            pending_persona_name,
            pending_model,
        ) = _select_initial_state(mgr, catalog, args)
    except SessionNotFoundError as e:
        stderr.print(rf"[red]\[找不到会话] {e}[/red]")
        return 1
    except SessionCorruptError as e:
        stderr.print(rf"[red]\[会话文件损坏] {e}[/red]")
        return 1
    except PersonaNotFoundError as e:
        stderr.print(rf"[red]\[人设错误] {e}[/red]")
        stderr.print(
            "[dim]提示：检查 ``--persona`` 参数；可用 personas 在 /personas 命令里看到。[/dim]"
        )
        return 1

    initial_conv: Conversation | None = None
    if initial_session is not None:
        try:
            initial_conv = mgr.start_conversation(initial_session)
        except PersonaNotFoundError as e:
            stderr.print(rf"[red]\[人设错误] {e}[/red]")
            return 1
        except LLMAuthError as e:
            stderr.print(rf"[red]\[配置错误] {e}[/red]")
            return 1

    ctx = _CliContext(
        mgr=mgr,
        catalog=catalog,
        session=initial_session,
        conv=initial_conv,
        pending_persona_id=pending_persona_id,
        pending_persona_name=pending_persona_name,
        pending_model=pending_model,
        memory_debug=memory_debug,
    )
    if ctx.is_bound():
        # --resume 命中：清屏让进入新会话像"打开一个新视图"；scrollback 保留
        _clear_screen()
    _print_banner(ctx)
    if ctx.is_bound():
        assert ctx.session is not None
        _replay_history(ctx.session)
    try:
        return _interactive_loop(ctx)
    finally:
        # 退出前 drain 抽取队列，把没抽完的记忆落库（design §5.1）
        if memory_obj is not None:
            memory_obj.close()


# =====================================================================
# Bridge 模式（M6.3 · 详见 006 design.md §4.7）
# =====================================================================
#
# 与 in-process 模式平行的一套精简 CLI 路径：
#
# - 不初始化 SessionManager / LLMClient / PersonaCatalog —— 全在远端 bridge
# - 状态只剩 ``thread_id`` + 一组 pending 显示字段；首条消息触发 bridge auto-create
# - slash 命令全部走 ``BridgeClient`` 的 meta REST + AG-UI run


@dataclass
class _BridgeContext:
    """bridge 模式下的 CLI 运行时状态。

    与 in-process 的 :class:`_CliContext` 平行但更轻——bridge 模式下"会话"完全
    存在远端，CLI 只持 ``thread_id`` 和一组用于 banner 展示的字段。

    Attributes:
        client: 远程 :class:`BridgeClient`。
        thread_id: 当前活跃的远端 session_id。``--resume`` / ``/open`` 命中
            既有会话则取 bridge 上已存在的 id；``/new`` 或新启动则现场生成
            一个新 uuid（**尚未** 在 bridge 落盘，等首条消息触发 auto-create）。
        known_in_bridge: ``thread_id`` 是否已确认存在于 bridge 端。
            ``True`` 时 ``/persona`` ``/model`` 可直接调 POST meta API；
            ``False`` 时只能更新 pending 显示字段，提示用户"先发首条消息"。
        pending_persona_name: banner 显示用；bridge auto-create 实际使用的是
            bridge 进程的 ``default_persona``，CLI 这边无法干预。
        pending_model: 同上。
    """

    client: BridgeClient
    thread_id: str
    known_in_bridge: bool
    pending_persona_name: str
    pending_model: str


def _bridge_print_banner(ctx: _BridgeContext) -> None:
    stdout.print("[bold]agent-friend · bridge mode[/bold]")
    if ctx.known_in_bridge:
        stdout.print(
            f"[dim]session: {ctx.thread_id[:8]} · "
            f"persona: {ctx.pending_persona_name} · model: {ctx.pending_model}[/dim]"
        )
    else:
        stdout.print(
            f"[dim]未绑定会话（id 占位: {ctx.thread_id[:8]}，首条消息会在 bridge 上 auto-create）[/dim]"
        )
        stdout.print(
            f"[dim]默认 persona: {ctx.pending_persona_name} · "
            f"默认 model: {ctx.pending_model} （bridge 端 default 生效）[/dim]"
        )
    stdout.print(
        "[dim]命令：/sessions /open <id> /persona <name> /personas /model <name> "
        "/new /quit  ·  Ctrl+D 退出 · Ctrl+C 取消当前输入[/dim]\n"
    )


def _bridge_replay_remote_events(events: list[dict[str, Any]]) -> None:
    """bridge 模式：replay 远端 ``GET /v1/sessions/{id}`` 返回的 events dict 列表。

    渲染规则与 :func:`_replay_history` 完全对齐（用户视觉一致），但吃 dict
    不吃 :class:`agent.Session`——避免反序列化 ``datetime`` 等不必要的工作。
    """
    msg_count = 0
    rendered: list[tuple[str, object]] = []
    for ev in events:
        ev_type = ev.get("type")
        payload: dict[str, Any] = ev.get("payload") or {}
        meta: dict[str, Any] = ev.get("meta") or {}
        if ev_type == "user_message":
            rendered.append(("user", str(payload.get("content", ""))))
            msg_count += 1
        elif ev_type == "assistant_message":
            rendered.append(
                (
                    "assistant",
                    (
                        str(payload.get("content", "")),
                        bool(payload.get("partial")),
                    ),
                )
            )
            msg_count += 1
        elif ev_type == "persona_change":
            rendered.append(
                (
                    "persona_change",
                    f"{payload.get('from', '?')} → {payload.get('to', '?')}",
                )
            )
        elif ev_type == "model_change":
            rendered.append(
                (
                    "model_change",
                    f"{payload.get('from', '?')} → {payload.get('to', '?')}",
                )
            )
        elif ev_type == "tool_call_request":
            rendered.append(
                (
                    "tool_request",
                    (
                        str(payload.get("tool_name", "")),
                        dict(payload.get("args") or {}),
                    ),
                )
            )
        elif ev_type == "tool_call_result":
            duration_raw = meta.get("duration_seconds")
            duration: float | None = (
                float(duration_raw) if isinstance(duration_raw, int | float) else None
            )
            rendered.append(
                (
                    "tool_result",
                    (
                        bool(payload.get("is_error", False)),
                        str(payload.get("content", "")),
                        duration,
                    ),
                )
            )

    if msg_count == 0:
        return

    for kind, data in rendered:
        if kind == "user":
            assert isinstance(data, str)
            stdout.print("[ansiblue]你:[/ansiblue] ", end="")
            stdout.print(data, markup=False, highlight=False)
        elif kind == "assistant":
            assert isinstance(data, tuple)
            text, partial = data
            stdout.print("[bold green]AI:[/bold green] ", end="")
            stdout.print(text, end="", style="green", markup=False, highlight=False)
            if partial:
                stdout.print(" [dim](中断)[/dim]")
            else:
                stdout.print()
        elif kind == "persona_change":
            assert isinstance(data, str)
            stdout.print(f"[dim]→ persona: {data}[/dim]")
        elif kind == "model_change":
            assert isinstance(data, str)
            stdout.print(f"[dim]→ model: {data}[/dim]")
        elif kind == "tool_request":
            assert isinstance(data, tuple)
            tool_name, args = data
            _print_tool_request(tool_name, args)
        elif kind == "tool_result":
            assert isinstance(data, tuple)
            is_error, text, duration = data
            _print_tool_result(is_error=is_error, text=text, duration_seconds=duration)

    stdout.print(f"[dim]─── 以上是历史 ({msg_count} 条消息) ───[/dim]\n")


def _bridge_match_session_prefix(client: BridgeClient, prefix: str) -> BridgeSessionSummary | None:
    """bridge 模式下对 ``list_sessions()`` 做 session_id 前缀匹配。"""
    sessions = client.list_sessions()
    candidates = [s for s in sessions if s.session_id.startswith(prefix)]
    if not candidates:
        stderr.print(f"[red]找不到匹配的会话: {prefix}[/red]")
        return None
    if len(candidates) > 1:
        stderr.print(f"[red]前缀 {prefix!r} 匹配到 {len(candidates)} 个会话，请用更长的 id：[/red]")
        for s in candidates[:5]:
            stderr.print(f"  [dim]{s.session_id[:12]} - {s.title}[/dim]")
        return None
    return candidates[0]


def _bridge_load_and_apply(ctx: _BridgeContext, session_id: str) -> None:
    """bridge 模式 ``/open`` / ``--resume`` 命中后的共通逻辑：
    更新 ctx + clear screen + banner + replay。"""
    try:
        detail = ctx.client.get_session_events(session_id)
    except httpx.HTTPError as e:
        stderr.print(rf"[red]\[bridge 拉历史失败] {e}[/red]")
        return
    ctx.thread_id = session_id
    ctx.known_in_bridge = True
    ctx.pending_persona_name = str(detail.get("persona", ctx.pending_persona_name))
    ctx.pending_model = str(detail.get("model", ctx.pending_model))
    _clear_screen()
    _bridge_print_banner(ctx)
    events = detail.get("events") or []
    _bridge_replay_remote_events(events)


# ---------- bridge slash 命令 ----------


def _bridge_cmd_sessions(client: BridgeClient) -> None:
    try:
        summaries = client.list_sessions()
    except httpx.HTTPError as e:
        stderr.print(rf"[red]\[bridge 拉列表失败] {e}[/red]")
        return
    if not summaries:
        stdout.print("[dim]（暂无会话）[/dim]")
        return
    table = Table(show_header=True, header_style="bold", show_lines=False)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("title", style="white")
    table.add_column("初始 persona", style="dim")
    table.add_column("初始 model", style="dim")
    table.add_column("最近活跃", style="dim")
    for s in summaries:
        table.add_row(s.session_id[:8], s.title, s.persona, s.model, s.updated_at)
    stdout.print(table)


def _bridge_cmd_open(ctx: _BridgeContext, prefix: str) -> None:
    if not prefix:
        stdout.print("[yellow]用法：/open <session_id 前缀>[/yellow]")
        return
    try:
        matched = _bridge_match_session_prefix(ctx.client, prefix)
    except httpx.HTTPError as e:
        stderr.print(rf"[red]\[bridge 拉列表失败] {e}[/red]")
        return
    if matched is None:
        return
    if matched.session_id == ctx.thread_id and ctx.known_in_bridge:
        stdout.print("[dim]当前已经在这个会话中。[/dim]")
        return
    _bridge_load_and_apply(ctx, matched.session_id)


def _bridge_cmd_new(ctx: _BridgeContext) -> None:
    """``/new``：生成新 uuid 作 thread_id；待首条消息触发 bridge auto-create。"""
    ctx.thread_id = uuid4().hex
    ctx.known_in_bridge = False
    _clear_screen()
    _bridge_print_banner(ctx)
    _print_state_line("新建会话", f"{ctx.thread_id[:8]} （待首条消息时 bridge 落盘）")


def _bridge_cmd_persona(ctx: _BridgeContext, raw: str) -> None:
    if not raw:
        stdout.print("[yellow]用法：/persona <name>[/yellow]")
        return
    if not ctx.known_in_bridge:
        stderr.print(
            "[yellow]bridge 模式下 /persona 需要会话已存在 bridge 上。"
            "先发首条消息建会话后再切。[/yellow]"
        )
        return
    try:
        result = ctx.client.switch_persona(ctx.thread_id, raw)
    except httpx.HTTPStatusError as e:
        stderr.print(rf"[red]\[persona 切换失败] {e.response.status_code}: {e.response.text}[/red]")
        return
    except httpx.HTTPError as e:
        stderr.print(rf"[red]\[bridge 通信失败] {e}[/red]")
        return
    new_persona = str(result.get("persona", raw))
    ctx.pending_persona_name = new_persona
    _print_state_line("persona", new_persona)


def _bridge_cmd_personas(client: BridgeClient) -> None:
    try:
        infos = client.list_personas()
    except httpx.HTTPError as e:
        stderr.print(rf"[red]\[bridge 拉 persona 列表失败] {e}[/red]")
        return
    if not infos:
        stdout.print("[dim]（暂无 persona）[/dim]")
        return
    table = Table(show_header=True, header_style="bold", show_lines=False)
    table.add_column("name", style="cyan", no_wrap=True)
    table.add_column("source", style="dim")
    table.add_column("id (short)", style="dim")
    table.add_column("description", style="white")
    for info in infos:
        desc = info.description if info.description else "[dim](无描述)[/dim]"
        table.add_row(info.name, info.source, info.id[:8], desc)
    stdout.print(table)


def _bridge_cmd_model(ctx: _BridgeContext, name: str) -> None:
    if not name:
        stdout.print("[yellow]用法：/model <model_name>[/yellow]")
        return
    if not ctx.known_in_bridge:
        stderr.print(
            "[yellow]bridge 模式下 /model 需要会话已存在 bridge 上。"
            "先发首条消息建会话后再切。[/yellow]"
        )
        return
    try:
        result = ctx.client.switch_model(ctx.thread_id, name)
    except httpx.HTTPStatusError as e:
        stderr.print(rf"[red]\[model 切换失败] {e.response.status_code}: {e.response.text}[/red]")
        return
    except httpx.HTTPError as e:
        stderr.print(rf"[red]\[bridge 通信失败] {e}[/red]")
        return
    new_model = str(result.get("model", name))
    ctx.pending_model = new_model
    _print_state_line("model", new_model)


def _bridge_handle_slash(cmd_line: str, ctx: _BridgeContext) -> None:
    parts = cmd_line.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd == "/sessions":
        _bridge_cmd_sessions(ctx.client)
    elif cmd == "/open":
        _bridge_cmd_open(ctx, arg)
    elif cmd == "/new":
        _bridge_cmd_new(ctx)
    elif cmd == "/persona":
        _bridge_cmd_persona(ctx, arg)
    elif cmd == "/personas":
        _bridge_cmd_personas(ctx.client)
    elif cmd == "/model":
        _bridge_cmd_model(ctx, arg)
    elif cmd == "/reset":
        stdout.print("[yellow]/reset 已删除（与 /new 语义重复）。请用 /new 新建会话。[/yellow]")
    else:
        stdout.print(
            f"[yellow]未知命令: {cmd}"
            f"（可用：/sessions /open /persona /personas /model /new /quit）[/yellow]"
        )


def _bridge_interactive_loop(ctx: _BridgeContext) -> int:
    prompt_session: PromptSession[str] = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        auto_suggest=AutoSuggestFromHistory(),
    )

    while True:
        try:
            user_input = prompt_session.prompt(HTML("<ansiblue>你: </ansiblue>")).strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            break

        if not user_input:
            continue
        if user_input.lower() in ("q", "exit", "quit", "/quit"):
            break

        if user_input.startswith("/"):
            _bridge_handle_slash(user_input, ctx)
            continue

        stdout.print("[bold green]AI:[/bold green] ", end="")
        text_emitted_in_segment = False
        try:
            for ev in ctx.client.run(thread_id=ctx.thread_id, user_input=user_input):
                if isinstance(ev, TextDelta):
                    stdout.print(
                        ev.text,
                        end="",
                        style="green",
                        markup=False,
                        highlight=False,
                    )
                    text_emitted_in_segment = True
                elif isinstance(ev, ToolCallRequest):
                    if text_emitted_in_segment:
                        stdout.print()
                    text_emitted_in_segment = False
                    _print_tool_request(ev.tool_name, ev.args)
                elif isinstance(ev, ToolCallResult):
                    _print_tool_result(
                        is_error=ev.is_error,
                        text=ev.text,
                        duration_seconds=None,
                    )
                elif isinstance(ev, TurnDone):
                    if text_emitted_in_segment:
                        stdout.print()
            # 第一条成功的消息也意味着 bridge 端 session 一定已经存在（auto-create
            # 或本就存在）。标 known_in_bridge 让 /persona /model 启用
            ctx.known_in_bridge = True
        except BridgeRunError as e:
            stdout.print()
            stdout.print(f"[yellow]{e}[/yellow] [dim](code={e.code})[/dim]\n")
        except httpx.HTTPStatusError as e:
            stdout.print()
            stderr.print(
                rf"[red]\[bridge HTTP 错误] {e.response.status_code}: {e.response.text[:200]}[/red]\n"
            )
        except httpx.HTTPError as e:
            stdout.print()
            stderr.print(rf"[red]\[bridge 通信失败] {e}[/red]\n")
        except KeyboardInterrupt:
            stdout.print("\n[dim][已中断本轮回复][/dim]\n")

    stdout.print("[dim]再见！[/dim]")
    return 0


def _run_bridge_mode(args: argparse.Namespace, base_url: str) -> int:
    """bridge 模式主入口（与 in-process :func:`main` 平行）。"""
    with BridgeClient(base_url) as client:
        # 探活：拉一次 /sessions 验证 bridge 可达 + URL 正确
        try:
            initial_sessions = client.list_sessions()
        except httpx.HTTPError as e:
            stderr.print(rf"[red]\[bridge 不可达] {base_url}: {e}[/red]")
            stderr.print("[dim]提示：先用 ./scripts/bridge/run.sh 启动 agent-bridge[/dim]")
            return 1

        # 解析 --resume
        if args.resume is None:
            ctx = _BridgeContext(
                client=client,
                thread_id=uuid4().hex,
                known_in_bridge=False,
                pending_persona_name=args.persona,
                pending_model=args.model or "(bridge default)",
            )
            _bridge_print_banner(ctx)
            return _bridge_interactive_loop(ctx)

        if args.resume == RESUME_LATEST_SENTINEL:
            if not initial_sessions:
                stderr.print(
                    "[yellow]bridge 上找不到任何已保存的会话。发首条消息或 /new 即可创建。[/yellow]"
                )
                ctx = _BridgeContext(
                    client=client,
                    thread_id=uuid4().hex,
                    known_in_bridge=False,
                    pending_persona_name=args.persona,
                    pending_model=args.model or "(bridge default)",
                )
                _bridge_print_banner(ctx)
                return _bridge_interactive_loop(ctx)
            latest = initial_sessions[0]
            ctx = _BridgeContext(
                client=client,
                thread_id=latest.session_id,
                known_in_bridge=True,
                pending_persona_name=latest.persona,
                pending_model=latest.model,
            )
            _bridge_load_and_apply(ctx, latest.session_id)
            return _bridge_interactive_loop(ctx)

        # --resume <prefix>
        candidates = [s for s in initial_sessions if s.session_id.startswith(args.resume)]
        if not candidates:
            stderr.print(f"[red]找不到匹配的会话: {args.resume}[/red]")
            return 1
        if len(candidates) > 1:
            stderr.print(
                f"[red]前缀 {args.resume!r} 匹配到 {len(candidates)} 个会话，请用更长的 id：[/red]"
            )
            for s in candidates[:5]:
                stderr.print(f"  [dim]{s.session_id[:12]} - {s.title}[/dim]")
            return 1
        matched = candidates[0]
        ctx = _BridgeContext(
            client=client,
            thread_id=matched.session_id,
            known_in_bridge=True,
            pending_persona_name=matched.persona,
            pending_model=matched.model,
        )
        _bridge_load_and_apply(ctx, matched.session_id)
        return _bridge_interactive_loop(ctx)


if __name__ == "__main__":
    sys.exit(main())
