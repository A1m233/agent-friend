"""agent-friend CLI 入口（M2.3 · 引擎层会话管理，懒加载会话）。

用法::

    ./scripts/cli/run.sh                          # 启动后**未绑定会话**，首条消息触发创建
    ./scripts/cli/run.sh --persona cute_friend   # 指定初始人设（pending，首消息生效）
    ./scripts/cli/run.sh --model deepseek/deepseek-v4-pro  # 指定初始 model
    ./scripts/cli/run.sh --resume                # 用户显式想恢复：立刻 open 最近会话
    ./scripts/cli/run.sh --resume <session_id>   # 显式 id（支持前缀）：立刻 open

需要在项目根 ``.env`` 中配置 ``DEEPSEEK_API_KEY``。

M2 范围（详见 002 design.md §4.8 + progress.md 实施日志）：

- 会话作为引擎层一等公民：CLI 全程通过 :class:`SessionManager` 操作会话生命周期
- **懒加载策略**：不像 M0.3 那样"启动即建"会话。用户没发消息 + 没 ``/new`` /
  ``/open`` 就 ``/quit``，**不会留下空 session 文件**。``/persona`` ``/model``
  在未绑定时只更新 pending 默认值，首条消息时一次性应用
- 持久化：每条消息 / 切换都**实时**落盘到 ``data/sessions/{session_id}.jsonl``
- 新 slash 命令：``/sessions`` ``/open`` ``/persona`` ``/model`` ``/new``
- ``/reset`` **已删除**（语义与 ``/new`` 重复，统一用 ``/new``）
- 持久化错误（``SessionPersistError``）红字提示但不中断主循环
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from dataclasses import dataclass
from pathlib import Path

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
    NaiveContextManager,
    PersonaNotFoundError,
    Session,
    SessionCorruptError,
    SessionManager,
    SessionNotFoundError,
    SessionPersistError,
    SessionSummary,
    random_fallback,
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

DATA_DIR = Path("data")
SESSIONS_DIR = DATA_DIR / "sessions"
HISTORY_FILE = DATA_DIR / ".cli_history"

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
        help="初始 persona（pending；未绑定时为默认值），对应 data/personas/{name}.md "
        '或内置 personas（默认 "default"）',
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
    return p.parse_args()


def make_spec_with_thinking_off(model_override: str | None = None) -> ProviderSpec:
    """构造 :class:`ProviderSpec`，并对 DeepSeek V4 系列默认关闭 thinking 模式。"""
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
    return LLMClient(make_spec_with_thinking_off(model))


def _prompt_factory(persona: str) -> MarkdownPromptBuilder:
    return MarkdownPromptBuilder(persona_name=persona)


# ===== CliContext =====


@dataclass
class _CliContext:
    """CLI 运行时上下文，承载"会话绑定状态机"。

    状态：

    - **未绑定**（``session is None and conv is None``）：用户尚未开始任何对话。
      此时 :attr:`pending_persona` / :attr:`pending_model` 持有"待应用"的默认值，
      首条消息（或显式 ``/new``）触发 ``mgr.create`` + ``start_conversation``。
    - **已绑定**：``session`` 和 ``conv`` 都非 None。
    """

    mgr: SessionManager
    session: Session | None
    conv: Conversation | None
    pending_persona: str
    pending_model: str

    def is_bound(self) -> bool:
        return self.session is not None and self.conv is not None

    def ensure_bound(self) -> None:
        """未绑定时用 pending_* 触发 create + start_conversation。

        Raises:
            SessionPersistError: 落盘失败。
            PersonaNotFoundError: ``pending_persona`` 文件不存在。
            LLMAuthError: 工厂构造 LLMClient 失败。
        """
        if self.is_bound():
            return
        new_session = self.mgr.create(
            persona=self.pending_persona,
            model=self.pending_model,
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
    args: argparse.Namespace,
) -> tuple[Session | None, str, str]:
    """根据 ``--resume`` 决定 CLI 启动时的初始 session（可能为 None）+ pending 默认值。

    Returns:
        ``(initial_session, pending_persona, pending_model)`` 三元组。
        ``initial_session`` 为 None 表示启动时**未绑定**，等首条消息触发创建。

    Raises:
        SessionNotFoundError / SessionCorruptError: ``--resume <id>`` 时。
    """
    initial_model = args.model or make_spec_with_thinking_off().model

    if args.resume is None:
        return None, args.persona, initial_model

    if args.resume == RESUME_LATEST_SENTINEL:
        latest = mgr.latest()
        if latest is None:
            stderr.print(
                "[yellow]找不到任何已保存的会话。发首条消息或 /new 即可创建新会话。[/yellow]"
            )
            return None, args.persona, initial_model
        return latest, latest.current_persona, latest.current_model

    matched = _match_session_prefix(mgr, args.resume)
    if matched is None:
        sys.exit(1)
    session = mgr.open(matched.session_id)
    return session, session.current_persona, session.current_model


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

    末尾打一行分隔线：``─── 以上是历史 (N 条消息) ───``。若 session 没有任何
    user/assistant 事件（如刚创建的空 session），整个函数直接 no-op。
    """
    msg_count = 0
    lines_to_print: list[tuple[str, str, bool]] = []
    for ev in session.events:
        if ev.type == "user_message":
            lines_to_print.append(("user", ev.payload.get("content", ""), False))
            msg_count += 1
        elif ev.type == "assistant_message":
            lines_to_print.append(
                (
                    "assistant",
                    ev.payload.get("content", ""),
                    bool(ev.payload.get("partial")),
                )
            )
            msg_count += 1
        elif ev.type == "persona_change":
            lines_to_print.append(
                (
                    "persona_change",
                    f"{ev.payload.get('from', '?')} → {ev.payload.get('to', '?')}",
                    False,
                )
            )
        elif ev.type == "model_change":
            lines_to_print.append(
                (
                    "model_change",
                    f"{ev.payload.get('from', '?')} → {ev.payload.get('to', '?')}",
                    False,
                )
            )

    if msg_count == 0:
        return

    for kind, text, partial in lines_to_print:
        if kind == "user":
            stdout.print("[ansiblue]你:[/ansiblue] ", end="")
            stdout.print(text, markup=False, highlight=False)
        elif kind == "assistant":
            stdout.print("[bold green]AI:[/bold green] ", end="")
            stdout.print(text, end="", style="green", markup=False, highlight=False)
            if partial:
                stdout.print(" [dim](中断)[/dim]")
            else:
                stdout.print()
        elif kind == "persona_change":
            stdout.print(f"[dim]→ persona: {text}[/dim]")
        elif kind == "model_change":
            stdout.print(f"[dim]→ model: {text}[/dim]")

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
            f"[dim]默认 persona: {ctx.pending_persona} · 默认 model: {ctx.pending_model}[/dim]"
        )
    stdout.print(
        "[dim]命令：/sessions /open <id> /persona <name> /model <name> "
        "/new /quit  ·  Ctrl+D 退出 · Ctrl+C 取消当前输入[/dim]\n"
    )


def _print_state_line(label: str, value: str) -> None:
    stdout.print(f"[dim]→ {label}: {value}[/dim]")


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
        assert ctx.conv is not None
        persona = ctx.conv.current_persona
        model = ctx.conv.current_model
    else:
        persona = ctx.pending_persona
        model = ctx.pending_model
    try:
        new_session = ctx.mgr.create(persona=persona, model=model)
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


def _cmd_persona(ctx: _CliContext, name: str) -> None:
    """``/persona <name>`` —— 已绑定则 ``switch_persona``，未绑定则客户端预检后更新 pending。"""
    if not name:
        stdout.print("[yellow]用法：/persona <name>[/yellow]")
        return
    if ctx.is_bound():
        assert ctx.conv is not None
        try:
            ctx.conv.switch_persona(name)
        except PersonaNotFoundError as e:
            stderr.print(rf"[red]\[人设错误] {e}[/red]")
            return
        except SessionPersistError as e:
            stderr.print(rf"[red]\[会话保存失败] {e}[/red]")
            return
        _print_state_line("persona", ctx.conv.current_persona)
    else:
        try:
            _prompt_factory(name).build()
        except PersonaNotFoundError as e:
            stderr.print(rf"[red]\[人设错误] {e}[/red]")
            return
        ctx.pending_persona = name
        _print_state_line("默认 persona", f"{name}（将在首条消息时生效）")


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
    elif cmd == "/model":
        _cmd_model(ctx, arg)
    elif cmd == "/reset":
        stdout.print("[yellow]/reset 已删除（与 /new 语义重复）。请用 /new 新建会话。[/yellow]")
    else:
        stdout.print(
            f"[yellow]未知命令: {cmd}（可用：/sessions /open /persona /model /new /quit）[/yellow]"
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
        try:
            for chunk in ctx.conv.stream(user_input):
                stdout.print(
                    chunk,
                    end="",
                    style="green",
                    markup=False,
                    highlight=False,
                )
            stdout.print()
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

    stdout.print("[dim]再见！[/dim]")
    return 0


def main() -> int:
    load_dotenv()
    args = parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

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

    mgr = SessionManager(
        store=store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager=NaiveContextManager(),
    )

    try:
        initial_session, pending_persona, pending_model = _select_initial_state(mgr, args)
    except SessionNotFoundError as e:
        stderr.print(rf"[red]\[找不到会话] {e}[/red]")
        return 1
    except SessionCorruptError as e:
        stderr.print(rf"[red]\[会话文件损坏] {e}[/red]")
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
        session=initial_session,
        conv=initial_conv,
        pending_persona=pending_persona,
        pending_model=pending_model,
    )
    if ctx.is_bound():
        # --resume 命中：清屏让进入新会话像"打开一个新视图"；scrollback 保留
        _clear_screen()
    _print_banner(ctx)
    if ctx.is_bound():
        assert ctx.session is not None
        _replay_history(ctx.session)
    return _interactive_loop(ctx)


if __name__ == "__main__":
    sys.exit(main())
