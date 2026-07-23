"""Locally managed canonical message attachments."""

from .models import AttachmentKind, AttachmentRef
from .store import AttachmentError, AttachmentStore

__all__ = ["AttachmentError", "AttachmentKind", "AttachmentRef", "AttachmentStore"]
