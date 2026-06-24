"""Spike 内部数据类。

- :class:`Contract`:从 ``cases/c*.yaml`` 顶部 ``contract:`` 块解析
- :class:`Case`:契约下单条 query fixture
- :class:`FixtureSession` / :class:`FixtureMessage`:C1 v2 引入的"伪过去对话"
  fixture,跑前注入到 SessionStore 让 ``recall_past_chats`` 真能召回到内容
- :class:`CaseJudge`:case 级判分规则(v2 fixture-grounded 模式专用)
- :class:`ToolCallTrace`:单次 tool_call 的完整快照(name + args + result + error)
- :class:`Trace`:跑完一个 case 后收集到的 agent 行为
- :class:`Verdict`:判分结果
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FixtureMessage:
    """fixture session 中一条历史消息。"""

    role: str  # "user" / "assistant"
    text: str


@dataclass(frozen=True)
class FixtureSession:
    """一条伪过去对话,跑 case 前注入到 SessionStore。

    Attributes:
        days_ago: session 创建时间偏移(当前时间减 N 天),让 recall 工具按时间排序合理
        messages: 历史消息序列,会被翻译成 user_message / assistant_message events
    """

    days_ago: int
    messages: list[FixtureMessage]


@dataclass(frozen=True)
class CaseJudge:
    """v2 fixture-grounded 判分规则,声明在单条 case 上。

    Attributes:
        must_call: trace.tool_calls 必须含此工具名;为 None 则沿用 contract.expected_tool
        must_mention_any: final_text 必须至少出现其中一个关键词(case-insensitive substring),
            空列表 = 跳过此项检查。用于把 final_text 锚到 fixture 里真实出现过的实词。
        must_not_mention_any: final_text 不能出现任何一个关键词,空列表 = 跳过。
            用于抓 fabrication:这些词在 fixture 里**没有**,但 agent 可能凭印象编。
    """

    must_call: str | None = None
    must_mention_any: list[str] = field(default_factory=list)
    must_not_mention_any: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Case:
    id: str
    query: str
    facet: str
    source: str
    forbidden_terms: list[str] = field(default_factory=list)  # 仅 C3 用
    fixture_session: FixtureSession | None = None  # v2 fixture(C1 引入)
    judge: CaseJudge | None = None  # v2 case 级判分;None = 走 contract 级 v1 规则


@dataclass(frozen=True)
class Contract:
    id: str  # "C1" / "C2" / "C3"
    name: str
    expected_behavior: str  # "tool_must_be_called" / "identity_must_hold"
    expected_tool: str | None  # C1/C2 才有
    required_in_response: str | None  # C3 才有
    cases: list[Case]


@dataclass(frozen=True)
class ToolCallTrace:
    """单次工具调用的完整快照,排查 agent 中间 reasoning 用。"""

    name: str
    args: dict[str, Any]
    result_text: str  # 工具回喂给 LLM 的文本
    is_error: bool


@dataclass(frozen=True)
class Trace:
    case_id: str
    tool_calls: list[ToolCallTrace]  # 顺序保留;含完整 args + result
    final_text: str
    stop_reason: str  # 来自 TurnDone.stop_reason
    error: str | None = None  # 跑挂了的话记原因,Verdict 直接标 fail


@dataclass(frozen=True)
class Verdict:
    case_id: str
    contract_id: str
    passed: bool
    reason: str
