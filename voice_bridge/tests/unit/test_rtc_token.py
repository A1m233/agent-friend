"""``rtc/token.py`` 单元测试。

主要验证：

- token 序列化格式：``"001" + app_id + base64(...)``
- 同一 (app_id, app_key, room, user, ts) 输入 → 相同 token（确定性）
- 不同 nonce 产生不同 token（防同 user 复用）
- HMAC 签名是消息体的一部分
"""

from __future__ import annotations

import base64
import struct
from typing import Any

from voice_bridge.rtc.token import VERSION, RoomTokenSigner


def _make(**overrides: Any) -> RoomTokenSigner:
    base: dict[str, Any] = dict(
        app_id="0123456789abcdef01234567",  # 24 chars
        app_key="test-app-key",
        room_id="room-1",
        user_id="user-1",
        issued_at=1735000000,
        nonce=42,
        expire_at=1735086400,
    )
    base.update(overrides)
    signer = RoomTokenSigner(**base)
    expire_at: int = base["expire_at"]
    signer.add_publish_privilege(expire_at)
    signer.add_subscribe_privilege(expire_at)
    return signer


class TestRoomTokenSigner:
    def test_token_starts_with_version_and_app_id(self) -> None:
        token = _make().serialize()
        assert token.startswith(VERSION)
        # app_id 紧跟在 VERSION 后
        assert token[3:27] == "0123456789abcdef01234567"

    def test_remainder_is_valid_base64(self) -> None:
        token = _make().serialize()
        rest = token[3 + 24 :]
        # 应该能解出有效字节流
        decoded = base64.b64decode(rest)
        assert len(decoded) > 0

    def test_deterministic_same_inputs(self) -> None:
        a = _make().serialize()
        b = _make().serialize()
        assert a == b

    def test_different_nonce_different_token(self) -> None:
        a = _make(nonce=42).serialize()
        b = _make(nonce=43).serialize()
        assert a != b

    def test_different_room_different_token(self) -> None:
        a = _make(room_id="r1").serialize()
        b = _make(room_id="r2").serialize()
        assert a != b

    def test_different_app_key_different_signature(self) -> None:
        a = _make(app_key="key-a").serialize()
        b = _make(app_key="key-b").serialize()
        assert a != b

    def test_message_carries_room_user_in_payload(self) -> None:
        """解码 token 检查 room_id / user_id 是否被打入二进制消息体。"""
        token = _make(room_id="ROOM-X", user_id="USER-Y").serialize()
        rest = token[3 + 24 :]
        decoded = base64.b64decode(rest)
        # 二进制消息体在前（uint16 长度前缀），签名在后
        msg_len = struct.unpack("<H", decoded[:2])[0]
        msg = decoded[2 : 2 + msg_len]
        assert b"ROOM-X" in msg
        assert b"USER-Y" in msg
