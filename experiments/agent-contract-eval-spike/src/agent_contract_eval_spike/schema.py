"""Spike 内部数据类。

- :class:`Contract`：从 ``cases/c*.yaml`` 顶部 ``contract:`` 块解析
- :class:`Case`：契约下单条 query fixture
- :class:`Trace`：跑完一个 case 后收集到的 agent 行为(tool_calls + final text)
- :class:`Verdict`：判分结果(pass/fail + reason)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    id: str
    query: str
    facet: str
    source: str
    forbidden_terms: list[str] = field(default_factory=list)  # 仅 C3 用


@dataclass(frozen=True)
class Contract:
    id: str  # "C1" / "C2" / "C3"
    name: str
    expected_behavior: str  # "tool_must_be_called" / "identity_must_hold"
    expected_tool: str | None  # C1/C2 才有
    required_in_response: str | None  # C3 才有
    cases: list[Case]


@dataclass(frozen=True)
class Trace:
    case_id: str
    tool_calls: list[str]  # 顺序保留;只含工具名(参数细节不参与 v1 判分)
    final_text: str
    stop_reason: str  # 来自 TurnDone.stop_reason
    error: str | None = None  # 跑挂了的话记原因,Verdict 直接标 fail


@dataclass(frozen=True)
class Verdict:
    case_id: str
    contract_id: str
    passed: bool
    reason: str
