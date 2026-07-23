"""LLM Provider 抽象层错误模型。

把底层（LiteLLM / OpenAI SDK）的各种异常抹平成项目自定义的 5 类
``LLMError`` 子类，让上层（Conversation / CLI）只需依赖本模块的异常类型，
不需要 import LiteLLM。

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.1.3。
"""


class LLMError(Exception):
    """所有 LLM 相关错误的基类。"""


class LLMAuthError(LLMError):
    """API key 错误或缺失。CLI 层期望行为：fail fast。"""


class LLMRateLimitError(LLMError):
    """被 Provider 限速。上层期望行为：记录并使用兜底话术。"""


class LLMNetworkError(LLMError):
    """网络错误或超时。LLMClient 有限重试用尽后交给上层兜底。"""


class LLMBadRequestError(LLMError):
    """请求格式错或上下文超长。多半是 bug，CLI 层不重试。"""


class LLMContentPolicyError(LLMError):
    """请求内容被 Provider 的安全策略拒绝；原样重试通常仍会失败。"""


class LLMProviderError(LLMError):
    """其他 Provider 侧错误。CLI 层期望行为：兜底话术。"""
