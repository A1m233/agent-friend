from __future__ import annotations

from typing import Any, ClassVar, cast

from agent.tools import Tool, ToolRegistry, ToolResult
from agent.tools.builtin.calculate import CalculateTool
from agent.tools.builtin.social_platforms import SocialReadTool, SocialSearchTool
from agent.tools.builtin.social_platforms.service import SocialPlatformService

from agent import ToolId


class _ExternalEchoTool:
    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "echo"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(text=str(args))


class _PinnedEchoTool(_ExternalEchoTool):
    name: ClassVar[str] = "pinned_echo"
    result_context_retention: ClassVar[str] = "pinned"


class _InvalidRetentionTool(_ExternalEchoTool):
    name: ClassVar[str] = "invalid_retention"
    result_context_retention: ClassVar[str] = "typo"


def test_builtin_namespace_is_derived_from_relative_capability_package() -> None:
    registry = ToolRegistry([CalculateTool()])

    assert registry.identity("calculate") == ToolId(
        namespace="calculate",
        name="calculate",
    )
    assert registry.all_identities() == [ToolId(namespace="calculate", name="calculate")]


def test_tools_in_one_capability_share_generated_namespace() -> None:
    unused_service = cast(SocialPlatformService, object())
    registry = ToolRegistry(
        cast(
            list[Tool],
            [SocialSearchTool(unused_service), SocialReadTool(unused_service)],
        )
    )

    assert registry.identity("social_search").namespace == "social_platforms"
    assert registry.identity("social_read").namespace == "social_platforms"
    assert registry.identity("social_read").qualified_name == "social_platforms/social_read"
    assert registry.render_capability_catalog() == (
        "<tool_capability_catalog>\n"
        "  <instruction>namespace 是工具相对于内置工具根目录的可信模块路径；"
        "调用时仍使用 callable_as。</instruction>\n"
        '  <namespace path="social_platforms">\n'
        '    <tool name="social_read" callable_as="social_read" />\n'
        '    <tool name="social_search" callable_as="social_search" />\n'
        "  </namespace>\n"
        "</tool_capability_catalog>"
    )


def test_catalog_filter_and_wire_names_remain_backward_compatible() -> None:
    registry = ToolRegistry([CalculateTool()])

    assert registry.render_capability_catalog(callable_names=set()) == ""
    assert registry.to_openai_tools()[0]["function"]["name"] == "calculate"
    assert registry.invoke("calculate", {"operation": "evaluate", "expression": "1+1"})


def test_injected_tool_outside_builtin_root_remains_callable_but_unscoped() -> None:
    registry = ToolRegistry([_ExternalEchoTool()])

    assert registry.identity("echo") == ToolId(namespace=None, name="echo")
    assert registry.render_capability_catalog() == ""
    assert registry.invoke("echo", {"value": "ok"}).text == "{'value': 'ok'}"


def test_result_retention_defaults_to_recoverable_and_fails_safe() -> None:
    registry = ToolRegistry(
        cast(list[Tool], [_ExternalEchoTool(), _PinnedEchoTool(), _InvalidRetentionTool()])
    )

    assert registry.result_context_retention("echo") == "recoverable"
    assert registry.result_context_retention("pinned_echo") == "pinned"
    assert registry.result_context_retention("invalid_retention") == "pinned"
    assert registry.result_context_retention("not_registered") == "pinned"
