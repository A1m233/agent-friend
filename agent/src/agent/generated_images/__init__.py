"""Persist provider image results without exposing raw bodies to conversation history."""

from .service import save_generated_image

__all__ = ["save_generated_image"]
