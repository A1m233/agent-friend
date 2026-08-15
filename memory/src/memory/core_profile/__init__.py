"""Structured, versioned core profile contracts and rendering."""

from .contracts import (
    CORE_FIELD_SPECS,
    CoreFieldKey,
    CoreFieldSpec,
    CoreProfile,
    CoreProfileEntry,
    CoreProfileError,
    CoreScopeKind,
    build_core_profile,
    normalize_core_value,
    validate_core_value,
)
from .renderer import CoreProfileRenderer

__all__ = [
    "CORE_FIELD_SPECS",
    "CoreFieldKey",
    "CoreFieldSpec",
    "CoreProfile",
    "CoreProfileEntry",
    "CoreProfileError",
    "CoreProfileRenderer",
    "CoreScopeKind",
    "build_core_profile",
    "normalize_core_value",
    "validate_core_value",
]
