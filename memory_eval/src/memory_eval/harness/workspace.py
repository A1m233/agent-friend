"""可恢复的 memory_eval 工作区：持久 case DB 与逐 case 结果 checkpoint。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory import ExtractionAttempt, MemoryItem
from memory_eval.datasets import EvalQuestion
from memory_eval.harness.judge import JudgeResult
from memory_eval.harness.runner import CaseOutcome, QuestionOutcome

__all__ = ["EvalWorkspace", "WorkspaceManifest"]

_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class WorkspaceManifest:
    dataset: str
    sample_ids: tuple[str, ...]
    model: str
    api_base: str | None
    provider_defaults: dict[str, object]
    extraction_keep_specifics: bool
    pinned_relevance_gate: bool
    dataset_files: tuple[dict[str, object], ...]
    extraction_code_fingerprint: str | None = None

    @classmethod
    def build(
        cls,
        *,
        dataset: str,
        sample_ids: list[str],
        model: str,
        api_base: str | None,
        provider_defaults: dict[str, object],
        extraction_keep_specifics: bool,
        pinned_relevance_gate: bool,
        dataset_paths: tuple[Path, ...],
        extraction_code_fingerprint: str | None = None,
    ) -> WorkspaceManifest:
        return cls(
            dataset=dataset,
            sample_ids=tuple(sample_ids),
            model=model,
            api_base=api_base,
            provider_defaults=provider_defaults,
            extraction_keep_specifics=extraction_keep_specifics,
            pinned_relevance_gate=pinned_relevance_gate,
            dataset_files=tuple(_file_meta(path) for path in dataset_paths),
            extraction_code_fingerprint=extraction_code_fingerprint,
        )


class EvalWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.manifest_path = self.root / "manifest.json"

    def create(self, manifest: WorkspaceManifest) -> None:
        if self.root.exists() and any(self.root.iterdir()):
            raise RuntimeError(f"评测工作区非空；请使用 --resume 或换目录: {self.root}")
        self.root.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(
            self.manifest_path,
            {"schema_version": _SCHEMA_VERSION, "manifest": dataclasses.asdict(manifest)},
        )

    def load_manifest(self) -> WorkspaceManifest:
        if not self.manifest_path.is_file():
            raise RuntimeError(f"工作区缺少 manifest.json: {self.root}")
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _SCHEMA_VERSION:
            raise RuntimeError("不支持的 memory_eval workspace schema")
        raw = payload["manifest"]
        return WorkspaceManifest(
            dataset=str(raw["dataset"]),
            sample_ids=tuple(str(item) for item in raw["sample_ids"]),
            model=str(raw["model"]),
            api_base=str(raw["api_base"]) if raw.get("api_base") is not None else None,
            provider_defaults=dict(raw["provider_defaults"]),
            extraction_keep_specifics=bool(raw["extraction_keep_specifics"]),
            pinned_relevance_gate=bool(raw["pinned_relevance_gate"]),
            dataset_files=tuple(dict(item) for item in raw["dataset_files"]),
            extraction_code_fingerprint=(
                str(raw["extraction_code_fingerprint"])
                if raw.get("extraction_code_fingerprint") is not None
                else None
            ),
        )

    def assert_compatible(self, expected: WorkspaceManifest) -> WorkspaceManifest:
        actual = self.load_manifest()
        if actual != expected:
            raise RuntimeError(
                "工作区配置与本次运行不一致，拒绝混用抽取产物。\n"
                f"workspace={actual}\nrequested={expected}"
            )
        return actual

    def case_db(self, index: int) -> Path:
        return self.root / "cases" / f"case-{index:03d}" / "memory.db"

    def is_ingested(self, index: int, sample_id: str) -> bool:
        marker = self._ingest_marker(index)
        if not marker.is_file() or not self.case_db(index).is_file():
            return False
        payload = json.loads(marker.read_text(encoding="utf-8"))
        return payload.get("sample_id") == sample_id and payload.get("complete") is True

    def reset_incomplete_case(self, index: int) -> None:
        """只清理本工作区内固定命名的未完成 DB 文件。"""
        db = self.case_db(index)
        db.parent.mkdir(parents=True, exist_ok=True)
        known = (
            db,
            Path(f"{db}-wal"),
            Path(f"{db}-shm"),
            db.with_name(f"{db.stem}-vectors-512.db"),
            db.with_name(f"{db.stem}-vectors-512.db-wal"),
            db.with_name(f"{db.stem}-vectors-512.db-shm"),
        )
        for path in known:
            if path.is_file():
                path.unlink()
        marker = self._ingest_marker(index)
        if marker.is_file():
            marker.unlink()
        attempts = self.extraction_attempts_path(index)
        if attempts.is_file():
            attempts.unlink()
        summary = self.extraction_summary_path(index)
        if summary.is_file():
            summary.unlink()

    def mark_ingested(self, index: int, sample_id: str) -> dict[str, object]:
        summary = self.write_extraction_summary(index)
        _write_json_atomic(
            self._ingest_marker(index),
            {"sample_id": sample_id, "complete": True, "extraction_summary": summary},
        )
        return summary

    def extraction_attempts_path(self, index: int) -> Path:
        return self.case_db(index).parent / "extraction-attempts.jsonl"

    def extraction_summary_path(self, index: int) -> Path:
        return self.case_db(index).parent / "extraction-summary.json"

    def append_extraction_attempt(self, index: int, attempt: ExtractionAttempt) -> None:
        """逐 fragment 追加诊断记录；每条完成即 flush，避免 case 末尾才落盘。"""
        path = self.extraction_attempts_path(index)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(dataclasses.asdict(attempt), ensure_ascii=False)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()

    def load_extraction_attempts(self, index: int) -> list[ExtractionAttempt]:
        path = self.extraction_attempts_path(index)
        if not path.is_file():
            return []
        attempts: list[ExtractionAttempt] = []
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                attempts.append(ExtractionAttempt(**json.loads(line)))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"抽取诊断记录损坏: {path}:{line_number}") from exc
        return attempts

    def write_extraction_summary(self, index: int) -> dict[str, object]:
        attempts = self.load_extraction_attempts(index)
        statuses = Counter(attempt.status for attempt in attempts)
        summary: dict[str, object] = {
            "attempt_count": len(attempts),
            "status_counts": dict(sorted(statuses.items())),
            "empty_or_error_count": sum(attempt.status != "success" for attempt in attempts),
            "episodic_count": sum(attempt.episodic_count for attempt in attempts),
            "semantic_count": sum(attempt.semantic_count for attempt in attempts),
            "duration_ms": sum(attempt.duration_ms for attempt in attempts),
        }
        _write_json_atomic(self.extraction_summary_path(index), summary)
        return summary

    def save_outcome(
        self,
        index: int,
        outcome: CaseOutcome,
        *,
        result_key: str,
    ) -> Path:
        path = self._result_path(index, result_key)
        _write_json_atomic(path, _serialize_outcome(outcome))
        return path

    def load_outcome(self, index: int, *, result_key: str) -> CaseOutcome | None:
        path = self._result_path(index, result_key)
        if not path.is_file():
            return None
        return _deserialize_outcome(json.loads(path.read_text(encoding="utf-8")))

    def _ingest_marker(self, index: int) -> Path:
        return self.case_db(index).parent / "ingested.json"

    def _result_path(self, index: int, result_key: str) -> Path:
        return self.root / "results" / result_key / f"case-{index:03d}.json"


def _serialize_outcome(outcome: CaseOutcome) -> dict[str, object]:
    return {
        "sample_id": outcome.sample_id,
        "n_turns": outcome.n_turns,
        "retrieval_mode": outcome.retrieval_mode,
        "index_state": outcome.index_state,
        "outcomes": [
            {
                "question": dataclasses.asdict(item.question),
                "recalled": [dataclasses.asdict(recalled) for recalled in item.recalled],
                "rendered": item.rendered,
                "judge": dataclasses.asdict(item.judge),
                "trace": item.trace,
            }
            for item in outcome.outcomes
        ],
    }


def _deserialize_outcome(payload: dict[str, Any]) -> CaseOutcome:
    outcomes = [
        QuestionOutcome(
            question=EvalQuestion(**item["question"]),
            recalled=[MemoryItem(**recalled) for recalled in item["recalled"]],
            rendered=str(item["rendered"]),
            judge=JudgeResult(**item["judge"]),
            trace=item.get("trace"),
        )
        for item in payload["outcomes"]
    ]
    return CaseOutcome(
        sample_id=str(payload["sample_id"]),
        n_turns=int(payload["n_turns"]),
        outcomes=outcomes,
        retrieval_mode=payload["retrieval_mode"],
        index_state=payload.get("index_state"),
    )


def _file_meta(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"path": path.name, "sha256": None, "bytes": None}
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"path": path.name, "sha256": digest.hexdigest(), "bytes": size}


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
