"""控制台报告与 JSON 结果序列化辅助。"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from .schema import Case, Contract, Trace, Verdict


@dataclass(frozen=True)
class CaseRunResult:
    """单条 case 的运行与判分结果。"""

    case: Case
    trace: Trace
    verdict: Verdict


def render_contract_summary(
    contract: Contract,
    results: list[CaseRunResult],
    console: Console,
) -> None:
    """把一个 contract 的结果渲染为终端表格。"""
    table = Table(title=f"{contract.id} · {contract.name}", show_lines=False)
    table.add_column("id", style="dim")
    table.add_column("pass")
    table.add_column("query", overflow="fold", max_width=42)
    table.add_column("reason", overflow="fold", max_width=60)
    for item in results:
        mark = "[green]✓[/green]" if item.verdict.passed else "[red]✗[/red]"
        table.add_row(item.case.id, mark, item.case.query, item.verdict.reason)
    console.print(table)

    passed = sum(1 for item in results if item.verdict.passed)
    total = len(results)
    rate = (passed / total * 100) if total else 0
    console.print(f"  → {contract.id} pass rate: [bold]{passed}/{total}[/bold] ({rate:.0f}%)\n")


def render_failure_details(
    contract: Contract,
    results: list[CaseRunResult],
    console: Console,
) -> None:
    """打印失败 case 的关键 trace。"""
    failures = [item for item in results if not item.verdict.passed]
    if not failures:
        return
    console.print(f"[yellow]── {contract.id} 失败 case 详情 ──[/yellow]")
    for item in failures:
        console.print(f"[bold]{item.case.id}[/bold] · {item.case.query}")
        console.print(f"  reason: {item.verdict.reason}")
        names = [tool_call.name for tool_call in item.trace.tool_calls] or "(none)"
        console.print(f"  tool_calls: {names}")
        if item.trace.skill_activations:
            console.print(
                "  skill_activations: "
                f"{[(activation.name, activation.status) for activation in item.trace.skill_activations]}"
            )
        if item.trace.assistant_segments:
            console.print("  assistant_segments:")
            for index, segment in enumerate(item.trace.assistant_segments, start=1):
                head = segment[:180].replace("\n", " ")
                tail = "…" if len(segment) > 180 else ""
                console.print(f"    {index}. {head}{tail}")
        if item.trace.final_text:
            head = item.trace.final_text[:240].replace("\n", " ")
            tail = "…" if len(item.trace.final_text) > 240 else ""
            console.print(f"  response: {head}{tail}")
        if item.trace.error:
            console.print(f"  [red]error[/red]: {item.trace.error}")
        if item.trace.skill_catalog_observation is not None:
            observation = item.trace.skill_catalog_observation
            console.print(
                "  skill_catalog: "
                f"snapshot={observation.snapshot_coverage}/"
                f"{len(observation.snapshot_entries)} "
                f"request={observation.request_coverage}/"
                f"{len(observation.request_entries)} "
                f"preflight={observation.preflight_passed}"
            )
        console.print()


def summarize(results: dict[str, list[CaseRunResult]]) -> dict[str, object]:
    """生成 report JSON 的 summary 段。"""
    by_contract: dict[str, dict[str, object]] = {}
    total = 0
    passed = 0
    runtime_errors = 0
    for contract_id, items in results.items():
        contract_total = len(items)
        contract_passed = sum(1 for item in items if item.verdict.passed)
        contract_errors = sum(1 for item in items if item.trace.error is not None)
        total += contract_total
        passed += contract_passed
        runtime_errors += contract_errors
        by_contract[contract_id] = {
            "total": contract_total,
            "passed": contract_passed,
            "failed": contract_total - contract_passed,
            "runtime_errors": contract_errors,
            "pass_rate": (contract_passed / contract_total) if contract_total else 0,
        }
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "runtime_errors": runtime_errors,
        "pass_rate": (passed / total) if total else 0,
        "by_contract": by_contract,
    }
