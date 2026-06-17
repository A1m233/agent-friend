"""``NaiveContextManager``：全发不截断的最朴素策略。

001 起的默认实现，009 迁入 ``context/`` 包并适配新签名（``new_user_input`` 可选、
新增 ``trailing_system`` / ``runtime``）。Naive **忽略 ``runtime``**——不做任何
token 预检 / 裁剪 / 压缩，行为与 001 完全一致。

若 history 真的爆了 LLM 的 context window，调用层会收到
:class:`llm_providers.LLMBadRequestError`，由 CLI 给出友好提示。
"""

from __future__ import annotations

from ..messages import Message
from .protocol import BuildResult, RuntimeContext, assemble_messages


class NaiveContextManager:
    """全发不截断（009 适配新签名，语义不变）。"""

    def build_messages(
        self,
        history: list[Message],
        system_prompt: str,
        new_user_input: str | None = None,
        extra_context: list[Message] | None = None,
        trailing_user: str | None = None,
        trailing_system: str | None = None,
        runtime: RuntimeContext | None = None,
    ) -> BuildResult:
        messages = assemble_messages(
            history=history,
            system_prompt=system_prompt,
            new_user_input=new_user_input,
            extra_context=extra_context,
            trailing_user=trailing_user,
            trailing_system=trailing_system,
        )
        return BuildResult(messages=messages)
