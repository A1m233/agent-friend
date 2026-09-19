"""上下文组装阶段的稳定错误类型。"""


class ContextCompactionUnavailableError(RuntimeError):
    """必要压缩未完成；保留历史，等待显式重试或配置修复。"""

    code = "context_compaction_unavailable"

    def __init__(self, message: str, *, reason: str = "unavailable") -> None:
        super().__init__(message)
        self.reason = reason


class ContextInputTooLargeError(ValueError):
    """固定输入或不可拆的当前轮在安全收缩后仍超过模型预算。"""

    code = "turn_input_too_large"


class ContextBaseOverBudgetError(ContextInputTooLargeError):
    """系统提示词、额外上下文和工具定义本身已超过安全预算。"""

    code = "base_prompt_over_budget"
