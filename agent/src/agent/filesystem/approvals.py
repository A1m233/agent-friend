"""Compatibility import for the shared approval broker."""

from ..approvals import ApprovalBroker

FilesystemApprovalBroker = ApprovalBroker

__all__ = ["FilesystemApprovalBroker"]
