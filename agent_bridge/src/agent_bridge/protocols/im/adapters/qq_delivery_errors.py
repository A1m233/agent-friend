"""Conservative interpretation of the pinned QQ SDK's public error boundary."""

from __future__ import annotations

import re

from ..delivery.models import DeliveryFailure

_HTTP_ERROR = re.compile(r"^QQ Bot API error \[(\d{3})\] /[^\s:]+: (.*)$", re.DOTALL)
_IMMUTABLE_MESSAGE = "已经提交的消息内容不可修改"
_ENDED_MESSAGE = "流式消息已经结束，不能继续发送"


def classify_qq_failure(value: object, *, stream: bool = False) -> DeliveryFailure:
    if isinstance(value, dict):
        if stream and value.get("message") == _IMMUTABLE_MESSAGE and value.get("code"):
            return DeliveryFailure("failed", "stream_immutable", True)
        if value.get("code"):
            return DeliveryFailure("failed", "platform_rejected", False)
    if isinstance(value, Exception):
        match = _HTTP_ERROR.fullmatch(str(value))
        if match:
            status = int(match[1])
            if stream and status == 404 and match[2].strip() == _IMMUTABLE_MESSAGE:
                return DeliveryFailure("failed", "stream_immutable", True)
            if stream and status == 400 and match[2].strip() == _ENDED_MESSAGE:
                return DeliveryFailure("failed", "stream_ended", True)
            if 400 <= status < 500:
                return DeliveryFailure("failed", "http_rejected", False)
    # Timeouts, server errors, missing receipts and unfamiliar exception formats
    # may have accepted the payload. Never advance the confirmed prefix.
    return DeliveryFailure("unknown", "unconfirmed_receipt", True)
