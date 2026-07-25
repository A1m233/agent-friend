"""上下文组装阶段的稳定错误类型。"""


class ContextInputTooLargeError(ValueError):
    """固定输入或不可拆的当前轮在安全收缩后仍超过模型预算。"""

    code = "turn_input_too_large"


class ContextBaseOverBudgetError(ContextInputTooLargeError):
    """系统提示词、额外上下文和工具定义本身已超过安全预算。"""

    code = "base_prompt_over_budget"
