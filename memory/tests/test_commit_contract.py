from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from memory.extraction import AsyncExtractionWorker, ExtractionContextBuilder, Extractor, Reconciler
from memory.extraction.deferred import decode, encode, observation_id

from llm_providers import ModelRequest, ModelResponse
from memory import ConversationFragment, Memory, SqliteMemoryStore, Utterance


class Model:
    def __init__(self, *outputs: str) -> None:
        self.outputs = list(outputs)
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(self.outputs.pop(0))


def proposal(*updates: dict[str, Any], episode: str | None = None) -> str:
    return json.dumps(
        {
            "core_updates": list(updates),
            "semantic_changes": [],
            "episodic_entries": [episode] if episode else [],
        }
    )


def update(value: str = "12345", *, op: str = "add") -> dict[str, Any]:
    item: dict[str, Any] = {"field": "user.bilibili_uid", "op": op, "reason": "用户本人身份"}
    if op != "remove":
        item["value"] = value
    if op != "add":
        item["target_ref"] = "C001"
    return item


def material(index: int = 0) -> ConversationFragment:
    return ConversationFragment(
        "s",
        [
            Utterance(
                "user",
                f"本人的账号确认 {index}",
                datetime(2026, 11, 1, tzinfo=UTC) + timedelta(seconds=index),
                f"s#{index}",
            )
        ],
        "p",
    )


def test_contract_error_is_repaired_once_with_exact_feedback() -> None:
    invalid = update()
    invalid.pop("reason")
    model = Model(proposal(invalid), proposal(update()))
    out = Extractor(model).extract(material(), [])  # type: ignore[arg-type]
    assert out.validation_error is None and out.core_updates[0].value == "12345"
    assert len(model.requests) == 2
    assert len(model.requests[0].messages) == 2
    assert "core_updates[0].reason" in model.requests[1].messages[-1]["content"]
    assert model.requests[1].control.allow_retries is False
    assert model.requests[1].control.trace_context is not None
    assert model.requests[1].control.trace_context.call_kind == "contract_repair"


def test_invalid_item_is_preserved_but_does_not_block_next_material(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "memory.db")
    model = Model("not json", "not json", proposal(episode="有效材料"))
    worker = AsyncExtractionWorker(store, Extractor(model), Reconciler(store))  # type: ignore[arg-type]
    receipt = worker.submit(material(0))
    worker.flush()
    assert store.extraction_status(receipt.observation_id).status == "blocked"
    next_receipt = worker.submit(material(1))
    worker.flush()
    worker.close()
    assert store.extraction_status(next_receipt.observation_id).status == "committed"
    assert len(model.requests) == 3
    store.close()
    reopened = SqliteMemoryStore(tmp_path / "memory.db")
    assert (
        reopened.extraction_status(receipt.observation_id).error_code == "invalid_extraction_output"
    )
    assert reopened.extraction_status(next_receipt.observation_id).status == "committed"
    reopened.close()


def test_core_barrier_is_idempotent_and_does_not_commit_full_observation() -> None:
    store = SqliteMemoryStore(":memory:")
    model = Model(proposal(update()), proposal(episode="用户确认账号"))
    memory = Memory(store, Extractor(model), Reconciler(store))  # type: ignore[arg-type]
    source = material()
    core = replace(source, extraction_scope="core")
    assert observation_id(source) != observation_id(core)
    assert decode(encode(core)) == core
    assert memory.ensure_core_profile(source, ("user.bilibili_uid",))["status"] == "ready"
    assert memory.ensure_core_profile(source, ("user.bilibili_uid",))["status"] == "ready"
    assert len(model.requests) == 1
    assert memory.observation_status(observation_id(source)).status == "unknown"
    assert memory.observation_status(observation_id(core)).status == "committed"
    full = memory.observe(source)
    memory.flush()
    assert memory.observation_status(full.observation_id).status == "committed"
    assert len(model.requests) == 2
    memory.close()
    store.close()


def test_core_barrier_honours_correction_and_withdrawal_before_action() -> None:
    store = SqliteMemoryStore(":memory:")
    model = Model(
        proposal(update()), proposal(update("67890", op="replace")), proposal(update(op="remove"))
    )
    memory = Memory(store, Extractor(model), Reconciler(store))  # type: ignore[arg-type]
    assert memory.ensure_core_profile(material(0), ("user.bilibili_uid",))["status"] == "ready"
    assert memory.ensure_core_profile(material(1), ("user.bilibili_uid",))["status"] == "ready"
    assert store.core_profile(owner_user_id="local", persona_id="p").entries[0].value == "67890"
    assert memory.ensure_core_profile(material(2), ("user.bilibili_uid",))["status"] == "missing"
    assert store.core_profile(owner_user_id="local", persona_id="p").is_empty()
    memory.close()
    store.close()


def test_failed_correction_cannot_use_old_identity_or_repeat_same_model_attempt() -> None:
    store = SqliteMemoryStore(":memory:")
    model = Model(proposal(update()), "broken", "broken")
    memory = Memory(store, Extractor(model), Reconciler(store))  # type: ignore[arg-type]
    assert memory.ensure_core_profile(material(0), ("user.bilibili_uid",))["status"] == "ready"
    assert memory.ensure_core_profile(material(1), ("user.bilibili_uid",))["status"] == "blocked"
    assert memory.ensure_core_profile(material(1), ("user.bilibili_uid",))["status"] == "blocked"
    assert len(model.requests) == 3
    assert store.core_profile(owner_user_id="local", persona_id="p").entries[0].value == "12345"
    memory.close()
    store.close()


def test_core_scope_cannot_write_other_memory_layers() -> None:
    store = SqliteMemoryStore(":memory:")
    model = Model(proposal(update(), episode="不该提前写入"), proposal(update()))
    memory = Memory(store, Extractor(model), Reconciler(store))  # type: ignore[arg-type]
    assert memory.ensure_core_profile(material(), ("user.bilibili_uid",))["status"] == "ready"
    assert "invalid_core_scope" in model.requests[1].messages[-1]["content"]
    assert (
        ExtractionContextBuilder(store)
        .build(replace(material(), extraction_scope="core"))
        .semantic_facts
        == ()
    )
    memory.close()
    store.close()


def test_cancel_and_zero_budget_do_not_commit_or_enqueue_stale_identity() -> None:
    store = SqliteMemoryStore(":memory:")
    model = Model(proposal(update()))
    memory = Memory(store, Extractor(model), Reconciler(store))  # type: ignore[arg-type]

    class Cancelled(Exception):
        pass

    def cancelled() -> None:
        raise Cancelled()

    with pytest.raises(Cancelled):
        memory.ensure_core_profile(material(), ("user.bilibili_uid",), cancel_check=cancelled)
    assert not store.pending_extractions()
    receipt = memory.ensure_core_profile(material(), ("user.bilibili_uid",), timeout_seconds=0)
    assert receipt["status"] == "unknown" and not model.requests
    assert store.core_profile(owner_user_id="local", persona_id="p").is_empty()
    memory.close()
    store.close()


def test_deleted_source_cannot_be_recreated_by_same_core_receipt() -> None:
    store = SqliteMemoryStore(":memory:")
    model = Model(proposal(update()))
    memory = Memory(store, Extractor(model), Reconciler(store))  # type: ignore[arg-type]
    assert memory.ensure_core_profile(material(), ("user.bilibili_uid",))["status"] == "ready"
    memory.invalidate_sources(session_id="s", event_uuids={"0"}, reason="source_deleted")
    assert memory.ensure_core_profile(material(), ("user.bilibili_uid",))["status"] == "missing"
    assert len(model.requests) == 1
    memory.close()
    store.close()
