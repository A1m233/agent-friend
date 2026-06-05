"""会话事件 → 记忆素材的投影层（过滤策略**单点维护**处）。

记忆模块只认识"干净的对话发言"（:class:`memory.ConversationFragment` /
:class:`memory.Utterance`），**不认识** :class:`agent.sessions.Event`。本模块是
两者之间唯一的转换点：决定"哪些事件算记忆素材、怎么洗"。新增事件类型 / 调整
过滤口径时**只改这一个函数**，记忆模块不动。

依赖方向：``agent`` 拥有 ``Event`` taxonomy，依赖 ``memory`` 的契约类型产出
``Utterance``；``memory`` 不反向依赖 ``agent``。

详见 docs/requirements/008-engine-memory/design.md §7。
"""

from __future__ import annotations

from memory import ConversationFragment, Utterance

from .sessions import Event

__all__ = ["project_turn"]


def project_turn(
    events: list[Event],
    *,
    session_id: str,
    persona_id: str,
    owner_user_id: str = "local",
) -> ConversationFragment:
    """把一段会话事件投影成可抽取的 :class:`ConversationFragment`。

    Args:
        events: 待投影的事件（通常是刚结束这一轮 append 进 session 的那几条）。
        session_id: 来源会话 id（用于拼 ``source_ref``）。
        persona_id: 本段归属 persona。
        owner_user_id: 多 user 预留维度，v1 固定。

    Returns:
        只含 user / assistant 文本发言的 fragment。

    过滤策略（v1）:
        - **保留** ``user_message``、``assistant_message``（``partial=False``）的文本。
        - **丢弃** ``session_meta`` / ``persona_change`` / ``model_change`` /
          ``tool_call_request`` / ``tool_call_result``。
        - ``tool_call_result`` 是最明显的**未来例外**：工具查出的事实可能值得记，
          届时在此处加"洗出有用部分"的逻辑即可。
    """
    utterances: list[Utterance] = []
    for ev in events:
        if ev.type == "user_message":
            content = ev.payload.get("content", "")
            if content:
                utterances.append(
                    Utterance(
                        speaker="user",
                        text=content,
                        ts=ev.ts,
                        source_ref=f"{session_id}#{ev.uuid}",
                    )
                )
        elif ev.type == "assistant_message":
            if ev.payload.get("partial"):
                continue  # 被中断的部分回复不入记忆
            content = ev.payload.get("content", "")
            if content:
                utterances.append(
                    Utterance(
                        speaker="agent",
                        text=content,
                        ts=ev.ts,
                        source_ref=f"{session_id}#{ev.uuid}",
                    )
                )
        # 其余事件类型：丢弃（噪声 / 非对话内容）
    return ConversationFragment(
        session_id=session_id,
        utterances=utterances,
        persona_id=persona_id,
        owner_user_id=owner_user_id,
    )
