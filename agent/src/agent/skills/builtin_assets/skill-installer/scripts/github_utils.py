#!/usr/bin/env python3
# mypy: ignore-errors
"""Shared GitHub helpers for skill install scripts."""

from __future__ import annotations

import http.client
import os
import urllib.error
import urllib.request


class GitHubTransportError(Exception):
    """An incomplete HTTP exchange, distinct from an HTTP status or local I/O error."""

    def __init__(self, phase: str, cause: Exception) -> None:
        self.phase = phase
        self.error_type = type(cause).__name__
        super().__init__(f"GitHub request {phase} failed ({self.error_type}): {cause}")


def github_request(url: str, user_agent: str) -> bytes:
    headers = {"User-Agent": user_agent}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    phase = "open"
    try:
        with urllib.request.urlopen(req) as resp:
            phase = "read"
            return resp.read()
    except urllib.error.HTTPError:
        # Callers retain status-specific behavior, including listing-path 404s.
        raise
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        # Only normalize errors inside the network boundary. Never return partial
        # response bytes or turn archive/filesystem failures into transport retries.
        raise GitHubTransportError(phase, exc) from exc


def github_api_contents_url(repo: str, path: str, ref: str) -> str:
    return f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
