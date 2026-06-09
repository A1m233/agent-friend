"""上下文管理：决定每次调 LLM 时实际发什么 ``messages``。

009 起把原单文件 ``context.py`` 升级为 ``context/`` 包，按职责拆分：

- :mod:`protocol` —— ``ContextManager`` Protocol + 数据契约（稳定核心）
- :mod:`naive` —— ``NaiveContextManager``（全发不截断）
- :mod:`budget` —— token 度量 + 阈值推导（M1）
- :mod:`fifo` —— ``FifoContextManager``（防爆窗兜底，M2）
- :mod:`summarizing` —— ``SummarizingContextManager``（摘要压缩主策略，M3）
- :mod:`summary_prompt` —— 摘要 prompt 模板 + 转录渲染（M3）

``Conversation`` 通过（per-session 工厂产出的）实例注入即可切换策略。

详见 docs/requirements/009-engine-context-management/design.md。
"""

from __future__ import annotations

from .budget import (
    BUFFER_RATIO,
    CHARS_TO_TOKENS,
    OUTPUT_RESERVE_RATIO,
    estimate_tokens,
    make_budget_snapshot,
)
from .fifo import RECENT_PROTECT_TURNS, FifoContextManager
from .naive import NaiveContextManager
from .protocol import (
    BudgetSnapshot,
    BuildResult,
    CompactionRecord,
    ContextManager,
    PriorSummary,
    RuntimeContext,
    assemble_messages,
)
from .summarizing import (
    MAX_CONSECUTIVE_FAILURES,
    SUMMARY_RECENT_TAIL_TURNS,
    SummarizingContextManager,
)


def default_context_manager() -> ContextManager:
    """默认上下文管理策略工厂（009 M3 起：摘要压缩主策略 + FIFO 兜底）。

    供装配点（CLI / bridge）作 ``context_manager_factory`` 传入——**每个会话产一个
    独立实例**（摘要策略持会话级 circuit breaker 状态，见 design §7）。把"默认用哪个
    策略"收敛到此单点，未来调整默认（或按 env 分流）只改这里。
    """
    return SummarizingContextManager(fallback=FifoContextManager())


__all__ = [
    "BUFFER_RATIO",
    "CHARS_TO_TOKENS",
    "MAX_CONSECUTIVE_FAILURES",
    "OUTPUT_RESERVE_RATIO",
    "RECENT_PROTECT_TURNS",
    "SUMMARY_RECENT_TAIL_TURNS",
    "BudgetSnapshot",
    "BuildResult",
    "CompactionRecord",
    "ContextManager",
    "FifoContextManager",
    "NaiveContextManager",
    "PriorSummary",
    "RuntimeContext",
    "SummarizingContextManager",
    "assemble_messages",
    "default_context_manager",
    "estimate_tokens",
    "make_budget_snapshot",
]
