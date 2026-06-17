"""IM 凭据本地加密存储(022 起)。

设计要点(详见 design.md §3.6 + §3.7 Security Limitations):

- **AES-GCM** 加密(``cryptography`` 库)+ 256-bit 密钥
- **密钥派生** = ``SHA-256(username + ":" + hostname + ":" + APP_SALT)`` —— 同机器
  derive 出固定值,跨平台一致,**不依赖 OS keychain**(避免 macOS Keychain /
  Win Credential Manager 双端 plumbing)
- **存储路径** = ``<base_dir>/<im_type>_<sha256(bind_id)[:16]>.json.enc``;
  ``base_dir`` 默认 = ``agent.user_data_dir() / "im_credentials"``,可注入便于测试
- **换机时**:密钥不匹配 → ``AESGCM.decrypt`` 抛 ``InvalidTag`` → :meth:`list_all`
  跳过该文件(日志 warning,不抛),等价于"该凭据失效",用户重新 onboard

**已知 Security Limitations**(design §3.7 写明):

1. 同机其他进程能用同款公式 derive 出密钥
2. 不防 root / admin / 抢用户身份的攻击者
3. 不防 swap / 内存 dump

本期接受这个 trade-off:比明文落盘强(挡 git 误提交 / 简单文件分享),local-first
个人助手的真实威胁模型够用。未来 hardening = 切 OS keychain。
"""

from __future__ import annotations

import getpass
import hashlib
import json
import logging
import os
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = ["CredentialStore", "ImCredential"]

logger = logging.getLogger(__name__)

_APP_SALT = "agent-friend-im"
"""固定 salt;跟用户名 / 主机名一起 derive 密钥。换值 = 所有已存凭据失效。"""

_IV_LEN = 12
"""AES-GCM 推荐 IV 长度 = 12 bytes(96 bits)。"""

_FILE_SUFFIX = ".json.enc"


@dataclass(frozen=True)
class ImCredential:
    """单个 IM 绑定的凭据。

    Attributes:
        im_type: 平台标识(``"qq"`` 等)
        bind_id: 平台内唯一标识(QQ = ``user_openid``)
        app_id: 平台 Bot AppID
        client_secret: 平台 Bot Secret(明文持有期仅在 bridge 进程内存)
        user_openid: QQ 特有:扫码用户的 openid;其他平台可能为空
        extra: 平台 specific 扩展位(飞书 ``tenant_key`` 等);未来新增平台用,
            v1 QQ 留空。
    """

    im_type: str
    bind_id: str
    app_id: str
    client_secret: str
    user_openid: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class CredentialStore:
    """AES-GCM 加密的本地凭据存储。

    Args:
        base_dir: 加密文件落盘的目录。测试时注入 tmp 路径,生产用
            ``agent.user_data_dir() / "im_credentials"``。
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        self._key = self._derive_key()

    @staticmethod
    def _derive_key() -> bytes:
        """SHA-256(username + ":" + hostname + ":" + APP_SALT) → 256-bit key。"""
        material = f"{getpass.getuser()}:{socket.gethostname()}:{_APP_SALT}"
        return hashlib.sha256(material.encode("utf-8")).digest()

    def save(self, cred: ImCredential) -> None:
        """加密落盘一条凭据。已存在同 ``(im_type, bind_id)`` 的文件会被**覆盖**。"""
        path = self._path_for(cred.im_type, cred.bind_id)
        plain = json.dumps(asdict(cred), ensure_ascii=False).encode("utf-8")
        iv = os.urandom(_IV_LEN)
        ct = AESGCM(self._key).encrypt(iv, plain, associated_data=None)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写:先写 .tmp 再 rename,避免半写状态(同 cred 解密失败让 list_all 跳过)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_bytes(iv + ct)
        tmp_path.replace(path)

    def list_all(self) -> list[ImCredential]:
        """加载 ``base_dir`` 下所有可解密的凭据。

        损坏 / 密钥不匹配的文件**跳过**(log warning,不抛),等价于"该凭据失效"。
        """
        if not self._base_dir.exists():
            return []
        creds: list[ImCredential] = []
        for path in sorted(self._base_dir.glob(f"*{_FILE_SUFFIX}")):
            try:
                creds.append(self._load(path))
            except (InvalidTag, ValueError, json.JSONDecodeError) as e:
                logger.warning(
                    "failed to load IM credential at %s (%s: %s), skipping",
                    path,
                    type(e).__name__,
                    e,
                )
        return creds

    def delete(self, im_type: str, bind_id: str) -> None:
        """删除一条凭据。目标不存在时静默。"""
        self._path_for(im_type, bind_id).unlink(missing_ok=True)

    # ---- internals ----

    def _path_for(self, im_type: str, bind_id: str) -> Path:
        """``<base_dir>/<im_type>_<sha256(bind_id)[:16]>.json.enc``"""
        bind_hash = hashlib.sha256(bind_id.encode("utf-8")).hexdigest()[:16]
        return self._base_dir / f"{im_type}_{bind_hash}{_FILE_SUFFIX}"

    def _load(self, path: Path) -> ImCredential:
        blob = path.read_bytes()
        if len(blob) <= _IV_LEN:
            raise ValueError(f"credential file too short: {path}")
        iv, ct = blob[:_IV_LEN], blob[_IV_LEN:]
        plain = AESGCM(self._key).decrypt(iv, ct, associated_data=None)
        data = json.loads(plain.decode("utf-8"))
        return ImCredential(**data)
