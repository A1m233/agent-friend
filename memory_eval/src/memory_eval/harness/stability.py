"""固定 fragment 的抽取稳定性 probe；每次调用即时 checkpoint。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from memory import ExtractionAttempt, Extractor
from memory_eval.adapters import case_fragments

if TYPE_CHECKING:
    from llm_providers import LanguageModel
    from memory import ConversationFragment, ExtractionOutput
    from memory_eval.datasets import EvalCase

__all__ = ["run_stability_probe", "select_stability_fragments"]


def select_stability_fragments(
    cases: list[EvalCase], limit: int
) -> list[tuple[str, ConversationFragment]]:
    """按稳定 hash 取样，使同一数据集与 limit 每次选择完全一致。"""
    candidates = [(case.sample_id, fragment) for case in cases for fragment in case_fragments(case)]
    ranked = sorted(candidates, key=lambda item: _probe_id(*item))
    return ranked[:limit]


def run_stability_probe(
    cases: list[EvalCase],
    llm_client: LanguageModel,
    *,
    output_dir: Path,
    fragment_count: int,
    repeats: int,
    resume: bool = False,
) -> dict[str, object]:
    """重复抽取固定 fragment，不落 memory DB，隔离观察模型/解析稳定性。"""
    if fragment_count <= 0 or repeats <= 0:
        raise ValueError("fragment_count 与 repeats 必须大于 0")
    output_dir.mkdir(parents=True, exist_ok=True)
    attempts_path = output_dir / "attempts.jsonl"
    if attempts_path.exists() and not resume:
        raise RuntimeError(f"稳定性输出已存在；请使用 --resume 或换 workspace: {attempts_path}")

    records = _load_records(attempts_path)
    completed = {(str(item["probe_id"]), int(item["repeat"])) for item in records}
    selected = select_stability_fragments(cases, fragment_count)
    config = {
        "fragment_count": fragment_count,
        "repeats": repeats,
        "probe_ids": [_probe_id(sample_id, fragment) for sample_id, fragment in selected],
    }
    _lock_config(output_dir / "config.json", config, resume=resume, has_records=bool(records))

    captured: list[ExtractionAttempt] = []
    extractor = Extractor(llm_client, on_attempt=captured.append)
    for sample_id, fragment in selected:
        probe_id = _probe_id(sample_id, fragment)
        for repeat in range(1, repeats + 1):
            if (probe_id, repeat) in completed:
                continue
            before = len(captured)
            output = extractor.extract(fragment, [])
            if len(captured) != before + 1:
                raise RuntimeError("抽取 attempt observer 未收到本次调用结果")
            record = {
                "probe_id": probe_id,
                "sample_id": sample_id,
                "repeat": repeat,
                "attempt": dataclasses.asdict(captured[-1]),
                "output": dataclasses.asdict(output),
                "output_sha256": _output_hash(output),
            }
            _append_jsonl(attempts_path, record)
            records.append(record)
            completed.add((probe_id, repeat))

    summary = _summarize(records, selected_count=len(selected), repeats=repeats)
    _write_json_atomic(output_dir / "summary.json", summary)
    return summary


def _lock_config(path: Path, config: dict[str, object], *, resume: bool, has_records: bool) -> None:
    if path.is_file():
        actual = json.loads(path.read_text(encoding="utf-8"))
        if actual != config:
            raise RuntimeError(
                "稳定性 probe 配置与 checkpoint 不一致，拒绝混用: "
                f"stored={actual} requested={config}"
            )
        return
    if resume and has_records:
        raise RuntimeError("稳定性 checkpoint 缺少 config.json，无法安全续跑")
    _write_json_atomic(path, config)


def _probe_id(sample_id: str, fragment: ConversationFragment) -> str:
    material = json.dumps(
        {
            "sample_id": sample_id,
            "utterances": [
                {
                    "speaker": utterance.speaker,
                    "text": utterance.text,
                    "ts": utterance.ts.isoformat(),
                    "source_ref": utterance.source_ref,
                }
                for utterance in fragment.utterances
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _output_hash(output: ExtractionOutput) -> str:
    payload = json.dumps(
        dataclasses.asdict(output), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"稳定性 checkpoint 损坏: {path}:{line_number}") from exc
    return records


def _summarize(
    records: list[dict[str, Any]], *, selected_count: int, repeats: int
) -> dict[str, object]:
    statuses = Counter(str(item["attempt"]["status"]) for item in records)
    records_by_probe: dict[str, list[dict[str, Any]]] = {}
    records_by_repeat: dict[int, list[dict[str, Any]]] = {}
    for item in records:
        records_by_probe.setdefault(str(item["probe_id"]), []).append(item)
        records_by_repeat.setdefault(int(item["repeat"]), []).append(item)

    probe_statuses = {
        probe_id: {str(item["attempt"]["status"]) for item in probe_records}
        for probe_id, probe_records in records_by_probe.items()
    }
    output_hashes = {
        probe_id: {str(item["output_sha256"]) for item in probe_records}
        for probe_id, probe_records in records_by_probe.items()
    }
    per_repeat = []
    for repeat in range(1, repeats + 1):
        repeat_records = records_by_repeat.get(repeat, [])
        repeat_statuses = Counter(str(item["attempt"]["status"]) for item in repeat_records)
        per_repeat.append(
            {
                "repeat": repeat,
                "completed_calls": len(repeat_records),
                "status_counts": dict(sorted(repeat_statuses.items())),
                "semantic_count": sum(
                    int(item["attempt"].get("semantic_count") or 0) for item in repeat_records
                ),
                "episodic_count": sum(
                    int(item["attempt"].get("episodic_count") or 0) for item in repeat_records
                ),
            }
        )
    unstable_output_probes = sum(len(hashes) > 1 for hashes in output_hashes.values())
    return {
        "selected_fragments": selected_count,
        "repeats": repeats,
        "expected_calls": selected_count * repeats,
        "completed_calls": len(records),
        "status_counts": dict(sorted(statuses.items())),
        "completed_probes": len(records_by_probe),
        "stable_output_probes": sum(len(hashes) == 1 for hashes in output_hashes.values()),
        "unstable_output_probes": unstable_output_probes,
        # schema 兼容：旧消费者只认识 unstable_probes。
        "unstable_probes": unstable_output_probes,
        "three_distinct_output_probes": sum(len(hashes) >= 3 for hashes in output_hashes.values()),
        "always_empty_probes": sum(value == {"empty_output"} for value in probe_statuses.values()),
        "always_success_probes": sum(value == {"success"} for value in probe_statuses.values()),
        "mixed_status_probes": sum(len(value) > 1 for value in probe_statuses.values()),
        "per_repeat": per_repeat,
    }


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
