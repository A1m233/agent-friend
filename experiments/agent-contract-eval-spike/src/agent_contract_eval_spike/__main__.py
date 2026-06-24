"""agent-contract-eval-spike · CLI 主入口。

⚠️ 跑会触发真实 DeepSeek API 调用(每 case 一次主对话,可能含工具循环),
按项目 ``llm-api-confirm`` 规则首次需获用户授权。

用法::

    # 全量(60 个 case)
    ./run.sh

    # dry-run:每契约只跑前 N 个
    uv run python -m agent_contract_eval_spike --dry-run 2

    # 单契约
    uv run python -m agent_contract_eval_spike --contract C1
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from llm_providers import LLMAuthError, ProviderSpec

from .judge import judge_case
from .loader import load_contracts
from .report import render_console, render_failure_details, write_json_report
from .runner import build_session_manager, run_case
from .schema import Case, Trace, Verdict

# __file__ → experiments/agent-contract-eval-spike/src/agent_contract_eval_spike/__main__.py
# parents[2] → experiments/agent-contract-eval-spike/
_SPIKE_ROOT = Path(__file__).resolve().parents[2]
_CASES_DIR = _SPIKE_ROOT / "cases"
_OUT_DIR = _SPIKE_ROOT / "out"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="agent-contract-eval-spike",
        description="行为契约评测 PoC(契约 + fixture + deterministic 判分)",
    )
    p.add_argument(
        "--dry-run",
        type=int,
        default=0,
        metavar="N",
        help="每契约只跑前 N 个 case(0 = 全量,默认)",
    )
    p.add_argument(
        "--contract",
        choices=["C1", "C2", "C3", "all"],
        default="all",
        help="只跑指定契约(默认 all)",
    )
    p.add_argument(
        "--model",
        default=None,
        help="覆盖被测 agent 用的 model(默认走 .env 的 DEEPSEEK_MODEL)",
    )
    return p.parse_args()


def main() -> int:
    load_dotenv()
    args = _parse_args()

    if not _CASES_DIR.exists():
        print(f"[err] cases 目录不存在: {_CASES_DIR}", file=sys.stderr)
        return 1

    contracts = load_contracts(_CASES_DIR)
    if args.contract != "all":
        contracts = [c for c in contracts if c.id == args.contract]
    if not contracts:
        print(f"[err] 没找到契约: {args.contract}", file=sys.stderr)
        return 1

    try:
        spec = ProviderSpec.from_env(prefix="DEEPSEEK")
    except LLMAuthError as e:
        print(
            f"[配置错误] {e}\n请在项目根 .env 配置 DEEPSEEK_API_KEY。",
            file=sys.stderr,
        )
        return 1
    model = args.model or spec.model

    console = Console()
    console.print(
        f"[dim]model={model}  tavily_key={'set' if os.environ.get('TAVILY_API_KEY') else 'NOT set'}  "
        f"dry_run={args.dry_run}  contracts={[c.id for c in contracts]}[/dim]\n"
    )

    mgr = build_session_manager(model=model)

    results: dict[str, list[tuple[Case, Trace, Verdict]]] = {}
    total_cases = 0
    for contract in contracts:
        cases_to_run = contract.cases
        if args.dry_run > 0:
            cases_to_run = cases_to_run[: args.dry_run]
        console.print(
            f"[bold]{contract.id} · {contract.name}[/bold] — 跑 {len(cases_to_run)} 个 case"
        )

        pairs: list[tuple[Case, Trace, Verdict]] = []
        for i, case in enumerate(cases_to_run, 1):
            console.print(f"  [{i}/{len(cases_to_run)}] {case.id} … ", end="")
            trace = run_case(case, mgr, model)
            verdict = judge_case(case, contract, trace)
            mark = "[green]✓[/green]" if verdict.passed else "[red]✗[/red]"
            console.print(mark)
            pairs.append((case, trace, verdict))
            total_cases += 1

        results[contract.id] = pairs
        console.print()

    console.print("[bold]── 汇总 ──[/bold]")
    for contract in contracts:
        render_console(contract, results[contract.id], console)
    for contract in contracts:
        render_failure_details(contract, results[contract.id], console)

    report_path = write_json_report(
        _OUT_DIR,
        contracts,
        results,
        meta={
            "model": model,
            "tavily_key_present": bool(os.environ.get("TAVILY_API_KEY")),
            "dry_run": args.dry_run,
            "total_cases": total_cases,
        },
    )
    console.print(f"[dim]报告已落盘: {report_path}[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
