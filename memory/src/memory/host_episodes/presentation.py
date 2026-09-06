"""Local display times for identified host receipts, without rewriting their quotes."""

from __future__ import annotations

import json
import re
from datetime import datetime

from .identity import is_notification_episode_source

_SUMMARY = re.compile(
    r"^(?P<timestamp>"
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})"
    r"|[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} "
    r"[+-][0-9]{2}:[0-9]{2}"
    r")(?P<body>，我通过桌宠气泡就「.*?」发出提醒，实际显示："
    r'(?P<quote>"(?:[^"\\]|\\.)*")。(?:内容在显示时被截断。)?)$',
    re.DOTALL,
)


def _local_time(value: datetime) -> datetime:
    return value.astimezone()


def format_notification_time(value: datetime) -> str:
    """Use the system local offset at this instant, with second precision."""
    if value.utcoffset() is None:
        raise ValueError("notification time must include a timezone")
    local = _local_time(value)
    offset = local.strftime("%z")
    return f"{local:%Y-%m-%d %H:%M:%S} {offset[:3]}:{offset[3:5]}"


def project_notification_summary(
    summary: str, *, episode_id: str, source_ref: str, occurred_at: datetime
) -> str:
    """Localize only an exact host receipt template backed by matching identity."""
    if not is_notification_episode_source(episode_id, source_ref):
        return summary
    match = _SUMMARY.fullmatch(summary)
    if match is None:
        return summary
    try:
        timestamp = datetime.fromisoformat(match["timestamp"])
        json.loads(match["quote"])
    except ValueError:
        return summary
    if timestamp.utcoffset() is None:
        return summary
    return format_notification_time(occurred_at) + match["body"]
