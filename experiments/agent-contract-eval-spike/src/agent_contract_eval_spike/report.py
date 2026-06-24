"""跑完一批 case,出控制台报告 + 落 ``out/<ISO-datetime>.json``。"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .schema import Case, Contract, Trace, Verdict


def render_console(
    contract: Contract,
    pairs: list[tuple[Case, Trace, Verdict]],
    console: Console,
) -> None:
    table = Table(title=f"{contract.id} · {contract.name}", show_lines=False)
    table.add_column("id", style="dim")
    table.add_column("pass")
    table.add_column("query", overflow="fold", max_width=40)
    table.add_column("reason", overflow="fold", max_width=60)

    for case, _trace, verdict in pairs:
        mark = "[green]✓[/green]" if verdict.passed else "[red]✗[/red]"
        table.add_row(case.id, mark, case.query, verdict.reason)
    console.print(table)

    passed = sum(1 for _, _, v in pairs if v.passed)
    total = len(pairs)
    rate = (passed / total * 100) if total else 0
    console.print(
        f"  → {contract.id} pass rate: [bold]{passed}/{total}[/bold] ({rate:.0f}%)\n"
    )


def render_failure_details(
    contract: Contract,
    pairs: list[tuple[Case, Trace, Verdict]],
    console: Console,
) -> None:
    fails = [(c, t, v) for c, t, v in pairs if not v.passed]
    if not fails:
        return
    console.print(f"[yellow]── {contract.id} 失败 case 详情 ──[/yellow]")
    for case, trace, verdict in fails:
        console.print(f"[bold]{case.id}[/bold] · {case.query}")
        console.print(f"  reason: {verdict.reason}")
        names = [tc.name for tc in trace.tool_calls] or "(none)"
        console.print(f"  tool_calls: {names}")
        if trace.final_text:
            head = trace.final_text[:200].replace("\n", " ")
            tail = "…" if len(trace.final_text) > 200 else ""
            console.print(f"  response: {head}{tail}")
        if trace.error:
            console.print(f"  [red]error[/red]: {trace.error}")
        console.print()


def write_json_report(
    out_dir: Path,
    contracts_run: list[Contract],
    results: dict[str, list[tuple[Case, Trace, Verdict]]],
    meta: dict,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    path = out_dir / f"{ts}.json"

    payload = {
        "meta": meta,
        "contracts": {},
    }
    for contract in contracts_run:
        pairs = results[contract.id]
        passed = sum(1 for _, _, v in pairs if v.passed)
        payload["contracts"][contract.id] = {
            "name": contract.name,
            "expected_behavior": contract.expected_behavior,
            "total": len(pairs),
            "passed": passed,
            "pass_rate": (passed / len(pairs)) if pairs else 0,
            "cases": [
                {
                    "id": case.id,
                    "query": case.query,
                    "facet": case.facet,
                    "source": case.source,
                    "passed": verdict.passed,
                    "reason": verdict.reason,
                    "tool_calls": [
                        {
                            "name": tc.name,
                            "args": tc.args,
                            "result_text": tc.result_text,
                            "is_error": tc.is_error,
                        }
                        for tc in trace.tool_calls
                    ],
                    "final_text": trace.final_text,
                    "stop_reason": trace.stop_reason,
                    "error": trace.error,
                }
                for case, trace, verdict in pairs
            ],
        }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
