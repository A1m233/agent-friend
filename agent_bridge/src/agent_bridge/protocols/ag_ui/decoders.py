"""把 AG-UI ``RunAgentInput`` 解码为 bridge 内部输入。

AG-UI 的 :class:`ag_ui.core.RunAgentInput` 已经是 pydantic 模型——FastAPI 直接
作为请求体类型解析。这里只承担**最后一步**：从已解析的 ``messages`` 数组里取
出本轮 user 新输入。

设计取舍（详见 design §4.4.2）：

- AG-UI 假设服务端已通过 ``thread_id`` 落盘了之前所有历史，``messages`` 数组
  里的 ``user`` / ``assistant`` 历史**与服务端 jsonl 是同一份事件流的不同视图**
- 本期不去对照"客户端送的 history 和 jsonl 落盘 history 是否一致"——后者是
  source of truth；客户端 history 仅用来识别最后一条 user 的 ``content``
- 不允许 ``messages`` 为空或末位不是 user / 不允许 ``thread_id`` / ``run_id`` 缺失
"""

from __future__ import annotations

from dataclasses import dataclass

from ag_ui.core import RunAgentInput, UserMessage


class DecodeError(ValueError):
    """AG-UI ``RunAgentInput`` 不符合 bridge 接受的最小约束。"""


@dataclass(frozen=True)
class DecodedRequest:
    """AG-UI request 解码结果。

    Attributes:
        thread_id: AG-UI 协议 ``thread_id``；bridge 直接当 ``session_id``。
        run_id: AG-UI 协议 ``run_id``；本轮 ``RUN_STARTED`` / ``RUN_FINISHED``
            事件回填。
        new_user_input: 最后一条 ``UserMessage.content`` 的文本。
    """

    thread_id: str
    run_id: str
    new_user_input: str


def decode_run_agent_input(payload: RunAgentInput) -> DecodedRequest:
    """从 :class:`RunAgentInput` 抽出 bridge 真正需要的三个字段。

    Raises:
        DecodeError: ``thread_id`` / ``run_id`` 缺失或为空；``messages``
            为空或末位不是 :class:`UserMessage`。
    """
    if not payload.thread_id:
        raise DecodeError("thread_id 不能为空")
    if not payload.run_id:
        raise DecodeError("run_id 不能为空")
    if not payload.messages:
        raise DecodeError("messages 不能为空")

    last = payload.messages[-1]
    if not isinstance(last, UserMessage):
        raise DecodeError(f"messages 最后一条必须是 role=user，收到 {type(last).__name__}")

    content = last.content if isinstance(last.content, str) else ""
    return DecodedRequest(
        thread_id=payload.thread_id,
        run_id=payload.run_id,
        new_user_input=content,
    )
