"""火山 RTC roomToken 签发。

把 spike Node 实现（``rtc-aigc-demo/Server/token.js``）翻译成 Python：

- 二进制布局完全一致：固定字节序 little-endian、自定义 ByteBuf
- HMAC-SHA256 签名 ``appKey`` 的"消息体"，附加到 token 末尾做完整性校验
- 输出格式：``"001" + appID(24 字节) + base64(msg + sig)``

详见 docs/requirements/007-voice-call/design.md §4.6 + spike token.js。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
from dataclasses import dataclass, field

VERSION = "001"
"""火山 RTC token 协议版本前缀。"""

PRIV_PUBLISH_STREAM = 0
PRIV_PUBLISH_AUDIO = 1
PRIV_PUBLISH_VIDEO = 2
PRIV_PUBLISH_DATA = 3
PRIV_SUBSCRIBE_STREAM = 4

_PUBLISH_GROUP = (PRIV_PUBLISH_STREAM, PRIV_PUBLISH_AUDIO, PRIV_PUBLISH_VIDEO, PRIV_PUBLISH_DATA)


class _ByteBuf:
    """little-endian 字节缓冲；语义对齐 spike token.js 的 ``ByteBuf``。"""

    def __init__(self) -> None:
        self._buf = bytearray()

    def put_uint16(self, v: int) -> None:
        self._buf.extend(struct.pack("<H", v))

    def put_uint32(self, v: int) -> None:
        self._buf.extend(struct.pack("<I", v))

    def put_bytes(self, data: bytes) -> None:
        self.put_uint16(len(data))
        self._buf.extend(data)

    def put_string(self, s: str) -> None:
        self.put_bytes(s.encode("utf-8"))

    def put_tree_map_uint32(self, m: dict[int, int]) -> None:
        self.put_uint16(len(m))
        for k in sorted(m.keys()):
            self.put_uint16(k)
            self.put_uint32(m[k])

    def to_bytes(self) -> bytes:
        return bytes(self._buf)


@dataclass
class RoomTokenSigner:
    """单次 RTC roomToken 签发。

    用法：

    >>> signer = RoomTokenSigner(app_id, app_key, room_id, user_id)
    >>> signer.add_publish_privilege(expire_ts=int(time.time()) + 86400)
    >>> signer.add_subscribe_privilege(expire_ts=int(time.time()) + 86400)
    >>> signer.expire_at = int(time.time()) + 86400
    >>> token = signer.serialize()

    Attributes:
        app_id: RTC 应用 ID（24 字节字符串）。
        app_key: RTC 应用密钥（用于 HMAC-SHA256 签名）。
        room_id: RTC 房间 ID。
        user_id: 用户 ID。
        issued_at: 签发时间（unix epoch 秒），默认为 0；调用方可覆盖以便测试。
        nonce: 随机数；同一房间不同用户 token 必须不同。
        expire_at: token 过期时间（0 表示不过期）。
        privileges: ``{privilege_id: expire_ts}`` 字典。
    """

    app_id: str
    app_key: str
    room_id: str
    user_id: str
    issued_at: int = 0
    nonce: int = 0
    expire_at: int = 0
    privileges: dict[int, int] = field(default_factory=dict)

    def add_publish_privilege(self, expire_ts: int) -> None:
        """加发布流权限（同时加 audio / video / data 子权限，对齐 spike）。"""
        for p in _PUBLISH_GROUP:
            self.privileges[p] = expire_ts

    def add_subscribe_privilege(self, expire_ts: int) -> None:
        """加订阅流权限。"""
        self.privileges[PRIV_SUBSCRIBE_STREAM] = expire_ts

    def _pack_msg(self) -> bytes:
        buf = _ByteBuf()
        buf.put_uint32(self.nonce)
        buf.put_uint32(self.issued_at)
        buf.put_uint32(self.expire_at)
        buf.put_string(self.room_id)
        buf.put_string(self.user_id)
        buf.put_tree_map_uint32(self.privileges)
        return buf.to_bytes()

    def serialize(self) -> str:
        """生成最终 token 字符串。

        格式：``"001" + app_id(原始) + base64(msg_bytes_with_uint16_len + sig_bytes_with_uint16_len)``。
        """
        msg = self._pack_msg()
        sig = hmac.new(self.app_key.encode("utf-8"), msg, hashlib.sha256).digest()

        content = _ByteBuf()
        content.put_bytes(msg)
        content.put_bytes(sig)
        encoded = base64.b64encode(content.to_bytes()).decode("ascii")
        return VERSION + self.app_id + encoded
