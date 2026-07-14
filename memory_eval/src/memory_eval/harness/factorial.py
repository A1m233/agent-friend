"""E0/E1 × R0/R1 终局实验的配对语料与统计报告。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from memory import (
    ExtractionAttempt,
    ExtractionOutput,
    Extractor,
    Reconciler,
    SemanticOp,
    SqliteMemoryStore,
)
from memory_eval.adapters import case_fragments

if TYPE_CHECKING:
    from llm_providers import LLMClient
    from memory import ConversationFragment
    from memory_eval.datasets import EvalCase

    from .runner import CaseOutcome

__all__ = ["build_factorial_corpora", "summarize_factorial"]


def build_factorial_corpora(
    cases: list[EvalCase],
    llm_client: LLMClient,
    *,
    variants: dict[str, str],
    output_dir: Path,
    run_metadata: dict[str, object],
    resume: bool = False,
) -> dict[str, object]:
    """AB/BA 抽取两份独立 corpus；输出先 checkpoint，再可重复重建 DB。"""
    if len(variants) != 2:
        raise ValueError("factorial corpus 必须且只能提供两个 extraction variant")
    baseline_name, candidate_name = variants
    names = (baseline_name, candidate_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "schema_version": 1,
        "sample_ids": [case.sample_id for case in cases],
        "fragment_counts": [len(case_fragments(case)) for case in cases],
        "variants": {
            name: {"prompt_sha256": _text_hash(prompt)} for name, prompt in variants.items()
        },
        "run_metadata": run_metadata,
    }
    _lock_config(output_dir / "config.json", config)

    captured: dict[str, list[ExtractionAttempt]] = {name: [] for name in names}
    extractors = {
        name: Extractor(
            llm_client,
            prompt=prompt,
            on_attempt=captured[name].append,
            normalize_metadata=name != "E0",
        )
        for name, prompt in variants.items()
    }
    total_records = 0
    for case_index, case in enumerate(cases):
        case_dir = output_dir / "cases" / f"case-{case_index:03d}"
        attempts_path = case_dir / "attempts.jsonl"
        marker_path = case_dir / "complete.json"
        if attempts_path.exists() and not resume:
            raise RuntimeError(
                f"factorial 抽取输出已存在；请使用 --resume 或换 workspace: {attempts_path}"
            )
        records = _load_records(attempts_path)
        total_records += len(records)
        fragments = case_fragments(case)
        _validate_records(
            records, case_index=case_index, fragment_count=len(fragments), names=names
        )
        stores = {
            name: _rebuild_store(case_dir / name / "memory.db", records, name, fragments)
            for name in names
        }
        completed = {(int(record["fragment_index"]), str(record["variant"])) for record in records}
        try:
            for fragment_index, fragment in enumerate(fragments):
                order = names if (case_index + fragment_index) % 2 == 0 else tuple(reversed(names))
                for order_position, name in enumerate(order, start=1):
                    if (fragment_index, name) in completed:
                        continue
                    existing = stores[name].related_semantic(
                        owner_user_id=fragment.owner_user_id,
                        persona_id=fragment.persona_id,
                    )
                    before = len(captured[name])
                    output = extractors[name].extract(fragment, existing)
                    if len(captured[name]) != before + 1:
                        raise RuntimeError("抽取 attempt observer 未收到本次调用结果")
                    record = {
                        "case_index": case_index,
                        "sample_id": case.sample_id,
                        "fragment_index": fragment_index,
                        "fragment_id": _fragment_id(case.sample_id, fragment),
                        "variant": name,
                        "order_position": order_position,
                        "attempt": dataclasses.asdict(captured[name][-1]),
                        "output": dataclasses.asdict(output),
                        "output_sha256": _output_hash(output),
                    }
                    _append_jsonl(attempts_path, record)
                    records.append(record)
                    total_records += 1
                    if not output.is_noop():
                        Reconciler(stores[name], normalize_metadata=name != "E0").apply(
                            output, fragment, existing
                        )
                    completed.add((fragment_index, name))
        finally:
            for store in stores.values():
                store.close()
        expected = len(fragments) * len(names)
        if len(records) != expected:
            raise RuntimeError(
                f"case {case_index} 抽取记录不完整: actual={len(records)} expected={expected}"
            )
        _write_json_atomic(
            marker_path,
            {
                "sample_id": case.sample_id,
                "complete": True,
                "records": len(records),
                "variant_statuses": {
                    name: dict(
                        sorted(
                            Counter(
                                str(item["attempt"]["status"])
                                for item in records
                                if item["variant"] == name
                            ).items()
                        )
                    )
                    for name in names
                },
            },
        )
    summary = {
        "cases": len(cases),
        "variants": list(names),
        "expected_calls": sum(len(case_fragments(case)) for case in cases) * len(names),
        "completed_calls": total_records,
    }
    _write_json_atomic(output_dir / "extraction-summary.json", summary)
    return summary


def summarize_factorial(
    outcomes: dict[str, list[CaseOutcome]],
    *,
    baseline_condition: str = "E0R0",
    full_condition: str = "E1R1",
    historical_floor: float = 0.3783,
    bootstrap_samples: int = 10_000,
) -> dict[str, object]:
    """按题配对汇总四条件，并计算完整终态相对 baseline 的 bootstrap CI。"""
    if set(outcomes) != {"E0R0", "E0R1", "E1R0", "E1R1"}:
        raise ValueError("factorial report 必须包含 E0R0/E0R1/E1R0/E1R1")
    flattened = {name: _flatten_scores(items) for name, items in outcomes.items()}
    lengths = {len(values) for values in flattened.values()}
    if len(lengths) != 1:
        raise ValueError("四条件题目数量不一致，不能做配对比较")
    summaries = {
        name: {
            "questions": len(values),
            "macro": sum(values) / len(values) if values else 0.0,
            "zero_count": sum(value == 0 for value in values),
            "zero_rate": sum(value == 0 for value in values) / len(values) if values else 0.0,
        }
        for name, values in flattened.items()
    }
    deltas = [
        full - baseline
        for baseline, full in zip(
            flattened[baseline_condition], flattened[full_condition], strict=True
        )
    ]
    ci_low, ci_high = _paired_bootstrap_ci(deltas, samples=bootstrap_samples)
    gates = {
        "extraction_non_regression": summaries["E1R0"]["macro"] >= summaries["E0R0"]["macro"],
        "retrieval_strict_improvement": summaries["E0R1"]["macro"] > summaries["E0R0"]["macro"],
        "full_strict_improvement": summaries["E1R1"]["macro"] > summaries["E0R0"]["macro"],
        "historical_floor": summaries["E1R1"]["macro"] > historical_floor,
        "zero_rate_improvement": summaries["E1R1"]["zero_rate"] < summaries["E0R0"]["zero_rate"],
        "paired_ci_positive": ci_low > 0,
    }
    return {
        "conditions": summaries,
        "full_vs_baseline": {
            "mean_delta": sum(deltas) / len(deltas) if deltas else 0.0,
            "paired_bootstrap_95_ci": [ci_low, ci_high],
            "improved_questions": sum(delta > 0 for delta in deltas),
            "regressed_questions": sum(delta < 0 for delta in deltas),
            "unchanged_questions": sum(delta == 0 for delta in deltas),
        },
        "gates": gates,
        "all_gates_passed": all(gates.values()),
    }


def factorial_db_path(output_dir: Path, case_index: int, variant: str) -> Path:
    return output_dir / "cases" / f"case-{case_index:03d}" / variant / "memory.db"


def _rebuild_store(
    db_path: Path,
    records: list[dict[str, Any]],
    variant: str,
    fragments: list[ConversationFragment],
) -> SqliteMemoryStore:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if path.is_file():
            path.unlink()
    store = SqliteMemoryStore(db_path)
    for record in sorted(
        (item for item in records if item["variant"] == variant),
        key=lambda item: int(item["fragment_index"]),
    ):
        fragment = fragments[int(record["fragment_index"])]
        output = _deserialize_output(record["output"])
        if output.is_noop():
            continue
        existing = store.related_semantic(
            owner_user_id=fragment.owner_user_id,
            persona_id=fragment.persona_id,
        )
        Reconciler(store, normalize_metadata=variant != "E0").apply(output, fragment, existing)
    return store


def _deserialize_output(payload: dict[str, Any]) -> ExtractionOutput:
    return ExtractionOutput(
        episodic_entries=[str(item) for item in payload.get("episodic_entries", [])],
        semantic_ops=[SemanticOp(**item) for item in payload.get("semantic_ops", [])],
    )


def _validate_records(
    records: list[dict[str, Any]],
    *,
    case_index: int,
    fragment_count: int,
    names: tuple[str, str],
) -> None:
    seen: set[tuple[int, str]] = set()
    for record in records:
        key = (int(record["fragment_index"]), str(record["variant"]))
        if int(record["case_index"]) != case_index:
            raise RuntimeError("factorial checkpoint case_index 不匹配")
        if key in seen or key[0] < 0 or key[0] >= fragment_count or key[1] not in names:
            raise RuntimeError(f"factorial checkpoint 记录非法或重复: {key}")
        seen.add(key)


def _flatten_scores(outcomes: list[CaseOutcome]) -> list[float]:
    return [
        float(question.judge.score)
        for case in outcomes
        for question in case.outcomes
        if question.judge.score is not None
    ]


def _paired_bootstrap_ci(deltas: list[float], *, samples: int) -> tuple[float, float]:
    if not deltas:
        return 0.0, 0.0
    rng = random.Random(48)
    size = len(deltas)
    means = sorted(
        sum(deltas[rng.randrange(size)] for _ in range(size)) / size for _ in range(samples)
    )
    return means[int(samples * 0.025)], means[min(samples - 1, int(samples * 0.975))]


def _fragment_id(sample_id: str, fragment: ConversationFragment) -> str:
    material = json.dumps(
        {
            "sample_id": sample_id,
            "utterances": [dataclasses.asdict(item) for item in fragment.utterances],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _text_hash(material)[:16]


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _output_hash(output: ExtractionOutput) -> str:
    return _text_hash(
        json.dumps(
            dataclasses.asdict(output),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"factorial checkpoint 损坏: {path}:{line_number}") from exc
    return records


def _lock_config(path: Path, config: dict[str, object]) -> None:
    if path.is_file():
        actual = json.loads(path.read_text(encoding="utf-8"))
        if actual != config:
            raise RuntimeError("factorial 配置与 checkpoint 不一致，拒绝混用")
        return
    _write_json_atomic(path, config)


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
