"""把 :class:`CaseOutcome` 渲染到终端（PoC：展示召回内容供肉眼评估）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from memory_eval.harness.runner import CaseOutcome

__all__ = ["print_outcome"]

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
