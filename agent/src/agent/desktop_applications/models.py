"""Application approval uses the shared broker and command risk input, not process ownership."""

from __future__ import annotations

from dataclasses import dataclass

from ..command_runtime.models import PendingCommandApproval


@dataclass
class PendingApplicationApproval(PendingCommandApproval):
    kind: str = "desktop_application"

    def to_dict(self) -> dict[str, object]:
        value = super().to_dict()
        value["details"] = {
            "executable_path": self.details.argv[0],
            "arguments": list(self.details.argv[1:]),
            "working_directory": self.details.workdir,
            "lifecycle": "user_desktop",
            "sandboxed": False,
        }
        return value
