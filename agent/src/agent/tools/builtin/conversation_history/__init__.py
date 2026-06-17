"""``conversation_history`` —— 会话记录查询工具子包。

对外仅暴露 :class:`ConversationHistoryTool`（实现 :class:`agent.tools.Tool` Protocol）。
其它内部数据结构（``Hit`` / 时间解析等）不进入公共 API。

详见 docs/requirements/020-engine-tool-conversation-history/design.md §4.1 ~ §4.4。
"""

from .tool import ConversationHistoryTool

__all__ = ["ConversationHistoryTool"]
