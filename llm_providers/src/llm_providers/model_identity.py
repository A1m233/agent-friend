"""Structural identity validation, independent of current model availability."""

from uuid import UUID


def is_custom_model_reference(value: str) -> bool:
    parts = value.split("/")
    if len(parts) != 3 or parts[0] != "custom":
        return False
    try:
        return all(str(UUID(part)) == part for part in parts[1:])
    except ValueError:
        return False
