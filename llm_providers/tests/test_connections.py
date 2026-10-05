from dataclasses import replace
from uuid import UUID

import pytest
from llm_providers.catalog import ModelRuntimeConfig
from llm_providers.connections import (
    ConnectionDefinition,
    CustomModel,
    ModelDirectory,
    model_reference,
)
from llm_providers.errors import LLMModelUnavailableError


def connection(number: int = 1, protocol: str = "responses") -> ConnectionDefinition:
    return ConnectionDefinition(
        id=str(UUID(int=number)),
        name=f"Connection {number}",
        base_url=f"https://service-{number}.example/v1",
        protocol=protocol,  # type: ignore[arg-type]
        models=(CustomModel(str(UUID(int=10)), "same/model", 8192),),
    )


def test_same_named_models_have_independent_routes_and_credentials() -> None:
    first, second = connection(), connection(2, "chat_completions")
    directory = ModelDirectory([first, second])
    refs = [model_reference(item.id, item.models[0].id) for item in (first, second)]
    specs = [
        directory.build_spec(directory.default_config(ref), api_key=f"key-{i}")
        for i, ref in enumerate(refs)
    ]
    assert refs[0] != refs[1]
    assert specs[0].model == specs[1].model == "openai/same/model"
    assert specs[0].api_base == first.base_url
    assert specs[1].api_base == second.base_url
    assert specs[0].api_key != specs[1].api_key
    assert directory.credential_name(refs[0]) != directory.credential_name(refs[1])
    assert specs[0].responses and specs[0].responses.model == "same/model"
    assert specs[1].responses is None


def test_directory_replacement_does_not_mutate_previous_snapshot() -> None:
    original = connection()
    before = ModelDirectory([original])
    after = ModelDirectory([replace(original, base_url="https://new.example/v1")])
    ref = model_reference(original.id, original.models[0].id)
    assert before.model(ref).api_base == original.base_url
    assert after.model(ref).api_base == "https://new.example/v1"
    with pytest.raises(TypeError):
        before.models[ref] = after.model(ref)  # type: ignore[index]
    with pytest.raises(LLMModelUnavailableError):
        ModelDirectory().model(ref)


def test_removed_builtin_name_does_not_rewrite_custom_connection() -> None:
    original = connection()
    original = replace(original, models=(replace(original.models[0], name="deepseek-v4-flash"),))
    directory = ModelDirectory([original])
    ref = model_reference(original.id, original.models[0].id)
    spec = directory.build_spec(directory.default_config(ref), api_key="fixture")
    assert spec.model == "openai/deepseek-v4-flash"
    assert spec.api_base == original.base_url
    assert spec.responses and spec.responses.model == "deepseek-v4-flash"


def test_custom_does_not_inherit_capabilities_from_remote_name() -> None:
    from llm_providers.kimi_transport import uses_kimi_stream_transport

    original = connection()
    original = replace(original, models=(replace(original.models[0], name="kimi-k2.5"),))
    directory = ModelDirectory([original])
    ref = model_reference(original.id, original.models[0].id)
    assert directory.model(ref).capabilities.multimodal_tool_results
    assert not uses_kimi_stream_transport(
        directory.build_spec(directory.default_config(ref), api_key="fixture")
    )
    with pytest.raises(ValueError):
        directory.validate(ModelRuntimeConfig(ref, "high"))
    assert directory.default_config("kimi/kimi-k2.5").reasoning_preset == "on"


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/model",
        "https://key:secret@example.com/v1",
        "https://example.com?key=secret",
        "https://example.com/#fragment",
        "https://example.com/v1/responses",
        "https://example.com:99999/v1",
    ],
)
def test_rejects_invalid_base_url(url: str) -> None:
    with pytest.raises(ValueError):
        replace(connection(), base_url=url)


@pytest.mark.parametrize("window", [0, -1, True])
def test_requires_known_context_window(window: int) -> None:
    with pytest.raises(ValueError):
        replace(connection().models[0], context_window=window)


def test_custom_default_does_not_discard_provider_continuation_state() -> None:
    from llm_providers.reasoning import (
        build_provider_reasoning_state,
        render_provider_reasoning_state,
    )

    item = connection()
    directory = ModelDirectory([item])
    config = directory.default_config(model_reference(item.id, item.models[0].id))
    spec = directory.build_spec(config, api_key="fixture")
    details = [{"type": "reasoning", "id": "r1", "encrypted_content": "opaque-fixture"}]
    state = build_provider_reasoning_state(spec, reasoning_content="", reasoning_details=details)
    assert state is not None
    replay = render_provider_reasoning_state(
        {"role": "assistant", "content": "working"},
        state,
        config=config,
        state_run_id="old",
        active_run_id="new",
    )
    assert replay["reasoning_details"] == details


def test_discovery_keeps_server_context_and_marks_catalog_only_as_reference() -> None:
    from llm_providers.connections.metadata import discovered_models, model_metadata

    models = discovered_models(
        {
            "data": [
                {"id": "private-model", "context_length": 32000, "max_tokens": 256},
                {"id": "output-only", "max_tokens": 4096},
                {"id": "invalid-window", "context_length": True},
                {"id": "private-model", "limits": {"context": 64000}},
            ]
        }
    )
    assert len(models) == 3
    assert models[0]["contextWindow"] == 64000
    assert models[0]["contextSource"] == "endpoint"
    assert models[1]["contextWindow"] is None
    assert models[2]["contextWindow"] is None
    reference = model_metadata("kimi-k3")
    assert reference["contextWindow"] > 0 and reference["contextSource"] == "catalog"
    assert model_metadata("kimi-k3", {"context_length": 8192})["contextWindow"] == 8192


@pytest.mark.parametrize(
    "preset,effort",
    [("none", None), ("effort:none", "none"), ("effort:vendor-next", "vendor-next")],
)
def test_custom_effort_is_not_limited_to_catalog(preset: str, effort: str | None) -> None:
    from llm_providers.catalog import validate_model_runtime_config

    item = connection()
    directory = ModelDirectory([item])
    config = ModelRuntimeConfig(model_reference(item.id, item.models[0].id), preset)
    assert validate_model_runtime_config(config) == config
    spec = directory.build_spec(config, api_key="fixture")
    assert spec.defaults.get("reasoning_effort") == effort
    assert ("reasoning_effort" in spec.defaults) == (effort is not None)


@pytest.mark.parametrize(
    "preset", ["effort:", "effort: high", "effort:bad\nvalue", "high", "effort:" + "x" * 129]
)
def test_custom_effort_rejects_malformed_selection(preset: str) -> None:
    from llm_providers.custom_reasoning import custom_effort

    with pytest.raises(ValueError):
        custom_effort(preset)


def test_custom_protocol_image_transport_needs_no_manual_declarations() -> None:
    item = connection()
    directory = ModelDirectory([item])
    ref = model_reference(item.id, item.models[0].id)
    spec = directory.build_spec(directory.default_config(ref), api_key="fixture")
    assert spec.attachment_capabilities is not None
    assert spec.attachment_capabilities.multimodal_tool_results
    assert "application/pdf" not in spec.attachment_capabilities.accepted_media_types


def test_legacy_capability_fields_are_ignored() -> None:
    from llm_providers.connections.codec import decode_connections

    item = connection()
    raw = {
        "revision": 1,
        "connections": [
            {
                "id": item.id,
                "name": item.name,
                "baseUrl": item.base_url,
                "protocol": item.protocol,
                "credentialRevision": item.id,
                "models": [
                    {
                        "id": item.models[0].id,
                        "name": "fixture",
                        "contextWindow": 8192,
                        "images": False,
                        "documents": True,
                    }
                ],
            }
        ],
    }
    decoded = decode_connections(raw)
    assert decoded[0].models[0] == CustomModel(item.models[0].id, "fixture", 8192)
