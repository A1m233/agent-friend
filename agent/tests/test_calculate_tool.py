from __future__ import annotations

import json
from typing import Any, cast

from agent.tools.builtin.calculate import CalculateTool


def _payload(**args: object) -> dict[str, object]:
    result = CalculateTool().invoke(args)
    assert not result.is_error, result.text
    return cast(dict[str, Any], json.loads(result.text))


def test_evaluate_uses_decimal_and_precedence() -> None:
    payload = _payload(operation="evaluate", expression="  (12.5 + 3) * 4 - 0.1  ")
    assert payload["result"] == "61.9"


def test_evaluate_uses_conventional_power_and_unary_precedence() -> None:
    negative_square = _payload(operation="evaluate", expression="-2 ** 2")
    right_associative = _payload(operation="evaluate", expression="2 ** 3 ** 2")
    assert negative_square["result"] == "-4"
    assert right_associative["result"] == "512"


def test_percentage_operations() -> None:
    assert _payload(operation="percentage_of", value="200", percentage="15")["result"] == "30"
    assert (
        _payload(operation="increase_by_percentage", value="80", percentage="12.5")["result"]
        == "90"
    )
    assert (
        _payload(operation="decrease_by_percentage", value="200", percentage="15")["result"]
        == "170"
    )
    assert (
        _payload(operation="percentage_change", old_value="80", new_value="100")[
            "result_percentage"
        ]
        == "25"
    )


def test_statistics_are_exact() -> None:
    payload = _payload(operation="statistics", values=["1.1", "2.2", "3.3", "8.8"])
    assert payload["results"] == {
        "count": 4,
        "max": "8.8",
        "mean": "3.85",
        "median": "2.75",
        "min": "1.1",
        "sum": "15.4",
    }


def test_rejects_division_by_zero_and_code_like_input() -> None:
    divided = CalculateTool().invoke({"operation": "evaluate", "expression": "1 / 0"})
    injected = CalculateTool().invoke(
        {"operation": "evaluate", "expression": "__import__('os').system('whoami')"}
    )
    assert divided.is_error and divided.meta["error_kind"] == "divide_by_zero"
    assert injected.is_error


def test_rejects_non_integer_or_large_exponents() -> None:
    fractional = CalculateTool().invoke({"operation": "evaluate", "expression": "2 ** 0.5"})
    huge = CalculateTool().invoke({"operation": "evaluate", "expression": "2 ** 101"})
    assert fractional.is_error
    assert huge.is_error
