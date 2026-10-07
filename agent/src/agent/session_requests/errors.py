"""Errors shared by request producers without depending on a trigger type."""

from dataclasses import dataclass


@dataclass
class SessionRequestError(Exception):
    kind: str
    message: str

    def __str__(self) -> str:
        return self.message

    @property
    def reason_code(self) -> str:
        return self.kind
