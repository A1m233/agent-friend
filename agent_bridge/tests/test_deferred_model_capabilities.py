from types import SimpleNamespace

from agent_bridge.runtime_config import DeferredLanguageModel

from llm_providers import LanguageModelCapabilities, get_model_capabilities


def test_deferred_capabilities_forward_without_generating() -> None:
    calls: list[str] = []
    model = SimpleNamespace(
        capabilities=LanguageModelCapabilities(assistant_message_phase="native")
    )

    def factory() -> SimpleNamespace:
        calls.append("assemble")
        return model

    deferred = DeferredLanguageModel(factory)
    assert calls == []
    assert get_model_capabilities(deferred).assistant_message_phase == "native"
    assert get_model_capabilities(deferred).assistant_message_phase == "native"
    assert calls == ["assemble"]


def test_deferred_legacy_model_has_unavailable_capabilities() -> None:
    assert (
        get_model_capabilities(DeferredLanguageModel(lambda: SimpleNamespace()))
        == LanguageModelCapabilities()
    )
