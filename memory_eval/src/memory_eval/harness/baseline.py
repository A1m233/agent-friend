"""把一次评测产出的结果序列化到 ``memory_eval/baselines/<ISO-datetime>-<short-sha>.json``。

baseline 不是评测的"被验收对象"——它是"用来对比的数字"：跑前 / 跑后看 macro 平均是否
变化、错题集合是否变化、召回内容是否变化。schema 完整覆盖：

- 运行条件（commit / dirty / dataset 文件 hash / provider 参数 / note）
- 单题维度（gold answer / anchors / 分数 / 完整召回）
- 全局维度（macro / 总耗时）

行为细节见 012 design §6。schema_version 当前为 2；schema 1 是 hotfix 前的"减信息版"。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from memory_eval.harness.runner import CaseOutcome

__all__ = ["BaselineRun", "write"]

_SCHEMA_VERSION = 2
_GIT_TIMEOUT_SEC = 5
_HASH_CHUNK = 65536


@dataclass(frozen=True)
class BaselineRun:
    """一次评测运行的元数据（baseline 文件的 ``run`` 段）。"""

    started_at: datetime
    ended_at: datetime
    dataset: str
    model: str
    api_base: str | None
    provider_defaults: Mapping[str, Any]
    limit_samples: int
    limit_questions: int
    note: str = ""
    dataset_paths: tuple[Path, ...] = field(default_factory=tuple)


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


def _working_tree_dirty() -> bool | None:
    """working tree 有未提交改动则 True；干净 False；非 git 环境 None。

    用 ``git status --porcelain`` 同时覆盖 unstaged / staged / 未跟踪文件。
    None 让读 baseline 的人能区分"干净"和"无法判定"。
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def _hash_file(path: Path) -> tuple[str | None, int | None]:
    """``(sha256_hex, bytes)``；文件不存在或不可读返回 ``(None, None)``。"""
    if not path.exists() or not path.is_file():
        return None, None
    h = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
                h.update(chunk)
                size += len(chunk)
    except OSError:
        return None, None
    return h.hexdigest(), size


def _dataset_files_meta(paths: Sequence[Path]) -> list[dict[str, object]]:
    """对每个数据集文件计算 sha256 + 字节数；只用 basename 入仓避免泄漏本地路径。"""
    items: list[dict[str, object]] = []
    for p in paths:
        sha, size = _hash_file(p)
        items.append({"path": p.name, "sha256": sha, "bytes": size})
    return items


def _filename(started_at: datetime, sha: str) -> str:
    """``2026-06-11T14-30-22-c8735f0.json``：ISO 时间 + 短 sha；冒号替成 ``-`` 保证 Windows 安全。"""
    stamp = started_at.strftime("%Y-%m-%dT%H-%M-%S")
    return f"{stamp}-{sha}.json"


def _serialize_recalled(items: Iterable[Any]) -> list[dict[str, object]]:
    """把 ``MemoryItem`` 列表序列化成 baseline 友好的 dict 列表。

    用 ``dataclasses.asdict`` 走通用路径，避免硬编码字段名与 ``MemoryItem`` 演进绑死。
    遇到非 dataclass（理论上不该发生）退化成 ``str(item)``，保证不抛。
    """
    out: list[dict[str, object]] = []
    for item in items:
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            out.append(dataclasses.asdict(item))
        else:
            out.append({"repr": str(item)})
    return out


def write(
    outcomes: Iterable[CaseOutcome],
    run: BaselineRun,
    out_dir: Path,
) -> Path:
    """把 ``outcomes`` 序列化成 baseline JSON 写入 ``out_dir``，返回写入路径。

    JSON 结构见 012 design §6.2（schema_version=2）：``schema_version`` / ``run`` /
    ``macro`` / ``per_question``，每题含完整 ``recalled`` 内容，便于离线 / 后续再判分。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    sha = _short_sha()
    dirty = _working_tree_dirty()
    path = out_dir / _filename(run.started_at, sha)

    per_question: list[dict[str, object]] = []
    scores: list[float] = []
    n_total = 0
    for outcome in outcomes:
        for idx, qo in enumerate(outcome.outcomes):
            n_total += 1
            score = qo.judge.score
            if score is not None:
                scores.append(score)
            per_question.append(
                {
                    "question_id": f"{outcome.sample_id}#q{idx}",
                    "sample_id": outcome.sample_id,
                    "question": qo.question.question,
                    "answer": qo.question.answer,
                    "anchors": list(qo.question.anchors),
                    "score": score,
                    "detail": qo.judge.detail,
                    "recalled_count": len(qo.recalled),
                    "recalled": _serialize_recalled(qo.recalled),
                }
            )

    n_scored = len(scores)
    duration = (run.ended_at - run.started_at).total_seconds()
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "run": {
            "started_at": run.started_at.isoformat(),
            "ended_at": run.ended_at.isoformat(),
            "duration_seconds": round(duration, 3),
            "git_commit": sha,
            "working_tree_dirty": dirty,
            "dataset": run.dataset,
            "limit_samples": run.limit_samples,
            "limit_questions": run.limit_questions,
            "note": run.note,
            "provider": {
                "model": run.model,
                "api_base": run.api_base,
                "defaults": dict(run.provider_defaults),
            },
            "dataset_files": _dataset_files_meta(run.dataset_paths),
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
