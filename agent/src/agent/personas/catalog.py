"""``PersonaCatalog`` —— user + builtin 两层 persona 的统一 CRUD 入口。

详见 docs/requirements/003-engine-persona-management/design.md §4。

身份模型核心约定：

- ``id`` (UUID v4) 是 **主键，不可变**
- ``name`` 是 user 维度唯一的 slug，**可变**（``rename`` 改它 + 改文件名）
- ``source`` 是 ``"user"`` 或 ``"builtin"``
- user 与 builtin 同名 = **并列存在**（各自独立 ID），不是"覆盖"
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Final, Literal

from ..errors import (
    PersonaAlreadyExistsError,
    PersonaNotFoundError,
    PersonaPersistError,
    PersonaReadOnlyError,
)
from ..paths import personas_dir as _default_personas_dir
from . import frontmatter as fm_mod

Source = Literal["user", "builtin"]


@dataclass(frozen=True)
class PersonaInfo:
    """persona 的元信息（**不含 prompt 内容**，防泄漏 + 不冗长）。

    Attributes:
        id: UUID v4 字符串；**主键，不可变**。
        name: slug 字符串；user 维度唯一，跨 source 可同名（与 builtin 并列）。
            可通过 :meth:`PersonaCatalog.rename` 修改。
        source: ``"user"`` 或 ``"builtin"``。不可变。
        description: 可选简短描述，从 frontmatter 的 ``description`` 字段读出。
    """

    id: str
    name: str
    source: Source
    description: str | None


class _KeepSentinel:
    """sentinel for 'do not change this field' in :meth:`PersonaCatalog.update`."""

    def __repr__(self) -> str:
        return "KEEP"


KEEP: Final[_KeepSentinel] = _KeepSentinel()
""":meth:`PersonaCatalog.update` 的"保持原值"哨兵。

三态语义：

- ``KEEP``（默认）：保持原值不变
- ``None``（仅 ``description``）：清除该字段
- 具体 ``str``：替换为新值
"""


_VALID_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _generate_uuid() -> str:
    """生成新 UUID v4 字符串。"""
    return str(uuid.uuid4())


def _validate_name(name: str) -> None:
    """校验 persona name 合法性。

    Raises:
        ValueError: name 非法。
    """
    if not name:
        raise ValueError("persona name 不能为空")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"persona name 不能含路径分隔符或 ..：{name!r}")
    if name.startswith("."):
        raise ValueError(f"persona name 不能以 '.' 开头：{name!r}")
    if name.endswith(".md"):
        raise ValueError(f"persona name 不能带 '.md' 后缀：{name!r}")
    if ":" in name:
        raise ValueError(f"persona name 不能含 ':'（与 CLI 消歧前缀冲突）：{name!r}")


@dataclass(frozen=True)
class _IndexEntry:
    """内部 index 一行。"""

    path: Path
    source: Source
    name: str
    description: str | None


class PersonaCatalog:
    """User + builtin 两层 persona 的统一 CRUD 入口。

    Args:
        external_dir: 用户自定义 persona 目录；``None`` 时用
            :func:`agent.paths.personas_dir`（系统标准用户数据目录下的
            ``personas/``，见决策 0002 §3.19）。**接受参数化**让未来 API 层
            能为多租户场景注入运行时配置，也便于测试用临时目录隔离。

    Note:
        本类**不缓存** index——每次方法调用都重新扫盘构建。孵化期 personas
        数 < 100，扫盘开销 < 5 ms 可接受。未来真有性能问题再加 LRU。
    """

    def __init__(self, external_dir: Path | None = None):
        self._external_dir = external_dir if external_dir is not None else _default_personas_dir()

    # ===== 读 =====

    def list(self) -> list[PersonaInfo]:
        """列出所有 user + builtin persona。

        Returns:
            按 ``name`` 字母序排列；同名时 user 在前 builtin 在后。
        """
        index = self._scan()
        infos: list[PersonaInfo] = []
        for pid, entry in index.items():
            infos.append(
                PersonaInfo(
                    id=pid,
                    name=entry.name,
                    source=entry.source,
                    description=entry.description,
                )
            )
        infos.sort(key=lambda i: (i.name, 0 if i.source == "user" else 1))
        return infos

    def get(self, persona_id: str) -> PersonaInfo:
        """按 id 查 persona 元信息。

        Raises:
            PersonaNotFoundError: id 不存在。
        """
        index = self._scan()
        entry = index.get(persona_id)
        if entry is None:
            raise PersonaNotFoundError(f"找不到 persona id={persona_id!r}")
        return PersonaInfo(
            id=persona_id,
            name=entry.name,
            source=entry.source,
            description=entry.description,
        )

    def read_content(self, persona_id: str) -> str:
        """读 persona 的 prompt 正文（**不含 frontmatter**）。

        Raises:
            PersonaNotFoundError: id 不存在。
            PersonaPersistError: 读文件失败。
        """
        index = self._scan()
        entry = index.get(persona_id)
        if entry is None:
            raise PersonaNotFoundError(f"找不到 persona id={persona_id!r}")
        try:
            text = entry.path.read_text(encoding="utf-8")
        except OSError as e:
            raise PersonaPersistError(f"读 persona 文件失败: {entry.path}: {e}") from e
        _, body = fm_mod.parse(text)
        return body.strip()

    def find_by_name(
        self,
        name: str,
        *,
        source: Source | None = None,
    ) -> PersonaInfo:
        """按 name 查 persona。

        Args:
            name: persona 的 slug。
            source: 显式限定来源；``None`` 时按 "user 优先" 规则：先查 user，
                找不到再 fallback builtin。

        Returns:
            匹配到的第一个 :class:`PersonaInfo`。

        Raises:
            PersonaNotFoundError: 没找到。
        """
        infos = self.list()
        if source is not None:
            for info in infos:
                if info.name == name and info.source == source:
                    return info
            raise PersonaNotFoundError(f"找不到 persona name={name!r}, source={source!r}")
        # user 优先
        for info in infos:
            if info.name == name and info.source == "user":
                return info
        for info in infos:
            if info.name == name and info.source == "builtin":
                return info
        raise PersonaNotFoundError(f"找不到 persona name={name!r}")

    # ===== 写（仅 user；builtin 抛 PersonaReadOnlyError）=====

    def create(
        self,
        name: str,
        content: str,
        description: str | None = None,
    ) -> PersonaInfo:
        """新建 user persona。**生成新的 UUID v4** 作为 id。

        Args:
            name: persona slug，user 维度内唯一（与 builtin 同名允许，并列存在）。
            content: prompt 正文。
            description: 可选简短描述。

        Returns:
            新建的 :class:`PersonaInfo`。

        Raises:
            ValueError: name 非法。
            PersonaAlreadyExistsError: user 维度已有同名。
            PersonaPersistError: 写文件失败。
        """
        _validate_name(name)
        target = self._user_path(name)
        if target.exists():
            raise PersonaAlreadyExistsError(f"user persona 已存在: {name!r}（文件 {target}）")
        new_id = _generate_uuid()
        text = fm_mod.serialize(content, id=new_id, description=description)
        self._atomic_write(target, text)
        return PersonaInfo(id=new_id, name=name, source="user", description=description)

    def update(
        self,
        persona_id: str,
        *,
        content: str | _KeepSentinel = KEEP,
        description: str | None | _KeepSentinel = KEEP,
    ) -> PersonaInfo:
        """更新 user persona 的 content / description。

        Args:
            persona_id: 目标 persona id。
            content: ``KEEP`` 保持原 body；具体 ``str`` 替换。
            description: ``KEEP`` 保持原；``None`` 清除该字段；具体 ``str`` 替换。

        Returns:
            更新后的 :class:`PersonaInfo`。

        Raises:
            PersonaNotFoundError: id 不存在。
            PersonaReadOnlyError: 命中 builtin。
            PersonaPersistError: 读/写文件失败。
        """
        index = self._scan()
        entry = index.get(persona_id)
        if entry is None:
            raise PersonaNotFoundError(f"找不到 persona id={persona_id!r}")
        if entry.source == "builtin":
            raise PersonaReadOnlyError(
                f"builtin persona 不允许 update：name={entry.name!r}, id={persona_id!r}"
            )
        try:
            old_text = entry.path.read_text(encoding="utf-8")
        except OSError as e:
            raise PersonaPersistError(f"读 persona 文件失败: {entry.path}: {e}") from e
        old_fm, old_body = fm_mod.parse(old_text)

        new_body = old_body if isinstance(content, _KeepSentinel) else content

        if isinstance(description, _KeepSentinel):
            new_description = old_fm.get("description")
            if not isinstance(new_description, str):
                new_description = None
        else:
            new_description = description

        new_text = fm_mod.serialize(
            new_body.strip() if new_body else "",
            id=persona_id,
            description=new_description,
        )
        self._atomic_write(entry.path, new_text)
        return PersonaInfo(
            id=persona_id,
            name=entry.name,
            source="user",
            description=new_description,
        )

    def delete(self, persona_id: str) -> None:
        """删除 user persona。

        Raises:
            PersonaNotFoundError: id 不存在。
            PersonaReadOnlyError: 命中 builtin。
            PersonaPersistError: 删文件失败。
        """
        index = self._scan()
        entry = index.get(persona_id)
        if entry is None:
            raise PersonaNotFoundError(f"找不到 persona id={persona_id!r}")
        if entry.source == "builtin":
            raise PersonaReadOnlyError(
                f"builtin persona 不允许 delete：name={entry.name!r}, id={persona_id!r}"
            )
        try:
            entry.path.unlink()
        except OSError as e:
            raise PersonaPersistError(f"删 persona 文件失败: {entry.path}: {e}") from e

    def rename(self, persona_id: str, new_name: str) -> PersonaInfo:
        """改 name + 跟随改文件名；**id 不动**。

        Args:
            persona_id: 目标 persona id。
            new_name: 新 slug。

        Returns:
            更新后的 :class:`PersonaInfo`。

        Raises:
            ValueError: new_name 非法。
            PersonaNotFoundError: id 不存在。
            PersonaReadOnlyError: 命中 builtin。
            PersonaAlreadyExistsError: new_name 在 user 维度已存在。
            PersonaPersistError: 改名失败。
        """
        _validate_name(new_name)
        index = self._scan()
        entry = index.get(persona_id)
        if entry is None:
            raise PersonaNotFoundError(f"找不到 persona id={persona_id!r}")
        if entry.source == "builtin":
            raise PersonaReadOnlyError(
                f"builtin persona 不允许 rename：name={entry.name!r}, id={persona_id!r}"
            )
        if new_name == entry.name:
            return PersonaInfo(
                id=persona_id,
                name=entry.name,
                source="user",
                description=entry.description,
            )
        new_path = self._user_path(new_name)
        if new_path.exists():
            raise PersonaAlreadyExistsError(f"user persona 已存在: {new_name!r}（文件 {new_path}）")
        try:
            os.rename(entry.path, new_path)
        except OSError as e:
            raise PersonaPersistError(f"重命名失败: {entry.path} → {new_path}: {e}") from e
        return PersonaInfo(
            id=persona_id,
            name=new_name,
            source="user",
            description=entry.description,
        )

    # ===== 内部 =====

    def _scan(self) -> dict[str, _IndexEntry]:
        """扫盘构建 ``id → IndexEntry`` 映射。

        发现 user persona 缺 ``id`` 字段时 warn + 生成 UUID v4 + lazy 写回文件
        （保留原 description 与 body）。builtin 缺 ``id`` 视为包资产损坏 → 抛错。
        """
        index: dict[str, _IndexEntry] = {}

        # ---- user ----
        if self._external_dir.exists() and self._external_dir.is_dir():
            try:
                user_files = sorted(self._external_dir.glob("*.md"))
            except OSError as e:
                raise PersonaPersistError(
                    f"列出 user persona 目录失败: {self._external_dir}: {e}"
                ) from e
            for path in user_files:
                pid, description = self._read_meta_user(path)
                name = path.stem
                index[pid] = _IndexEntry(
                    path=path,
                    source="user",
                    name=name,
                    description=description,
                )

        # ---- builtin ----
        for path, name in _iter_builtin_files():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                raise PersonaPersistError(f"读 builtin persona 失败: {path}: {e}") from e
            meta, _ = fm_mod.parse(text)
            raw_id = meta.get("id")
            if not isinstance(raw_id, str) or not _VALID_UUID_RE.match(raw_id):
                raise AssertionError(
                    f"builtin persona {path} 缺 id 字段或格式非法（{raw_id!r}）；"
                    "请补全 frontmatter（这是包资产 bug）"
                )
            description = meta.get("description")
            if not isinstance(description, str):
                description = None
            index[raw_id] = _IndexEntry(
                path=path,
                source="builtin",
                name=name,
                description=description,
            )

        return index

    def _read_meta_user(self, path: Path) -> tuple[str, str | None]:
        """读 user persona 文件的 ``id`` + ``description``。

        缺 id 时 warn + 生成 UUID + lazy 写回；返回 ``(id, description)``。
        """
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise PersonaPersistError(f"读 persona 文件失败: {path}: {e}") from e
        meta, body = fm_mod.parse(text)
        raw_id = meta.get("id")
        description = meta.get("description")
        if not isinstance(description, str):
            description = None

        if isinstance(raw_id, str) and _VALID_UUID_RE.match(raw_id):
            return raw_id, description

        new_id = _generate_uuid()
        warnings.warn(
            f"user persona {path} 缺 id 字段（或格式非法）；自动生成 {new_id} 并写回",
            stacklevel=2,
        )
        new_text = fm_mod.serialize(body.strip(), id=new_id, description=description)
        self._atomic_write(path, new_text)
        return new_id, description

    def _user_path(self, name: str) -> Path:
        return self._external_dir / f"{name}.md"

    def _atomic_write(self, target: Path, text: str) -> None:
        """tempfile + ``os.replace`` 原子写。"""
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path_str = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=str(target.parent),
            )
            tmp_path = Path(tmp_path_str)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(tmp_path, target)
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                raise
        except OSError as e:
            raise PersonaPersistError(f"原子写失败: {target}: {e}") from e


def _iter_builtin_files() -> list[tuple[Path, str]]:
    """枚举 ``agent.personas`` 包下的所有 ``.md``，返回 ``[(Path, stem), ...]``。

    Note:
        ``importlib.resources.files`` 在 editable install 场景下返回 :class:`Path`
        子类，可直接使用；wheel 装包场景理论上是 zipfile traversal，需要 ``as_file``
        提取——本期孵化只跑 editable install，未触发后者。
    """
    from contextlib import nullcontext
    from importlib.resources import as_file

    root = files("agent.personas")
    result: list[tuple[Path, str]] = []
    for entry in root.iterdir():
        name = entry.name
        if not name.endswith(".md"):
            continue
        if name == "README.md":
            continue
        stem = name[: -len(".md")]
        ctx = nullcontext(entry) if isinstance(entry, Path) else as_file(entry)
        with ctx as p:
            result.append((Path(str(p)), stem))
    return result
