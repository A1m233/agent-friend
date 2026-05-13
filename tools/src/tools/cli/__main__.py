"""agent-friend CLI 入口（M0.3 完整版）。

用法::

    ./scripts/cli/run.sh                            # 默认人设，新会话
    ./scripts/cli/run.sh --persona cute_friend     # 指定人设
    ./scripts/cli/run.sh --resume                  # 恢复最新会话
    ./scripts/cli/run.sh --resume <path>           # 恢复指定会话

需要在项目根 ``.env`` 中配置 ``DEEPSEEK_API_KEY``。

M0.3 范围（详见 design.md §2.3 / §4.7）：

- ``prompt_toolkit`` + ``rich``：行编辑（方向键、Backspace）、↑↓ 翻历史、
  Ctrl+C 取消当前输入、Ctrl+D 退出、彩色区分用户/AI/系统/错误
- ``/quit`` 或 Ctrl+D 自动保存会话到 ``data/sessions/session-{ts}.json``
- ``--resume`` 恢复历史会话（不带参数 = 找最新；带路径 = 显式）
- ``--persona`` 切换人设（``data/personas/{name}.md`` 优先于内置）
- 5 类 ``LLMError`` 分级处理：auth 退出、bad_request 直说、其他用兜底话术
- 兜底话术池避免暴露"网络/超时/限速"等技术术语
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from rich.console import Console

from agent import (
    Conversation,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaNotFoundError,
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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="agent-friend",
        description="agent-friend CLI 调试 UI（M0.3 多轮 + 流式 + 持久化 + 人设切换）",
    )
    p.add_argument(
        "--persona",
        default="default",
        help='人设名称，对应 data/personas/{name}.md 或内置 personas（默认 "default"）',
    )
    p.add_argument(
        "--resume",
        nargs="?",
        const=RESUME_LATEST_SENTINEL,
        default=None,
        metavar="PATH",
        help="恢复历史会话；不带参数则取 data/sessions/ 下最新文件",
    )
    return p.parse_args()


def find_latest_session() -> Path | None:
    if not SESSIONS_DIR.exists():
        return None
    files = sorted(
        SESSIONS_DIR.glob("session-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def make_spec_with_thinking_off() -> ProviderSpec:
    """构造 spec，并对 DeepSeek V4 系列默认关闭 thinking 模式。

    详见 design.md §4.1.1：thinking 偏好属于调用方（CLI）的 use case 偏好，
    不放在 ``PROVIDER_DEFAULTS`` 里。
    """
    spec = ProviderSpec.from_env(prefix="DEEPSEEK")
    if "deepseek" in spec.model.lower():
        spec = dataclasses.replace(
            spec,
            defaults={
                **spec.defaults,
                "extra_body": {"thinking": {"type": "disabled"}},
            },
        )
    return spec


def build_conversation(
    args: argparse.Namespace,
    llm_client: LLMClient,
) -> Conversation:
    """根据 ``--persona`` / ``--resume`` 构造或恢复 :class:`Conversation`。"""
    builder = MarkdownPromptBuilder(persona_name=args.persona)
    cm = NaiveContextManager()

    if args.resume is None:
        return Conversation(
            llm_client=llm_client,
            context_manager=cm,
            prompt_builder=builder,
        )

    if args.resume == RESUME_LATEST_SENTINEL:
        path = find_latest_session()
        if path is None:
            stderr.print("[yellow]找不到任何已保存的会话，将开启新对话。[/yellow]")
            return Conversation(
                llm_client=llm_client,
                context_manager=cm,
                prompt_builder=builder,
            )
    else:
        path = Path(args.resume)
        if not path.is_file():
            stderr.print(f"[red]找不到会话文件：{path}[/red]")
            sys.exit(1)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        conv = Conversation.load(
            data,
            llm_client=llm_client,
            context_manager=cm,
            prompt_builder=builder,
        )
    except (OSError, json.JSONDecodeError, ValueError) as e:
        stderr.print(f"[red]恢复会话失败：{type(e).__name__}: {e}[/red]")
        sys.exit(1)

    stdout.print(f"[dim]已恢复会话：{path}（{len(conv.history)} 条历史消息）[/dim]")
    return conv


def save_session(conv: Conversation) -> Path | None:
    """把对话历史写到 ``data/sessions/session-{ts}.json``，返回路径或 ``None``。"""
    if not conv.history:
        return None
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = SESSIONS_DIR / f"session-{ts}.json"
    try:
        path.write_text(
            json.dumps(conv.dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
    except OSError as e:
        stderr.print(f"[red]保存会话失败：{e}[/red]")
        return None


def print_banner(spec: ProviderSpec, persona: str) -> None:
    stdout.print("[bold]agent-friend · M0.3 demo[/bold]")
    stdout.print(f"[dim]模型：{spec.model} | 人设：{persona}[/dim]")
    stdout.print(
        "[dim]命令：/quit 或 q 退出（自动保存）；/reset 清空历史；"
        "Ctrl+D 退出；Ctrl+C 取消当前输入[/dim]\n"
    )


def main() -> int:
    load_dotenv()
    args = parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        spec = make_spec_with_thinking_off()
    except LLMAuthError as e:
        stderr.print(rf"[red]\[配置错误] {e}[/red]")
        stderr.print("[dim]提示：复制 .env.example 为 .env 并填入 DEEPSEEK_API_KEY[/dim]")
        return 1

    try:
        conv = build_conversation(args, LLMClient(spec))
    except PersonaNotFoundError as e:
        stderr.print(rf"[red]\[人设错误] {e}[/red]")
        return 1

    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        auto_suggest=AutoSuggestFromHistory(),
    )

    print_banner(spec, args.persona)

    while True:
        try:
            user_input = session.prompt(HTML("<ansiblue>你: </ansiblue>")).strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            break

        if not user_input:
            continue
        if user_input.lower() in ("q", "exit", "quit", "/quit"):
            break
        if user_input.lower() == "/reset":
            conv.reset()
            stdout.print("[dim][已清空历史][/dim]\n")
            continue

        stdout.print("[bold green]AI:[/bold green] ", end="")
        try:
            for chunk in conv.stream(user_input):
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

    saved = save_session(conv)
    if saved is not None:
        stdout.print(f"\n[dim]会话已保存到 {saved}[/dim]")
    stdout.print("[dim]再见！[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
