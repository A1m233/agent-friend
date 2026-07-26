"""IM 通道 smoke 测试(022 design §5.2)。

**非破坏性本机 e2e**,验证 IM 通道核心装配 + 路由 + 落盘端到端走通:

1. 用临时 ``AGENT_FRIEND_DATA_DIR`` 启动 :class:`BridgeRuntime`
2. monkey-patch :meth:`SessionBridge.bind_persistent` 让它返回一个 fake
   :class:`Conversation`(stream 出 ``TextDelta + TurnDone``),**避开真 LLM 调用**
3. 灌一条假 ``InboundEvent`` 到 :meth:`IMRouter.handle_inbound`,由 fake Provider
   暴露原生流式会话
4. 断言:
   - 流式会话收到中间快照和完整 DONE 文本
   - 普通 send 只收到独立菜单控制消息,不重复发送正文

**不接真 QQ gateway,不发真消息,不动用户数据**。

详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §5.2。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch


class _FakeStream:
    def __init__(self) -> None:
        self.updates: list[str] = []
        self.finishes: list[str] = []
        self.aborts: list[tuple[str, str]] = []

    @property
    def has_started(self) -> bool:
        return bool(self.updates)

    @property
    def is_completed(self) -> bool:
        return bool(self.finishes)

    @property
    def is_failed(self) -> bool:
        return False

    async def update(self, full_text: str) -> None:
        self.updates.append(full_text)

    async def finish(self, full_text: str) -> None:
        self.finishes.append(full_text)

    async def abort(self, full_text: str, reason: str) -> None:
        self.aborts.append((full_text, reason))


class _FakeProvider:
    def __init__(self) -> None:
        self.stream = _FakeStream()
        self.sent: list[Any] = []

    async def send(self, content: Any) -> None:
        self.sent.append(content)

    def open_text_stream(self, _context: Any) -> _FakeStream:
        return self.stream


def _build_fake_conv(reply: str) -> Any:
    """构造一个最小 Conversation:stream(user_input) → TextDelta + TurnDone。"""
    from unittest.mock import MagicMock

    from agent import TextDelta, TurnDone

    conv = MagicMock()
    conv.stream.return_value = iter([TextDelta(text=reply), TurnDone()])
    return conv


async def _run() -> int:
    tmp_root = Path(tempfile.mkdtemp(prefix="agent-friend-im-smoke-"))
    os.environ["AGENT_FRIEND_DATA_DIR"] = str(tmp_root)
    os.environ["AGENT_BRIDGE_MEMORY_ENABLED"] = "false"
    # 让 ProviderSpec.from_env 不撞缺 key 错误(smoke 不调真 LLM)
    os.environ.setdefault("DEEPSEEK_API_KEY", "smoke-fake-key")

    print(f"[smoke] tmp data dir: {tmp_root}")

    from agent_bridge.assembly import build_runtime
    from agent_bridge.protocols.im import IMRouter
    from agent_bridge.protocols.im.event import InboundEvent
    from agent_bridge.session_bridge import SessionBridge

    runtime = build_runtime.__wrapped__ if hasattr(build_runtime, "__wrapped__") else build_runtime
    from agent_bridge.settings import BridgeSettings

    bridge_runtime = runtime(BridgeSettings())
    assert bridge_runtime.im_runtime is not None, "IM 装配失败"
    assert bridge_runtime.im_onboard_registry is not None, "Onboard 注册表未装配"

    print(f"[smoke] BridgeRuntime 装配成功 · im_runtime={type(bridge_runtime.im_runtime).__name__}")
    print(f"[smoke] 初始 list_status = {bridge_runtime.im_runtime.list_status()}")

    # 直接构造一个 router 用 fake bind_persistent
    fake_reply = "你好呀 · 我记下啦"
    session_bridge = SessionBridge(bridge_runtime)
    router = IMRouter(
        session_bridge=session_bridge,
        default_persona=bridge_runtime.default_persona,
        default_model=bridge_runtime.default_model,
    )

    provider = _FakeProvider()

    inbound = InboundEvent(
        event_type="C2C_MESSAGE_CREATE",
        chat_id="SMOKE-USER-OPENID",
        user_id="SMOKE-USER-OPENID",
        chat_scope="c2c",
        content="你好 · smoke",
        message_id="SMOKE-MSG-001",
        timestamp="2026-06-18T00:00:00Z",
        message_type=0,
    )

    with patch.object(session_bridge, "bind_persistent", return_value=_build_fake_conv(fake_reply)):
        await router.handle_inbound("fake", inbound, provider)

    # ---- 断言 ----
    if not provider.stream.updates:
        print("[smoke] FAIL · stream update 未被调用", file=sys.stderr)
        return 1
    if provider.stream.finishes != [fake_reply]:
        print(
            f"[smoke] FAIL · stream final 不匹配:{provider.stream.finishes!r}",
            file=sys.stderr,
        )
        return 1
    if len(provider.sent) != 1 or provider.sent[0].text != "操作":
        print(f"[smoke] FAIL · 独立菜单消息不匹配:{provider.sent!r}", file=sys.stderr)
        return 1
    menu = provider.sent[0]
    if menu.controls is None:
        print("[smoke] FAIL · 菜单 controls 缺失", file=sys.stderr)
        return 1
    if any(item.text == fake_reply for item in provider.sent):
        print("[smoke] FAIL · 完整正文被普通 send 重复发送", file=sys.stderr)
        return 1

    print(f"[smoke] PASS · stream updates = {provider.stream.updates!r}")
    print(f"[smoke] PASS · stream final = {provider.stream.finishes!r}")
    print(
        "[smoke] PASS · menu outbound "
        f"text_len={len(menu.text)} controls={menu.controls is not None}"
    )
    print(f"[smoke] PASS · IMRuntime 状态 = {bridge_runtime.im_runtime.list_status()}")
    print("[smoke] PASS · 全部断言通过")

    bridge_runtime.close()
    return 0


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
