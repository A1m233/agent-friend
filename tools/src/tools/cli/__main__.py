"""agent-friend CLI 入口（M0.1 单轮通话版）。

用法::

    uv run python -m tools.cli

需要在项目根目录的 ``.env`` 文件中配置 ``DEEPSEEK_API_KEY``。

M0.1 严格边界（详见 design.md §2.1）：
- 仅单轮：每条输入独立调用 LLM，**不维护对话历史**
- 无流式：等完整回复后一次性打印
- 无人设：不附加任何 system prompt
- 错误处理：仅捕获 ``LLMError`` 基类，不做差异化（M0.3 才精细化）
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

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

    client = LLMClient(spec)

    print("agent-friend · M0.1 单轮通话 demo")
    print(f"模型：{spec.model}")
    print("输入 q / exit / quit 退出，Ctrl+D / Ctrl+C 也行。\n")

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

        try:
            reply = client.complete(messages=[{"role": "user", "content": user_input}])
        except LLMError as e:
            print(f"[出错了] {type(e).__name__}: {e}\n", file=sys.stderr)
            continue

        print(f"AI: {reply}\n")


if __name__ == "__main__":
    sys.exit(main())
