"""把 :class:`CaseOutcome` 渲染到终端：逐题展示（``print_outcome``）+ 全量汇总（``print_summary``）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from collections.abc import Iterable

    from memory_eval.harness.runner import CaseOutcome, QuestionOutcome

__all__ = ["print_outcome", "print_summary"]

_console = Console()


def print_outcome(outcome: CaseOutcome) -> None:
    """逐题打印：问题 / 标准答案 / 召回到的记忆条目 / 判分（若有）。"""
    _console.rule(
        f"[bold]{outcome.sample_id}[/bold] · {outcome.n_turns} turns · "
        f"{len(outcome.outcomes)} questions"
    )
    for i, qo in enumerate(outcome.outcomes, 1):
        category = qo.question.category or "?"
        _console.print(
            f"[bold cyan]Q{i}[/bold cyan] [dim]({category})[/dim] {qo.question.question}"
        )
        _console.print(f"  [dim]标准答案:[/dim] {qo.question.answer or '(无)'}")
        if not qo.recalled:
            _console.print("  [yellow]召回为空[/yellow]")
        else:
            _console.print(f"  [dim]召回 {len(qo.recalled)} 条:[/dim]")
            for item in qo.recalled:
                _console.print(
                    f"    - ({item.layer}) {item.text}",
                    markup=False,
                    highlight=False,
                )
        if qo.judge.correct is not None:
            mark = "[green]✓[/green]" if qo.judge.correct else "[red]✗[/red]"
            _console.print(f"  judge: {mark} {qo.judge.detail}")
        _console.print()


def print_summary(outcomes: Iterable[CaseOutcome]) -> None:
    """全部 case 跑完后输出 macro 平均 + 0 分错题清单。

    跳过 ``judge.score is None`` 的题（未判分，如无 anchor 的数据集），仅对已判分题
    做算术平均；0 分错题列出 sample_id / question / anchors / 召回到的记忆条目，便于
    人复盘"为什么完全没召回"。
    """
    scored: list[tuple[str, QuestionOutcome]] = []
    for outcome in outcomes:
        for qo in outcome.outcomes:
            if qo.judge.score is not None:
                scored.append((outcome.sample_id, qo))

    _console.rule("[bold]Summary[/bold]")
    if not scored:
        _console.print("[yellow]无可判分题（所有 judge.score 为 None），跳过汇总。[/yellow]")
        return

    macro = sum(qo.judge.score for _, qo in scored if qo.judge.score is not None) / len(scored)
    zeros = [(sid, qo) for sid, qo in scored if qo.judge.score == 0.0]
    _console.print(
        f"[bold]Macro 平均:[/bold] {macro:.3f}（基于 {len(scored)} 道有效题；其中 0 分 {len(zeros)} 道）"
    )

    if not zeros:
        return
    _console.print()
    _console.print(f"[bold red]0 分错题清单（{len(zeros)} 道）[/bold red]")
    for sid, qo in zeros:
        _console.print(f"  [bold]{sid}[/bold] · {qo.question.question}")
        _console.print(f"    anchors: {qo.question.anchors}")
        if not qo.recalled:
            _console.print("    [yellow]召回为空[/yellow]")
        else:
            _console.print(f"    召回 {len(qo.recalled)} 条:")
            for item in qo.recalled:
                _console.print(
                    f"      - ({item.layer}) {item.text}",
                    markup=False,
                    highlight=False,
                )
