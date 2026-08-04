"""Restricted decimal arithmetic and basic statistics."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext
from typing import Any, ClassVar

from ...protocol import ToolResult

_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d*)?|\.\d+)|(.))")
_MAX_EXPRESSION_LENGTH = 512
_MAX_DEPTH = 32
_MAX_EXPONENT = 100
_MAX_VALUES = 1000
_MAX_ABS_ADJUSTED = 10_000


class _ExpressionError(ValueError):
    pass


@dataclass
class _Parser:
    tokens: list[str]
    position: int = 0

    @classmethod
    def from_text(cls, text: str) -> _Parser:
        text = text.strip()
        if not text or len(text) > _MAX_EXPRESSION_LENGTH:
            raise _ExpressionError("表达式为空或过长")
        tokens: list[str] = []
        offset = 0
        while offset < len(text):
            match = _TOKEN.match(text, offset)
            if match is None:
                raise _ExpressionError("表达式包含无法识别的内容")
            number, symbol = match.groups()
            offset = match.end()
            if number is not None:
                tokens.append(number)
            elif symbol in "+-*/()":
                if symbol == "*" and offset < len(text) and text[offset] == "*":
                    tokens.append("**")
                    offset += 1
                else:
                    tokens.append(symbol)
            elif symbol and symbol.isspace():
                continue
            else:
                raise _ExpressionError(f"不支持的符号: {symbol!r}")
        return cls(tokens)

    def parse(self) -> Decimal:
        value = self._expression(0)
        if self.position != len(self.tokens):
            raise _ExpressionError("表达式末尾存在多余内容")
        return value

    def _expression(self, depth: int) -> Decimal:
        value = self._term(depth)
        while self._peek() in {"+", "-"}:
            operator = self._take()
            right = self._term(depth)
            value = value + right if operator == "+" else value - right
        return _bounded(value)

    def _term(self, depth: int) -> Decimal:
        value = self._unary(depth)
        while self._peek() in {"*", "/"}:
            operator = self._take()
            right = self._unary(depth)
            if operator == "*":
                value *= right
            else:
                if right == 0:
                    raise DivisionByZero
                value /= right
        return _bounded(value)

    def _power(self, depth: int) -> Decimal:
        value = self._primary(depth)
        if self._peek() == "**":
            self._take()
            exponent = self._unary(depth)
            if exponent != exponent.to_integral_value() or abs(exponent) > _MAX_EXPONENT:
                raise _ExpressionError("乘方指数必须是绝对值不超过 100 的整数")
            value = value ** int(exponent)
        return _bounded(value)

    def _unary(self, depth: int) -> Decimal:
        if self._peek() == "+":
            self._take()
            return self._unary(depth)
        if self._peek() == "-":
            self._take()
            return -self._unary(depth)
        return self._power(depth)

    def _primary(self, depth: int) -> Decimal:
        if depth > _MAX_DEPTH:
            raise _ExpressionError("表达式嵌套过深")
        token = self._take()
        if token == "(":
            value = self._expression(depth + 1)
            if self._take() != ")":
                raise _ExpressionError("括号不匹配")
            return value
        try:
            return _decimal(token)
        except (InvalidOperation, ValueError) as exc:
            raise _ExpressionError("需要数字或左括号") from exc

    def _peek(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def _take(self) -> str:
        token = self._peek()
        if token is None:
            raise _ExpressionError("表达式意外结束")
        self.position += 1
        return token


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("数值类型无效")
    decimal_value = Decimal(str(value))
    if not decimal_value.is_finite():
        raise ValueError("数值必须是有限数")
    return _bounded(decimal_value)


def _bounded(value: Decimal) -> Decimal:
    if not value.is_finite() or (value and abs(value.adjusted()) > _MAX_ABS_ADJUSTED):
        raise _ExpressionError("计算结果超出支持范围")
    return value


def _render(value: Decimal) -> str:
    if value == 0:
        return "0"
    normalized = value.normalize()
    if -12 <= normalized.adjusted() <= 50:
        return format(normalized, "f")
    return format(normalized, "E")


class CalculateTool:
    """Exact decimal calculations without arbitrary code execution."""

    name: ClassVar[str] = "calculate"
    result_context_retention: ClassVar[str] = "recoverable"
    execution_kind: ClassVar[str] = "parallel_read"
    timeout_seconds: ClassVar[float] = 5.0
    description: ClassVar[str] = (
        "对需要精确结果的算术、百分比和基础统计使用此工具，不要靠心算。"
        "evaluate 只接受数字、括号和 + - * / **；百分比和统计使用对应结构化 operation。"
        "不要用于汇率、实时数据、符号代数、方程、微积分或任意代码。"
        "失败时说明未取得计算结果，不要猜测。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "evaluate",
                    "percentage_of",
                    "percentage_change",
                    "increase_by_percentage",
                    "decrease_by_percentage",
                    "statistics",
                ],
            },
            "expression": {"type": "string"},
            "value": {"type": ["string", "number"]},
            "percentage": {"type": ["string", "number"]},
            "old_value": {"type": ["string", "number"]},
            "new_value": {"type": ["string", "number"]},
            "values": {"type": "array", "items": {"type": ["string", "number"]}},
            "statistics": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["sum", "count", "mean", "median", "min", "max"],
                },
            },
        },
        "required": ["operation"],
        "additionalProperties": False,
    }

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        operation = args.get("operation")
        try:
            with localcontext() as context:
                context.prec = 50
                payload = self._calculate(str(operation), args)
        except DivisionByZero:
            return _error("divide_by_zero", "计算失败：不能除以零。")
        except (InvalidOperation, _ExpressionError, ValueError, KeyError) as exc:
            return _error("invalid_arguments", f"计算失败：{exc}")
        return ToolResult(
            text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            meta={"operation": operation, "precision": 50},
        )

    def _calculate(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        if operation == "evaluate":
            expression = _required_str(args, "expression")
            result = _Parser.from_text(expression).parse()
            return {"operation": operation, "expression": expression, "result": _render(result)}
        if operation == "percentage_of":
            value = _decimal(args["value"])
            percentage = _decimal(args["percentage"])
            result = _bounded(value * percentage / Decimal(100))
            return _percent_payload(operation, value, percentage, result)
        if operation in {"increase_by_percentage", "decrease_by_percentage"}:
            value = _decimal(args["value"])
            percentage = _decimal(args["percentage"])
            delta = value * percentage / Decimal(100)
            result = value + delta if operation.startswith("increase") else value - delta
            return _percent_payload(operation, value, percentage, _bounded(result))
        if operation == "percentage_change":
            old_value = _decimal(args["old_value"])
            new_value = _decimal(args["new_value"])
            if old_value == 0:
                raise DivisionByZero
            result = _bounded((new_value - old_value) / old_value * Decimal(100))
            return {
                "operation": operation,
                "old_value": _render(old_value),
                "new_value": _render(new_value),
                "result_percentage": _render(result),
            }
        if operation == "statistics":
            raw_values = args.get("values")
            if not isinstance(raw_values, list) or not raw_values or len(raw_values) > _MAX_VALUES:
                raise ValueError("values 必须包含 1 到 1000 个数")
            values = [_decimal(item) for item in raw_values]
            requested = args.get("statistics") or ["sum", "count", "mean", "median", "min", "max"]
            if not isinstance(requested, list) or not requested:
                raise ValueError("statistics 必须是非空列表")
            allowed = {"sum", "count", "mean", "median", "min", "max"}
            if any(item not in allowed for item in requested):
                raise ValueError("statistics 包含不支持的统计项")
            ordered = sorted(values)
            total = sum(values, Decimal(0))
            middle = len(ordered) // 2
            median = (
                ordered[middle]
                if len(ordered) % 2
                else (ordered[middle - 1] + ordered[middle]) / Decimal(2)
            )
            all_results: dict[str, str | int] = {
                "sum": _render(total),
                "count": len(values),
                "mean": _render(total / Decimal(len(values))),
                "median": _render(median),
                "min": _render(ordered[0]),
                "max": _render(ordered[-1]),
            }
            return {
                "operation": operation,
                "input_count": len(values),
                "results": {key: all_results[key] for key in requested},
            }
        raise ValueError(f"不支持的 operation: {operation!r}")


def _required_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 必须是非空字符串")
    return value


def _percent_payload(
    operation: str, value: Decimal, percentage: Decimal, result: Decimal
) -> dict[str, str]:
    return {
        "operation": operation,
        "value": _render(value),
        "percentage": _render(percentage),
        "result": _render(result),
    }


def _error(kind: str, text: str) -> ToolResult:
    return ToolResult(text=text, is_error=True, meta={"error_kind": kind})
