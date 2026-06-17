"""IM 凭据加密存储单测(M22.2)。

覆盖 design.md §3.6 + progress.md §M22.2 单测点:

- ``save → list_all`` round-trip(字段完全一致)
- 多条凭据 save / list / delete 行为
- 密钥派生 same machine 同输入同输出
- wrong key 解密失败时 ``list_all`` 跳过(不抛)
- 文件名 hash 路径稳定(``<im_type>_<bind_id_hash[:16]>.json.enc``)
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_bridge.protocols.im.credentials import CredentialStore, ImCredential


@pytest.fixture
def store(tmp_path: Path) -> CredentialStore:
    """每个测试用独立 tmp 目录,互不污染。"""
    return CredentialStore(base_dir=tmp_path / "im_credentials")


def _make_qq_cred(bind_id: str = "OPENID-ABCD") -> ImCredential:
    return ImCredential(
        im_type="qq",
        bind_id=bind_id,
        app_id="123456",
        client_secret="secret-shhh",
        user_openid=bind_id,
    )


# ---------- save → list_all round-trip ----------


def test_save_then_list_returns_identical_credential(store: CredentialStore) -> None:
    cred = _make_qq_cred()
    store.save(cred)
    loaded = store.list_all()
    assert len(loaded) == 1
    assert loaded[0] == cred  # frozen dataclass __eq__ 字段全等


def test_save_two_creds_then_list_returns_both(store: CredentialStore) -> None:
    a = _make_qq_cred("OPENID-A")
    b = _make_qq_cred("OPENID-B")
    store.save(a)
    store.save(b)
    loaded = store.list_all()
    assert sorted([c.bind_id for c in loaded]) == ["OPENID-A", "OPENID-B"]


def test_delete_then_list_skips_deleted(store: CredentialStore) -> None:
    a = _make_qq_cred("OPENID-A")
    b = _make_qq_cred("OPENID-B")
    store.save(a)
    store.save(b)
    store.delete("qq", "OPENID-A")
    loaded = store.list_all()
    assert len(loaded) == 1
    assert loaded[0].bind_id == "OPENID-B"


def test_delete_nonexistent_is_silent(store: CredentialStore) -> None:
    store.delete("qq", "NEVER-EXISTED")  # 不抛
    assert store.list_all() == []


def test_save_overwrites_existing_same_bind_id(store: CredentialStore) -> None:
    a = _make_qq_cred("OPENID-A")
    store.save(a)
    a2 = ImCredential(
        im_type="qq",
        bind_id="OPENID-A",
        app_id="999999",  # 改 app_id
        client_secret="new-secret",
        user_openid="OPENID-A",
    )
    store.save(a2)
    loaded = store.list_all()
    assert len(loaded) == 1
    assert loaded[0].app_id == "999999"
    assert loaded[0].client_secret == "new-secret"


# ---------- 密钥派生稳定性 ----------


def test_derive_key_deterministic(tmp_path: Path) -> None:
    """同机器同 user → 密钥相同。"""
    s1 = CredentialStore(tmp_path / "a")
    s2 = CredentialStore(tmp_path / "b")
    assert s1._key == s2._key


def test_derive_key_changes_with_username(tmp_path: Path) -> None:
    """换用户名 → 密钥变。"""
    with patch("getpass.getuser", return_value="user-a"):
        key_a = CredentialStore(tmp_path / "a")._key
    with patch("getpass.getuser", return_value="user-b"):
        key_b = CredentialStore(tmp_path / "b")._key
    assert key_a != key_b


def test_derive_key_changes_with_hostname(tmp_path: Path) -> None:
    """换主机名 → 密钥变。"""
    with patch("socket.gethostname", return_value="host-a"):
        key_a = CredentialStore(tmp_path / "a")._key
    with patch("socket.gethostname", return_value="host-b"):
        key_b = CredentialStore(tmp_path / "b")._key
    assert key_a != key_b


# ---------- wrong-key 跳过 ----------


def test_wrong_key_skipped_silently(tmp_path: Path) -> None:
    """模拟换机:store-A 写入,store-B 用不同 username 读 → 解密失败 → list_all 跳过。"""
    base = tmp_path / "im_credentials"
    with patch("getpass.getuser", return_value="alice"):
        store_a = CredentialStore(base)
        store_a.save(_make_qq_cred())
    # 换 username 等价于换 derive key
    with patch("getpass.getuser", return_value="bob"):
        store_b = CredentialStore(base)
        loaded = store_b.list_all()
    assert loaded == []  # 跳过,不抛


def test_corrupt_file_skipped_silently(store: CredentialStore, tmp_path: Path) -> None:
    """文件被外部改坏 → 跳过,不抛。"""
    store.save(_make_qq_cred())
    # 找到落盘文件,改坏
    enc_files = list((tmp_path / "im_credentials").glob("*.enc"))
    assert len(enc_files) == 1
    enc_files[0].write_bytes(b"corrupted-not-valid-aes-gcm-blob")
    assert store.list_all() == []  # 跳过


def test_too_short_file_skipped(store: CredentialStore, tmp_path: Path) -> None:
    """文件比 IV 还短 → ValueError → 跳过。"""
    base = tmp_path / "im_credentials"
    base.mkdir(parents=True)
    (base / "qq_deadbeefdeadbeef.json.enc").write_bytes(b"\x00" * 5)
    assert store.list_all() == []


# ---------- 文件名路径稳定 ----------


def test_path_for_uses_sha256_prefix(store: CredentialStore, tmp_path: Path) -> None:
    cred = _make_qq_cred("OPENID-STABLE")
    store.save(cred)
    expected_hash = hashlib.sha256(b"OPENID-STABLE").hexdigest()[:16]
    expected_path = tmp_path / "im_credentials" / f"qq_{expected_hash}.json.enc"
    assert expected_path.exists()


def test_path_for_different_bind_id_different_path(store: CredentialStore, tmp_path: Path) -> None:
    store.save(_make_qq_cred("OPENID-A"))
    store.save(_make_qq_cred("OPENID-B"))
    files = list((tmp_path / "im_credentials").glob("*.enc"))
    assert len(files) == 2  # 不同 bind_id → 不同 hash → 不同文件


# ---------- 边界 ----------


def test_list_all_empty_when_dir_missing(tmp_path: Path) -> None:
    """base_dir 不存在 → 返回空 list,不抛。"""
    store = CredentialStore(tmp_path / "never-created")
    assert store.list_all() == []


def test_credential_with_extra_field(store: CredentialStore) -> None:
    """``extra`` dict 也能 round-trip(未来扩展字段)。"""
    cred = ImCredential(
        im_type="qq",
        bind_id="OPENID-X",
        app_id="1",
        client_secret="s",
        user_openid="OPENID-X",
        extra={"tenant_key": "T-001", "rate_limit": 60},
    )
    store.save(cred)
    loaded = store.list_all()
    assert loaded[0].extra == {"tenant_key": "T-001", "rate_limit": 60}


def test_iv_is_random_per_save(store: CredentialStore, tmp_path: Path) -> None:
    """同 cred 连保存两次,密文应不同(IV 随机)。"""
    cred = _make_qq_cred()
    store.save(cred)
    first_blob = next((tmp_path / "im_credentials").glob("*.enc")).read_bytes()
    store.save(cred)  # 同 bind_id 覆盖
    second_blob = next((tmp_path / "im_credentials").glob("*.enc")).read_bytes()
    assert first_blob != second_blob  # IV 不同 → 整体密文不同
