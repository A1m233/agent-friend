"""``agent`` 模块自有异常。

跟 :mod:`llm_providers.errors` 设计一致：上层调用方（CLI / 未来 API）
``catch`` 这些项目级异常做对外提示，**不直接暴露 Python 内置异常**
（如 ``FileNotFoundError``）。
"""

from __future__ import annotations


class AgentError(Exception):
    """所有 ``agent`` 模块异常的基类。"""


class MalformedToolCallError(AgentError):
    """模型连续生成不符合公开 schema 的工具调用参数。"""

    reason_code = "malformed_tool_call"

    def __init__(self, validation_reason: str) -> None:
        super().__init__(validation_reason)
        self.validation_reason = validation_reason


class PersonaNotFoundError(AgentError):
    """指定 persona（按 name 或 id 查）在 external_dir 与内置 personas 都找不到。"""


class PersonaReadOnlyError(AgentError):
    """对 builtin persona 做写操作（update / delete / rename）时抛出。

    builtin 是包资产，只读。如需"覆盖"行为，请在 user 维度 ``create`` 一个同名
    persona——数据层是"并列"而非"覆盖"，CLI 默认按 user 优先解析。
    """


class PersonaAlreadyExistsError(AgentError):
    """``create`` / ``rename`` 目标 name 在 user 维度已存在时抛出。"""


class PersonaPersistError(AgentError):
    """persona 文件持久化失败（IO 错）时抛出。"""


class PersonaContentInvalidError(AgentError):
    """单一可编辑人格正文不满足确定性格式约束。"""


class PersonaRevisionConflictError(AgentError):
    """应用草稿时源文件已被外部修改，禁止静默覆盖。"""


class PersonaUndoUnavailableError(AgentError):
    """当前没有可消费的引导式应用撤销点。"""


class PersonaAmbiguousError(AgentError):
    """name 在多个 source 下歧义且未显式指定时抛出（本期为预留）。

    本期 ``find_by_name(name)`` 按 "user 优先" 自动消歧，不会触发此错误。
    保留接口位置给未来支持更多 source（如远端 catalog）时使用。
    """
