"""agent-friend CLI 入口（M0.2 多轮 + 流式版）。

用法::

    uv run python -m tools.cli
    # 或
    ./scripts/cli/run.sh

需要在项目根目录的 ``.env`` 文件中配置 ``DEEPSEEK_API_KEY``。

M0.2 范围（详见 design.md §2.2）：
- 多轮对话：维护 ``Conversation`` history，模型能记住前文
- 流式输出：边收边打印（打字机效果）
- 默认人设：从 ``agent/personas/default.md`` 加载
- 错误处理：仍然只捕获 ``LLMError`` 基类（不差异化，M0.3 才精细化）
- 不持久化：``/quit`` 直接丢弃历史（dump/load 接口已就位但 CLI 未接入）
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

from agent import (
    Conversation,
    MarkdownPromptBuilder,
    NaiveContextManager,
)
from llm_providers import LLMAuthError, LLMClient, LLMError, ProviderSpec


def main() -> int:
    load_dotenv()

    try:
        spec = ProviderSpec.from_env(prefix="DEEPSEEK")
    except LLMAuthError as e:
        print(f"[配置错误] {e}", file=sys.stderr)
        print(
            "提示：请复制 .env.example 为 .env 并填入 DEEPSEEK_API_KEY。",
            file=sys.stderr,
        )
        return 1

    conv = Conversation(
        llm_client=LLMClient(spec),
        context_manager=NaiveContextManager(),
        prompt_builder=MarkdownPromptBuilder(persona_name="default"),
    )

    print("agent-friend · M0.2 多轮对话 + 流式 demo")
    print(f"模型：{spec.model}")
    print("命令：q / exit / quit 退出；/reset 清空历史；Ctrl+D / Ctrl+C 也可退出。\n")

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            return 0

        if not user_input:
            continue
        if user_input.lower() in ("q", "exit", "quit"):
            print("再见！")
            return 0
        if user_input.lower() == "/reset":
            conv.reset()
            print("[已清空历史]\n")
            continue

        print("AI: ", end="", flush=True)
        try:
            for chunk in conv.stream(user_input):
                print(chunk, end="", flush=True)
        except LLMError as e:
            print(f"\n[出错了] {type(e).__name__}: {e}\n", file=sys.stderr)
            continue
        except KeyboardInterrupt:
            print("\n[已中断本轮回复，历史已记录部分内容]\n", file=sys.stderr)
            continue

        print("\n")


if __name__ == "__main__":
    sys.exit(main())
