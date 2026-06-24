"""加载 ``cases/c*.yaml`` 为 :class:`Contract` + :class:`Case` 列表。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .schema import Case, CaseJudge, Contract, FixtureMessage, FixtureSession


def _parse_fixture_session(raw: Any) -> FixtureSession | None:
    if not raw:
        return None
    return FixtureSession(
        days_ago=int(raw.get("days_ago", 0)),
        messages=[
            FixtureMessage(role=str(m["role"]), text=str(m["text"]))
            for m in raw.get("messages", []) or []
        ],
    )


def _parse_judge(raw: Any) -> CaseJudge | None:
    if not raw:
        return None
    return CaseJudge(
        must_call=raw.get("must_call"),
        must_mention_any=[str(x) for x in (raw.get("must_mention_any") or [])],
        must_not_mention_any=[str(x) for x in (raw.get("must_not_mention_any") or [])],
    )


def load_contracts(cases_dir: Path) -> list[Contract]:
    """扫 ``cases_dir`` 下所有 ``c*.yaml``,按文件名排序加载。"""
    contracts: list[Contract] = []
    for path in sorted(cases_dir.glob("c*.yaml")):
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        contract_meta = data["contract"]
        cases_raw = data["cases"]

        cases = [
            Case(
                id=c["id"],
                query=c["query"],
                facet=c.get("facet", ""),
                source=c.get("source", ""),
                forbidden_terms=list(c.get("forbidden_terms", []) or []),
                fixture_session=_parse_fixture_session(c.get("fixture_session")),
                judge=_parse_judge(c.get("judge")),
            )
            for c in cases_raw
        ]

        contracts.append(
            Contract(
                id=contract_meta["id"],
                name=contract_meta["name"],
                expected_behavior=contract_meta["expected_behavior"],
                expected_tool=contract_meta.get("expected_tool"),
                required_in_response=contract_meta.get("required_in_response"),
                cases=cases,
            )
        )
    return contracts
