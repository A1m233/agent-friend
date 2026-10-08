"""The offline history probe checks its byte and association measurements."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.responses import JSONResponse

probe = runpy.run_path(str(Path(__file__).with_name("cold_cache_probe.py")))
measure = probe["history_measurements"]


def item(uuid: str, kind: str, correlation: str, *, run: str = "run") -> dict[str, Any]:
    return {
        "type": kind,
        "uuid": uuid,
        "payload": {"event_tool_call_id": correlation, "label": "汉字😀"},
        "run": {"run_id": run},
        "body": {"state": "deferred"},
    }


def test_compact_measurement_matches_http_json_and_allows_pending_request() -> None:
    request = item("request", "tool_call_request", "call")
    request["result_event"] = item("result", "tool_call_result", "call")
    payload = {"events": [request, item("pending", "tool_call_request", "pending")]}
    result = measure(payload)
    assert result["compact_response_bytes"] == len(JSONResponse(payload).body)
    assert result["response_bytes"] > result["compact_response_bytes"]
    assert result["tool_requests"] == 2
    assert result["paired_results"] == 1
    assert result["pending_tool_requests"] == 1
    assert result["deferred_bodies"] == 3
    reordered = json.loads(json.dumps(payload, sort_keys=True))
    assert measure(reordered)["history_page_sha256"] == result["history_page_sha256"]
    reordered["events"][0]["payload"]["label"] = "别字😀"
    changed = measure(reordered)
    assert changed["compact_response_bytes"] == result["compact_response_bytes"]
    assert changed["history_page_sha256"] != result["history_page_sha256"]


@pytest.mark.parametrize("mismatch", ["correlation", "run"])
def test_pair_checks_require_matching_request_and_run(mismatch: str) -> None:
    request = item("request", "tool_call_request", "call")
    request["result_event"] = item(
        "result",
        "tool_call_result",
        "other" if mismatch == "correlation" else "call",
        run="other" if mismatch == "run" else "run",
    )
    with pytest.raises(ValueError, match="pair_mismatch"):
        measure({"events": [request]})


def test_response_limits_measure_encoded_bytes_and_item_count() -> None:
    large = item("large", "assistant_message", "")
    large["payload"]["content"] = "😀" * 70000
    with pytest.raises(ValueError, match="response_limit"):
        measure({"events": [large]})
    with pytest.raises(ValueError, match="response_limit"):
        measure({"events": [item(str(n), "assistant_message", "") for n in range(81)]})


def test_canonical_business_digest_ignores_dict_order_but_keeps_event_order() -> None:
    first = {"type": "user_message", "payload": {"content": "汉字😀", "other": 1}}
    reordered = {"payload": {"other": 1, "content": "汉字😀"}, "type": "user_message"}
    events = [
        SimpleNamespace(to_jsonl=lambda value=value: json.dumps(value))
        for value in (
            first,
            reordered,
            {"type": "assistant_message", "payload": {"content": "next"}},
        )
    ]
    digest = probe["event_digest"]
    assert digest(events[:1]) != digest(events[1:2])
    assert digest(events[:1], canonical=True) == digest(events[1:2], canonical=True)
    assert digest([events[0], events[2]], canonical=True) != digest(
        [events[2], events[0]], canonical=True
    )


def test_history_digest_validation_compares_four_accesses_within_repeat() -> None:
    validate = probe["validate_repeat"]
    conditions = [{"rows": [{"history_page_sha256": "same"} for _ in range(2)]} for _ in range(2)]
    assert validate("history", conditions) == ["history_page_sha256"]
    conditions[1]["rows"][1]["history_page_sha256"] = "different"
    with pytest.raises(ValueError, match="history_page_sha256"):
        validate("history", conditions)
    with pytest.raises(ValueError, match="four_accesses"):
        validate("history", conditions[:1])


def test_execution_equivalence_uses_canonical_business_and_exact_restore_digests() -> None:
    conditions = [
        {
            "rows": [
                {
                    "business_sha256": f"different-legacy-order-{condition}-{access}",
                    "canonical_business_sha256": "same-business",
                    "restore_sha256": "same-restored-state",
                    "exact_digest_comparable": True,
                }
                for access in range(2)
            ]
        }
        for condition in range(2)
    ]
    assert probe["validate_repeat"]("execution", conditions) == [
        "canonical_business_sha256",
        "restore_sha256",
    ]
    conditions[1]["rows"][0]["restore_sha256"] = "changed-state"
    with pytest.raises(ValueError, match="restore_sha256"):
        probe["validate_repeat"]("execution", conditions)


def test_legacy_history_fallback_compares_business_when_bounded_api_is_absent() -> None:
    conditions = [
        {
            "rows": [
                {
                    "read_kind": "legacy_query_then_page",
                    "canonical_business_sha256": "same-business",
                }
                for _ in range(2)
            ]
        }
        for _ in range(2)
    ]
    assert probe["validate_repeat"]("history", conditions) == ["canonical_business_sha256"]
