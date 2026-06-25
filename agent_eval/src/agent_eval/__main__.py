"""agent_eval CLI 入口。

运行会触发真实 LLM 调用；含 C2 时还可能触发 Tavily 搜索调用。
按项目 llm-api-confirm 规则，执行前需获得用户明确授权。
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from rich.console import Console

from .baseline import ProviderRunInfo, ReportRun, ToolRunInfo, write_report
from .judge import judge_case
from .loader import build_case_set_meta, load_contracts
from .report import CaseRunResult, render_contract_summary, render_failure_details
from .selection import Selection, select_contracts

if TYPE_CHECKING:
    from llm_providers.spec import ProviderSpec

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_CASES_DIR = _PACKAGE_ROOT / "cases"
_REPORTS_DIR = _PACKAGE_ROOT / "reports"
_BASELINES_DIR = _PACKAGE_ROOT / "baselines"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent-eval",
        description="agent 行为契约评测（fixtures + deterministic judge）",
    )
    parser.add_argument(
        "--contract",
        default="all",
        help="只跑指定契约，如 C1 / C2 / C3 / C4；默认 all",
    )
    parser.add_argument(
        "--case",
        dest="case_id",
        default=None,
        metavar="CASE_ID",
        help="只跑指定 case id，如 C4-07；指定后忽略 --contract / --limit-cases",
    )
    parser.add_argument(
        "--limit-cases",
        type=int,
        default=0,
        metavar="N",
        help="每个 contract 只跑前 N 个 case；0 表示不限制",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="覆盖被测 agent 用的 model；默认取 .env 的 DEEPSEEK_MODEL",
    )
    parser.add_argument(
        "--note",
        default="",
        help="写入 report / baseline 的人工注解",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="写入 agent_eval/baselines/；默认写入 agent_eval/reports/",
    )
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=_CASES_DIR,
        help="覆盖 case fixture 目录（主要用于本地验证 / 测试）",
    )
    return parser.parse_args()


def _provider_spec(model: str | None) -> ProviderSpec:
    from llm_providers.spec import ProviderSpec

    spec = ProviderSpec.from_env(prefix="DEEPSEEK")
    if model:
        spec = dataclasses.replace(spec, model=model)
    return spec


def _tool_info() -> ToolRunInfo:
    tavily_present = bool(os.environ.get("TAVILY_API_KEY"))
    return ToolRunInfo(
        tavily_key_present=tavily_present,
        web_search_available=tavily_present,
        recall_past_chats_available=True,
    )


def main() -> int:
    load_dotenv()
    args = _parse_args()
    console = Console()

    try:
        contracts = load_contracts(args.cases_dir)
        case_set = build_case_set_meta(args.cases_dir, contracts)
        selection = Selection(
            contract=args.contract,
            case_id=args.case_id,
            limit_cases=max(0, args.limit_cases),
        )
        selected = select_contracts(contracts, selection)
    except ValueError as exc:
        print(f"[err] {exc}", file=sys.stderr)
        return 1

    from llm_providers.errors import LLMAuthError

    try:
        spec = _provider_spec(args.model)
    except LLMAuthError as exc:
        print(
            f"[配置错误] {exc}\n请在项目根 .env 配置 DEEPSEEK_API_KEY。",
            file=sys.stderr,
        )
        return 1

    tools = _tool_info()
    if (
        any(contract.id == "C2" for contract in selected.contracts)
        and not tools.web_search_available
    ):
        console.print(
            "[yellow]提示：本次包含 C2，但未配置 TAVILY_API_KEY；web_search 不会注册，"
            "C2 结果会反映该运行环境。[/yellow]"
        )

    out_dir = _BASELINES_DIR if args.baseline else _REPORTS_DIR
    console.print(
        f"[dim]model={spec.model} baseline={args.baseline} "
        f"selection={selected.label} total_cases={selected.total_cases}[/dim]\n"
    )

    from .runner import run_case

    started_at = datetime.now(UTC)
    results: dict[str, list[CaseRunResult]] = {}
    for contract in selected.contracts:
        console.print(
            f"[bold]{contract.id} · {contract.name}[/bold] — 跑 {len(contract.cases)} 个 case"
        )
        items: list[CaseRunResult] = []
        for index, case in enumerate(contract.cases, 1):
            console.print(f"  [{index}/{len(contract.cases)}] {case.id} … ", end="")
            trace = run_case(case, spec)
            verdict = judge_case(case, contract, trace)
            mark = "[green]✓[/green]" if verdict.passed else "[red]✗[/red]"
            console.print(mark)
            items.append(CaseRunResult(case=case, trace=trace, verdict=verdict))
        results[contract.id] = items
        console.print()

    ended_at = datetime.now(UTC)
    console.print("[bold]── 汇总 ──[/bold]")
    for contract in selected.contracts:
        render_contract_summary(contract, results[contract.id], console)
    for contract in selected.contracts:
        render_failure_details(contract, results[contract.id], console)

    path = write_report(
        out_dir=out_dir,
        label=selected.label,
        contracts_run=selected.contracts,
        results=results,
        run=ReportRun(
            started_at=started_at,
            ended_at=ended_at,
            selection=selection,
            provider=ProviderRunInfo(
                model=spec.model,
                api_base=spec.api_base,
                defaults=spec.defaults,
                context_window=spec.context_window,
            ),
            tools=tools,
            case_set=case_set,
            note=args.note,
        ),
    )
    console.print(f"[dim]报告已落盘: {path}[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
