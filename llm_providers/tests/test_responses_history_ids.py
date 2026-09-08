"""Responses request IDs must stay unique without losing historical tool pairing."""

import copy
from typing import Any

import pytest
from llm_providers.errors import LLMBadRequestError
from llm_providers.responses.request import convert_messages


def call(identity: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": identity,
                "type": "function",
                "function": {"name": "calculate", "arguments": "{}"},
            }
        ],
    }


def output(identity: str, text: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": identity, "content": text}


def test_reused_history_ids_preserve_results_and_source() -> None:
    messages = [call("exec_command_28"), output("exec_command_28", "first")]
    prefix = convert_messages(messages)
    messages += [call("exec_command_28"), output("exec_command_28", "391")]
    original = copy.deepcopy(messages)
    items = convert_messages(messages)
    assert items[:2] == prefix
    assert items == convert_messages(messages)
    assert messages == original
    assert items[0]["call_id"] == items[1]["call_id"] == "exec_command_28"
    assert items[2]["call_id"] == items[3]["call_id"] != items[0]["call_id"]
    assert items[1]["output"] == "first" and items[3]["output"] == "391"


def test_generated_id_collision_does_not_rewrite_prior_prefix() -> None:
    messages = [call("x"), output("x", "one"), call("x"), output("x", "two")]
    prefix = convert_messages(messages)
    generated = prefix[-1]["call_id"]
    messages += [call(generated), output(generated, "three")]
    items = convert_messages(messages)
    assert items[:4] == prefix
    assert items[-2]["call_id"] == items[-1]["call_id"]
    assert len({item["call_id"] for item in items if item["type"] == "function_call"}) == 3


def test_parallel_results_follow_source_ids_even_in_reverse_order() -> None:
    messages = [call("x"), output("x", "old")]
    parallel = call("x")
    parallel["tool_calls"] += call("y")["tool_calls"]
    items = convert_messages([*messages, parallel, output("y", "Y"), output("x", "X")])
    assert items[2]["call_id"] == items[5]["call_id"] != "x"
    assert items[3]["call_id"] == items[4]["call_id"] == "y"
    assert [item["output"] for item in items if item["type"] == "function_call_output"] == [
        "old",
        "Y",
        "X",
    ]


@pytest.mark.parametrize(
    "messages",
    [
        [call("x"), call("x"), output("x", "ambiguous")],
        [call("x"), output("x", "one"), output("x", "duplicate")],
        [{"role": "assistant", "tool_calls": call("x")["tool_calls"] * 2}],
    ],
)
def test_ambiguous_pairing_is_rejected(messages: list[dict[str, Any]]) -> None:
    with pytest.raises(LLMBadRequestError):
        convert_messages(messages)


def test_unique_ids_and_existing_standalone_output_conversion_are_unchanged() -> None:
    items = convert_messages([call("x"), output("x", "X"), output("external", "Y")])
    assert [item["call_id"] for item in items] == ["x", "x", "external"]


def test_standalone_output_cannot_alias_a_generated_wire_id() -> None:
    messages = [call("x"), output("x", "one"), call("x")]
    generated = convert_messages(messages)[-1]["call_id"]
    with pytest.raises(LLMBadRequestError):
        convert_messages([*messages, output(generated, "wrong source identity")])


def test_many_reuses_produce_bounded_unique_ids() -> None:
    identity = "x" * 64
    messages = [
        message
        for index in range(1000)
        for message in (call(identity), output(identity, str(index)))
    ]
    items = convert_messages(messages)
    ids = [item["call_id"] for item in items[::2]]
    assert len(set(ids)) == 1000
    assert all(len(identity) <= 64 for identity in ids)
    assert all(
        items[index]["call_id"] == items[index + 1]["call_id"] for index in range(0, len(items), 2)
    )
