"""加载 ``agent_eval/cases`` 下的 YAML fixtures。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .schema import (
    Case,
    CaseJudge,
    CaseSetMeta,
    Contract,
    FakeTool,
    FixtureAttachment,
    FixtureMessage,
    FixtureSession,
)

_SUPPORTED_SURFACES = frozenset({"desktop_chat", "voice_call", "im:qq"})


def _mapping(raw: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} 必须是 mapping")
    return raw


def _required_str(raw: dict[str, Any], key: str, *, label: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.{key} 必须是非空字符串")
    return value


def _optional_str(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} 必须是字符串或 null")
    return value


def _optional_aware_datetime(raw: dict[str, Any], key: str, *, label: str) -> datetime | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label}.{key} 必须是 ISO 8601 字符串或 null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label}.{key} 必须是合法 ISO 8601 日期时间") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label}.{key} 必须包含时区")
    return parsed


def _str_list(raw: Any, *, label: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{label} 必须是 list")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"{label} 只能包含字符串")
        out.append(item)
    return out


def _bool(raw: Any, *, label: str) -> bool:
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise ValueError(f"{label} 必须是 bool")
    return raw


def _parse_fixture_attachment(
    raw: Any,
    *,
    label: str,
    asset_root: Path,
) -> FixtureAttachment:
    data = _mapping(raw, label=label)
    relative = Path(_required_str(data, "path", label=label))
    if relative.is_absolute():
        raise ValueError(f"{label}.path 必须是相对路径")
    root = asset_root.resolve()
    resolved = (root / relative).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{label}.path 不能逃逸 agent_eval 目录")
    if not resolved.is_file():
        raise ValueError(f"{label}.path 文件不存在: {relative.as_posix()}")
    display_name = _optional_str(data, "display_name") or resolved.name
    if not display_name.strip():
        raise ValueError(f"{label}.display_name 必须是非空字符串")
    return FixtureAttachment(path=resolved, display_name=display_name.strip())


def _parse_fixture_attachments(
    raw: Any,
    *,
    label: str,
    asset_root: Path,
) -> list[FixtureAttachment]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{label} 必须是 list")
    return [
        _parse_fixture_attachment(
            item,
            label=f"{label}[{index}]",
            asset_root=asset_root,
        )
        for index, item in enumerate(raw)
    ]


def _parse_fixture_session(
    raw: Any,
    *,
    label: str,
    asset_root: Path,
) -> FixtureSession | None:
    if raw is None:
        return None
    data = _mapping(raw, label=label)
    days_raw = data.get("days_ago", 0)
    try:
        days_ago = int(days_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}.days_ago 必须是整数") from exc
    messages = _parse_fixture_messages(
        data.get("messages", []),
        label=f"{label}.messages",
        asset_root=asset_root,
    )
    title_raw = data.get("title")
    title = None
    if title_raw is not None:
        if not isinstance(title_raw, str) or not title_raw.strip():
            raise ValueError(f"{label}.title 必须是非空字符串")
        title = title_raw.strip()
    return FixtureSession(days_ago=days_ago, messages=messages, title=title)


def _parse_fixture_messages(
    raw: Any,
    *,
    label: str,
    asset_root: Path,
) -> list[FixtureMessage]:
    messages_raw = raw
    if not isinstance(messages_raw, list):
        raise ValueError(f"{label} 必须是 list")
    messages: list[FixtureMessage] = []
    for index, msg_raw in enumerate(messages_raw):
        item_label = f"{label}[{index}]"
        msg = _mapping(msg_raw, label=item_label)
        role = _required_str(msg, "role", label=item_label)
        if role not in {"user", "assistant"}:
            raise ValueError(f"{item_label}.role 只能是 user 或 assistant")
        text_raw = msg.get("text", "")
        if not isinstance(text_raw, str):
            raise ValueError(f"{item_label}.text 必须是字符串")
        attachments = _parse_fixture_attachments(
            msg.get("attachments"),
            label=f"{item_label}.attachments",
            asset_root=asset_root,
        )
        if not text_raw.strip() and not attachments:
            raise ValueError(f"{item_label} 必须包含非空 text 或 attachment")
        messages.append(
            FixtureMessage(
                role=role,
                text=text_raw,
                attachments=attachments,
            )
        )
    return messages


def _parse_fake_tool(raw: Any, *, label: str) -> FakeTool | None:
    if raw is None:
        return None
    data = _mapping(raw, label=label)
    return FakeTool(
        name=_required_str(data, "name", label=label),
        result_text=_required_str(data, "result_text", label=label),
        is_error=_bool(data.get("is_error"), label=f"{label}.is_error"),
    )


def _parse_judge(raw: Any, *, label: str) -> CaseJudge | None:
    if raw is None:
        return None
    data = _mapping(raw, label=label)
    return CaseJudge(
        must_call=_optional_str(data, "must_call"),
        must_not_call_any=_str_list(
            data.get("must_not_call_any"),
            label=f"{label}.must_not_call_any",
        ),
        require_non_empty_response=_bool(
            data.get("require_non_empty_response"),
            label=f"{label}.require_non_empty_response",
        ),
        forbid_question=_bool(
            data.get("forbid_question"),
            label=f"{label}.forbid_question",
        ),
        must_mention_any=_str_list(data.get("must_mention_any"), label=f"{label}.must_mention_any"),
        must_not_mention_any=_str_list(
            data.get("must_not_mention_any"),
            label=f"{label}.must_not_mention_any",
        ),
        forbid_memory_mechanism_terms=_bool(
            data.get("forbid_memory_mechanism_terms"),
            label=f"{label}.forbid_memory_mechanism_terms",
        ),
        forbid_unsupported_lookup_claim=_bool(
            data.get("forbid_unsupported_lookup_claim"),
            label=f"{label}.forbid_unsupported_lookup_claim",
        ),
        forbid_memory_mismatch_story=_bool(
            data.get("forbid_memory_mismatch_story"),
            label=f"{label}.forbid_memory_mismatch_story",
        ),
        forbid_unsupported_source_attribution=_bool(
            data.get("forbid_unsupported_source_attribution"),
            label=f"{label}.forbid_unsupported_source_attribution",
        ),
    )


def _load_contract_file(path: Path) -> Contract:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"读取 case 文件失败: {path}: {exc}") from exc
    data = _mapping(raw, label=str(path))
    meta = _mapping(data.get("contract"), label=f"{path}.contract")
    cases_raw = data.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise ValueError(f"{path}.cases 必须是非空 list")

    cases: list[Case] = []
    asset_root = path.parent.parent
    seen_case_ids: set[str] = set()
    for index, case_raw in enumerate(cases_raw):
        label = f"{path}.cases[{index}]"
        case_data = _mapping(case_raw, label=label)
        case_id = _required_str(case_data, "id", label=label)
        if case_id in seen_case_ids:
            raise ValueError(f"case id 重复: {case_id}")
        seen_case_ids.add(case_id)
        surface = _optional_str(case_data, "surface")
        if surface is not None and surface not in _SUPPORTED_SURFACES:
            raise ValueError(
                f"{label}.surface 不支持 {surface!r}；可选值: {sorted(_SUPPORTED_SURFACES)}"
            )
        cases.append(
            Case(
                id=case_id,
                query=_required_str(case_data, "query", label=label),
                facet=str(case_data.get("facet", "")),
                source=str(case_data.get("source", "")),
                runtime_now=_optional_aware_datetime(case_data, "runtime_now", label=label),
                surface=surface,
                forbidden_terms=_str_list(
                    case_data.get("forbidden_terms"),
                    label=f"{label}.forbidden_terms",
                ),
                fixture_session=_parse_fixture_session(
                    case_data.get("fixture_session"),
                    label=f"{label}.fixture_session",
                    asset_root=asset_root,
                ),
                current_session_messages=_parse_fixture_messages(
                    case_data.get("current_session_messages", []),
                    label=f"{label}.current_session_messages",
                    asset_root=asset_root,
                ),
                query_attachments=_parse_fixture_attachments(
                    case_data.get("query_attachments"),
                    label=f"{label}.query_attachments",
                    asset_root=asset_root,
                ),
                fake_tool=_parse_fake_tool(
                    case_data.get("fake_tool"),
                    label=f"{label}.fake_tool",
                ),
                judge=_parse_judge(case_data.get("judge"), label=f"{label}.judge"),
            )
        )

    return Contract(
        id=_required_str(meta, "id", label=f"{path}.contract"),
        name=_required_str(meta, "name", label=f"{path}.contract"),
        expected_behavior=_required_str(meta, "expected_behavior", label=f"{path}.contract"),
        expected_tool=_optional_str(meta, "expected_tool"),
        required_in_response=_optional_str(meta, "required_in_response"),
        cases=cases,
    )


def case_files(cases_dir: Path) -> list[Path]:
    """返回排序后的 case 文件列表。"""

    def sort_key(path: Path) -> tuple[int, str]:
        match = re.match(r"c(\d+)", path.stem, re.IGNORECASE)
        return (int(match.group(1)) if match else 2**31, path.name)

    return sorted(cases_dir.glob("c*.yaml"), key=sort_key)


def load_contracts(cases_dir: Path) -> list[Contract]:
    """扫 ``cases_dir`` 下所有 ``c*.yaml``，按文件名排序加载。"""
    paths = case_files(cases_dir)
    if not paths:
        raise ValueError(f"cases 目录没有 c*.yaml: {cases_dir}")

    contracts: list[Contract] = []
    seen_contract_ids: set[str] = set()
    seen_case_ids: set[str] = set()
    for path in paths:
        contract = _load_contract_file(path)
        if contract.id in seen_contract_ids:
            raise ValueError(f"contract id 重复: {contract.id}")
        seen_contract_ids.add(contract.id)
        for case in contract.cases:
            if case.id in seen_case_ids:
                raise ValueError(f"case id 重复: {case.id}")
            seen_case_ids.add(case.id)
        contracts.append(contract)
    return contracts


def build_case_set_meta(cases_dir: Path, contracts: list[Contract]) -> CaseSetMeta:
    """计算 case 集 digest 与数量信息。"""
    digest = hashlib.sha256()
    for path in case_files(cases_dir):
        rel = path.relative_to(cases_dir).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    asset_root = cases_dir.parent.resolve()
    attachment_paths = {
        attachment.path.resolve()
        for contract in contracts
        for case in contract.cases
        for attachment in (
            list(case.query_attachments)
            + [item for message in case.current_session_messages for item in message.attachments]
            + [
                item
                for message in (case.fixture_session.messages if case.fixture_session else [])
                for item in message.attachments
            ]
        )
    }
    for path in sorted(attachment_paths, key=lambda item: item.as_posix()):
        rel = path.relative_to(asset_root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    counts = {contract.id: len(contract.cases) for contract in contracts}
    return CaseSetMeta(
        digest=digest.hexdigest(),
        total_contracts=len(contracts),
        total_cases=sum(counts.values()),
        contract_counts=counts,
    )
