"""CLI 参数到待运行 contracts 的纯选择逻辑。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .schema import Contract


@dataclass(frozen=True)
class Selection:
    """用户选择的运行范围。"""

    contract: str = "all"
    case_id: str | None = None
    limit_cases: int = 0


@dataclass(frozen=True)
class SelectionResult:
    """筛选后的 contracts 与文件名标签。"""

    contracts: list[Contract]
    label: str
    total_cases: int


def select_contracts(contracts: list[Contract], selection: Selection) -> SelectionResult:
    """按 contract / case / limit 选择要跑的 case。"""
    if selection.case_id:
        for contract in contracts:
            for case in contract.cases:
                if case.id == selection.case_id:
                    selected = [replace(contract, cases=[case])]
                    return SelectionResult(
                        contracts=selected,
                        label=case.id,
                        total_cases=1,
                    )
        raise ValueError(f"没找到 case: {selection.case_id}")

    selected = contracts
    label = "all"
    if selection.contract != "all":
        selected = [contract for contract in contracts if contract.id == selection.contract]
        if not selected:
            raise ValueError(f"没找到契约: {selection.contract}")
        label = selection.contract

    if selection.limit_cases > 0:
        selected = [
            replace(contract, cases=contract.cases[: selection.limit_cases])
            for contract in selected
        ]
        label = f"{label}-limit{selection.limit_cases}"

    total = sum(len(contract.cases) for contract in selected)
    if total == 0:
        raise ValueError("选择结果为空")
    return SelectionResult(contracts=selected, label=label, total_cases=total)
