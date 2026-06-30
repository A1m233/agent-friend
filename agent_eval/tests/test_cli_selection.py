from __future__ import annotations

import pytest
from agent_eval.schema import Case, Contract
from agent_eval.selection import Selection, select_contracts


def _case(case_id: str) -> Case:
    return Case(id=case_id, query=f"query {case_id}", facet="", source="")


def _contracts() -> list[Contract]:
    return [
        Contract(
            id="C1",
            name="one",
            expected_behavior="tool_must_be_called",
            expected_tool="recall_past_chats",
            required_in_response=None,
            cases=[_case("C1-01"), _case("C1-02")],
        ),
        Contract(
            id="C2",
            name="two",
            expected_behavior="tool_must_be_called",
            expected_tool="web_search",
            required_in_response=None,
            cases=[_case("C2-01")],
        ),
        Contract(
            id="C5",
            name="five",
            expected_behavior="tool_must_be_called",
            expected_tool="recall_past_chats",
            required_in_response=None,
            cases=[_case("C5-01")],
        ),
    ]


def test_select_all() -> None:
    result = select_contracts(_contracts(), Selection())

    assert result.label == "all"
    assert result.total_cases == 4
    assert [contract.id for contract in result.contracts] == ["C1", "C2", "C5"]


def test_select_contract() -> None:
    result = select_contracts(_contracts(), Selection(contract="C5"))

    assert result.label == "C5"
    assert result.total_cases == 1
    assert [contract.id for contract in result.contracts] == ["C5"]


def test_case_overrides_contract_and_limit() -> None:
    result = select_contracts(
        _contracts(),
        Selection(contract="C2", case_id="C1-02", limit_cases=1),
    )

    assert result.label == "C1-02"
    assert result.total_cases == 1
    assert result.contracts[0].id == "C1"
    assert result.contracts[0].cases[0].id == "C1-02"


def test_limit_cases_per_contract() -> None:
    result = select_contracts(_contracts(), Selection(limit_cases=1))

    assert result.label == "all-limit1"
    assert result.total_cases == 3
    assert [len(contract.cases) for contract in result.contracts] == [1, 1, 1]


def test_missing_target_fails() -> None:
    with pytest.raises(ValueError, match="没找到契约"):
        select_contracts(_contracts(), Selection(contract="C9"))
    with pytest.raises(ValueError, match="没找到 case"):
        select_contracts(_contracts(), Selection(case_id="C9-01"))
