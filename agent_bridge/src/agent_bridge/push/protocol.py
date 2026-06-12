"""014 · bridge push 通道协议：SSE envelope 序列化。

复用 :class:`agent.runtime.listeners.PushEnvelope` 作为 wire schema，本模块
提供 envelope → SSE bytes 的编码。

详见 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §8.2。
"""

from __future__ import annotations

import json
from dataclasses import asdict

from agent.runtime import PushEnvelope


def encode_envelope_sse(env: PushEnvelope) -> bytes:
    """把 :class:`PushEnvelope` 序列化为一个完整的 SSE event chunk。

    输出形如：

    ``event: push\\ndata: {...json...}\\n\\n``

    客户端按 SSE 协议解析时，每收到一个空行（``\\n\\n``）就触发一次
    ``message`` 事件。``event: push`` 名让消费方区分不同事件类别（未来若
    新增 ``event: error`` 等可加分支）。
    """
    payload = json.dumps(asdict(env), ensure_ascii=False)
    return f"event: push\ndata: {payload}\n\n".encode()
