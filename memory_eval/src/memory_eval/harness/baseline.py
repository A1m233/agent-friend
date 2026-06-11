"""把一次评测产出的结果序列化到 ``memory_eval/baselines/<ISO-datetime>-<short-sha>.json``。

baseline 不是评测的"被验收对象"——它是"用来对比的数字"：跑前 / 跑后看 macro 平均是否
变化、错题集合是否变化。文件命名带 commit short-sha，让数字能反查到当时的代码状态。

行为细节见 012 design §6。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from memory_eval.harness.runner import CaseOutcome

__all__ = ["BaselineRun", "write"]

_SCHEMA_VERSION = 1
_GIT_TIMEOUT_SEC = 5


@dataclass(frozen=True)
class BaselineRun:
    """一次评测运行的元数据（baseline 文件的 ``run`` 段）。"""

    started_at: datetime
    dataset: str
    model: str
    limit_samples: int
    limit_questions: int


def _short_sha() -> str:
    """取当前 HEAD 的短 sha；非 git 环境 / git 不可用时降级为 ``"nogit"``。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "nogit"
    if result.returncode != 0:
        return "nogit"
    sha = result.stdout.strip()
    return sha or "nogit"


def _filename(started_at: datetime, sha: str) -> str:
    """``2026-06-11T14-30-22-c8735f0.json``：ISO 时间 + 短 sha；冒号替成 ``-`` 保证 Windows 安全。"""
    stamp = started_at.strftime("%Y-%m-%dT%H-%M-%S")
    return f"{stamp}-{sha}.json"


def write(
    outcomes: Iterable[CaseOutcome],
    run: BaselineRun,
    out_dir: Path,
) -> Path:
    """把 ``outcomes`` 序列化成 baseline JSON 写入 ``out_dir``，返回写入路径。

    JSON 结构见 012 design §6.2：``schema_version`` / ``run`` / ``macro`` / ``per_question``。
    每题只存 ``recalled_count`` 不存召回全文（baseline 是对比用的数字、不是重放快照）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    sha = _short_sha()
    path = out_dir / _filename(run.started_at, sha)

    per_question: list[dict[str, object]] = []
    scores: list[float] = []
    n_total = 0
    for outcome in outcomes:
        for qo in outcome.outcomes:
            n_total += 1
            score = qo.judge.score
            if score is not None:
                scores.append(score)
            per_question.append(
                {
                    "sample_id": outcome.sample_id,
                    "question": qo.question.question,
                    "anchors": list(qo.question.anchors),
                    "score": score,
                    "detail": qo.judge.detail,
                    "recalled_count": len(qo.recalled),
                }
            )

    n_scored = len(scores)
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "run": {
            "started_at": run.started_at.isoformat(),
            "git_commit": sha,
            "dataset": run.dataset,
            "model": run.model,
            "limit_samples": run.limit_samples,
            "limit_questions": run.limit_questions,
        },
        "macro": {
            "score_mean": (sum(scores) / n_scored) if n_scored else None,
            "n_questions_total": n_total,
            "n_questions_scored": n_scored,
            "n_questions_zero": sum(1 for s in scores if s == 0.0),
        },
        "per_question": per_question,
    }

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
