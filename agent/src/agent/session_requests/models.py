"""Input options independent of scheduling or any transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from llm_providers import ModelRuntimeConfig, default_model_runtime_config

from ..approvals.models import SessionApprovalMode
from ..attachments.models import AttachmentRef


@dataclass(frozen=True)
class RequestOptions:
    attachments: tuple[AttachmentRef, ...] = ()
    model: ModelRuntimeConfig | None = None
    approval_mode: SessionApprovalMode | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attachments": [ref.to_dict() for ref in self.attachments],
            "model": (
                {"model_id": self.model.model_id, "reasoning_preset": self.model.reasoning_preset}
                if self.model
                else None
            ),
            "approval_mode": self.approval_mode,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RequestOptions:
        if set(value) - {"attachments", "model", "approval_mode"}:
            raise ValueError("unknown request option")
        mode = value.get("approval_mode")
        if mode not in (None, "per_request", "session_auto"):
            raise ValueError("invalid approval_mode")
        raw_model = value.get("model")
        model = None
        if raw_model is not None:
            if not isinstance(raw_model, dict) or set(raw_model) - {"model_id", "reasoning_preset"}:
                raise ValueError("invalid model override")
            model_id = raw_model.get("model_id")
            preset = raw_model.get("reasoning_preset")
            if not isinstance(model_id, str) or not model_id.strip():
                raise ValueError("model_id must not be empty")
            if preset is not None and not isinstance(preset, str):
                raise ValueError("invalid reasoning_preset")
            if preset is not None:
                model = ModelRuntimeConfig(model_id, preset)
            else:
                try:
                    model = default_model_runtime_config(model_id)
                except ValueError:
                    model = ModelRuntimeConfig(model_id, "none")

        attachments = value.get("attachments", [])
        if not isinstance(attachments, list) or any(
            not isinstance(ref, dict) for ref in attachments
        ):
            raise ValueError("attachments must be an array of asset references")
        return cls(
            attachments=tuple(AttachmentRef.from_dict(ref) for ref in attachments),
            model=model,
            approval_mode=cast(SessionApprovalMode | None, mode),
        )
