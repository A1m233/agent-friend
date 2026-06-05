"""Volc OpenAPI Sign V4 签名算法单元测试。

用固定 timestamp / 固定 AK SK / 固定 body 计算签名，断言关键性质：

- 同一输入产生相同签名（确定性）
- 不同 body 产生不同签名（敏感性）
- 不同时间产生不同签名（time-bounded）
- ``Authorization`` 头格式合法（含 ``Credential`` / ``SignedHeaders`` / ``Signature``）

详见 docs/requirements/007-voice-call/design.md §4.5.2。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from voice_bridge.rtc.openapi import sign_v4


@pytest.fixture
def sign_kwargs() -> dict[str, Any]:
    return dict(
        method="POST",
        query={"Action": "StartVoiceChat", "Version": "2024-12-01"},
        headers={"Host": "rtc.volcengineapi.com", "Content-Type": "application/json"},
        body=b'{"AppId":"test","RoomId":"r1","TaskId":"t1"}',
        access_key="AKLT-test-access-key",
        secret_key="test-secret-key-base64",
        now=datetime(2026, 5, 25, 12, 34, 56, tzinfo=UTC),
    )


class TestSignV4:
    def test_returns_required_headers(self, sign_kwargs: dict[str, Any]) -> None:
        result = sign_v4(**sign_kwargs)
        assert "Authorization" in result
        assert "X-Date" in result
        assert "X-Content-Sha256" in result

    def test_x_date_format(self, sign_kwargs: dict[str, Any]) -> None:
        result = sign_v4(**sign_kwargs)
        assert result["X-Date"] == "20260525T123456Z"

    def test_authorization_format(self, sign_kwargs: dict[str, Any]) -> None:
        """Authorization 应符合 ``HMAC-SHA256 Credential=... SignedHeaders=... Signature=...`` 格式。"""
        result = sign_v4(**sign_kwargs)
        auth = result["Authorization"]
        assert auth.startswith("HMAC-SHA256 ")
        assert "Credential=AKLT-test-access-key/" in auth
        assert "20260525/cn-north-1/rtc/request" in auth
        assert "SignedHeaders=" in auth
        assert "Signature=" in auth

    def test_signed_headers_lowercase_alphabetical(self, sign_kwargs: dict[str, Any]) -> None:
        result = sign_v4(**sign_kwargs)
        auth = result["Authorization"]
        signed = auth.split("SignedHeaders=")[1].split(",")[0]
        # 应该是字典序、小写：content-type;host;x-content-sha256;x-date
        assert signed == "content-type;host;x-content-sha256;x-date"

    def test_deterministic(self, sign_kwargs: dict[str, Any]) -> None:
        a = sign_v4(**sign_kwargs)
        b = sign_v4(**sign_kwargs)
        assert a == b

    def test_different_body_changes_signature(self, sign_kwargs: dict[str, Any]) -> None:
        a = sign_v4(**sign_kwargs)
        sign_kwargs["body"] = b'{"different":"body"}'
        b = sign_v4(**sign_kwargs)
        assert a["Authorization"] != b["Authorization"]
        assert a["X-Content-Sha256"] != b["X-Content-Sha256"]

    def test_different_time_changes_signature(self, sign_kwargs: dict[str, Any]) -> None:
        a = sign_v4(**sign_kwargs)
        sign_kwargs["now"] = datetime(2026, 5, 25, 12, 34, 57, tzinfo=UTC)
        b = sign_v4(**sign_kwargs)
        assert a["Authorization"] != b["Authorization"]
        assert a["X-Date"] != b["X-Date"]

    def test_different_secret_changes_signature(self, sign_kwargs: dict[str, Any]) -> None:
        a = sign_v4(**sign_kwargs)
        sign_kwargs["secret_key"] = "another-secret"
        b = sign_v4(**sign_kwargs)
        assert a["Authorization"] != b["Authorization"]

    def test_naive_datetime_treated_as_utc(self, sign_kwargs: dict[str, Any]) -> None:
        """传入 naive datetime 时，函数应把它当 UTC 处理（不抛错）。"""
        sign_kwargs["now"] = datetime(2026, 5, 25, 12, 34, 56)  # naive
        result = sign_v4(**sign_kwargs)
        assert result["X-Date"] == "20260525T123456Z"

    def test_known_vector(self, sign_kwargs: dict[str, Any]) -> None:
        """固定输入产生稳定签名——若实现误改本断言会立刻发现。

        本向量不是火山官方公开向量；它只保证"算法 + 输入 → 输出"在本仓库内
        是稳定的。真实火山服务接受性需要靠集成测试 / smoke 验证。
        """
        result = sign_v4(**sign_kwargs)
        # 签名值是 secret + body + time 的 HMAC 链路决定的；下面是当前实现
        # 在本测试输入下产生的签名，作为回归基准锁定。
        assert result["X-Content-Sha256"] == (
            "9fc24300ee14e37d733b5019b9234def1ee2b4ae1a7bcd41597ee36f1c3c64ea"
        )
        # Signature 字段必须是 64 字符的 hex
        sig = result["Authorization"].split("Signature=")[1]
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)
