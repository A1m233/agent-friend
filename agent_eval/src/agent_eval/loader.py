"""加载 ``agent_eval/cases`` 下的 YAML fixtures。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from .schema import (
    Case,
    CaseJudge,
    CaseSetMeta,
    Contract,
    FixtureMessage,
    FixtureSession,
)


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


def _parse_fixture_session(raw: Any, *, label: str) -> FixtureSession | None:
    if raw is None:
        return None
    data = _mapping(raw, label=label)
    days_raw = data.get("days_ago", 0)
    try:
        days_ago = int(days_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}.days_ago 必须是整数") from exc
    messages_raw = data.get("messages", [])
    if not isinstance(messages_raw, list):
        raise ValueError(f"{label}.messages 必须是 list")
    messages: list[FixtureMessage] = []
    for index, msg_raw in enumerate(messages_raw):
        msg = _mapping(msg_raw, label=f"{label}.messages[{index}]")
        role = _required_str(msg, "role", label=f"{label}.messages[{index}]")
        text = _required_str(msg, "text", label=f"{label}.messages[{index}]")
        messages.append(FixtureMessage(role=role, text=text))
    return FixtureSession(days_ago=days_ago, messages=messages)


def _parse_judge(raw: Any, *, label: str) -> CaseJudge | None:
    if raw is None:
        return None
    data = _mapping(raw, label=label)
    return CaseJudge(
        must_call=_optional_str(data, "must_call"),
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
    seen_case_ids: set[str] = set()
    for index, case_raw in enumerate(cases_raw):
        label = f"{path}.cases[{index}]"
        case_data = _mapping(case_raw, label=label)
        case_id = _required_str(case_data, "id", label=label)
        if case_id in seen_case_ids:
            raise ValueError(f"case id 重复: {case_id}")
        seen_case_ids.add(case_id)
        cases.append(
            Case(
                id=case_id,
                query=_required_str(case_data, "query", label=label),
                facet=str(case_data.get("facet", "")),
                source=str(case_data.get("source", "")),
                forbidden_terms=_str_list(
                    case_data.get("forbidden_terms"),
                    label=f"{label}.forbidden_terms",
                ),
                fixture_session=_parse_fixture_session(
                    case_data.get("fixture_session"),
                    label=f"{label}.fixture_session",
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
    return sorted(cases_dir.glob("c*.yaml"))


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
    counts = {contract.id: len(contract.cases) for contract in contracts}
    return CaseSetMeta(
        digest=digest.hexdigest(),
        total_contracts=len(contracts),
        total_cases=sum(counts.values()),
        contract_counts=counts,
    )
