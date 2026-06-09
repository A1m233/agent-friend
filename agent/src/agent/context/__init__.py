"""上下文管理：决定每次调 LLM 时实际发什么 ``messages``。

009 起把原单文件 ``context.py`` 升级为 ``context/`` 包，按职责拆分：

- :mod:`protocol` —— ``ContextManager`` Protocol + 数据契约（稳定核心）
- :mod:`naive` —— ``NaiveContextManager``（全发不截断）
- :mod:`budget` —— token 度量 + 阈值推导（M1）
- ``fifo`` / ``summarizing`` —— 防爆窗兜底（M2）/ 摘要压缩（M3），后续里程碑落地

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

__all__ = [
    "BUFFER_RATIO",
    "CHARS_TO_TOKENS",
    "OUTPUT_RESERVE_RATIO",
    "BudgetSnapshot",
    "BuildResult",
    "CompactionRecord",
    "ContextManager",
    "NaiveContextManager",
    "PriorSummary",
    "RuntimeContext",
    "assemble_messages",
    "estimate_tokens",
    "make_budget_snapshot",
]
