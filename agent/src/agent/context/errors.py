"""上下文组装阶段的稳定错误类型。"""


class ContextInputTooLargeError(ValueError):
    """固定输入或不可拆的当前轮在安全收缩后仍超过模型预算。"""

    code = "context_input_too_large"
