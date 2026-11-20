"""One route-level gate shared by tool execution and native image projection."""

from __future__ import annotations

from dataclasses import dataclass

from ..catalog import ModelRouteCapabilities, model_capabilities
from ..protocol import LanguageModel
from ..spec import ProviderSpec


@dataclass
class ToolImageSupport:
    """Optional image transport state, shared by a bound model and its guards.

    Wrappers may forward this object without exposing an unguarded model call.
    Rejections belong to this binding, not to the wrapper or a global model ID.
    """

    rejected: bool = False


def tool_image_spec(client: LanguageModel) -> ProviderSpec | None:
    """Read the explicit optional contract; a spec alone does not opt a model in."""
    support = getattr(client, "tool_image_support", None)
    spec = getattr(client, "spec", None)
    if not isinstance(support, ToolImageSupport) or support.rejected:
        return None
    return spec if isinstance(spec, ProviderSpec) else None


def tool_image_capabilities(spec: ProviderSpec) -> ModelRouteCapabilities:
    return spec.attachment_capabilities or model_capabilities(spec.product_model_id or spec.model)


def supports_tool_images(client: LanguageModel) -> bool:
    spec = tool_image_spec(client)
    if spec is None:
        return False
    route = spec.responses
    capabilities = tool_image_capabilities(spec)
    return (
        capabilities.multimodal_tool_results
        and "image" in capabilities.input_modalities
        and (route is None or route.parameter_profile in {"openai", "deepseek"})
    )


def record_image_rejection(client: LanguageModel, error: Exception) -> bool:
    """Only explicit modality rejections may disable optional image transport."""
    if not supports_tool_images(client):
        return False
    from ..errors import LLMImageInputUnsupportedError

    if not isinstance(error, LLMImageInputUnsupportedError):
        return False
    support = getattr(client, "tool_image_support", None)
    if not isinstance(support, ToolImageSupport):
        return False
    support.rejected = True
    return True
