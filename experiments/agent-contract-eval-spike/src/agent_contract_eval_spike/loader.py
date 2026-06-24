"""加载 ``cases/c*.yaml`` 为 :class:`Contract` + :class:`Case` 列表。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .schema import Case, Contract


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
