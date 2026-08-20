"""两种抽取契约的 AB/BA 配对诊断；每次真实调用即时 checkpoint。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jieba

from memory import Extractor

from .stability import select_stability_fragments

if TYPE_CHECKING:
    from llm_providers import LanguageModel
    from memory import ConversationFragment, ExtractionAttempt, ExtractionOutput
    from memory_eval.datasets import EvalCase

__all__ = ["run_paired_extraction_probe"]

_GENERIC_TOKENS = frozenset(
    {"用户", "助手", "喜欢", "记住", "知道", "已经", "还是", "一个", "这个", "那个"}
)


def run_paired_extraction_probe(
    cases: list[EvalCase],
    llm_client: LanguageModel,
    *,
    variants: dict[str, str],
    output_dir: Path,
    fragment_count: int,
    repeats: int,
    run_metadata: dict[str, object],
    resume: bool = False,
) -> dict[str, object]:
    """在相同 fragment 上交错运行两份 prompt，隔离抽取契约净影响。"""
    if len(variants) != 2:
        raise ValueError("配对抽取必须且只能提供两个 variant")
    if fragment_count <= 0 or repeats <= 0:
        raise ValueError("fragment_count 与 repeats 必须大于 0")

    baseline_name, candidate_name = variants
    names = (baseline_name, candidate_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    attempts_path = output_dir / "attempts.jsonl"
    if attempts_path.exists() and not resume:
        raise RuntimeError(f"配对抽取输出已存在；请使用 --resume 或换 workspace: {attempts_path}")

    selected = select_stability_fragments(cases, fragment_count)
    config = {
        "schema_version": 1,
        "fragment_count": fragment_count,
        "repeats": repeats,
        "probe_ids": [_probe_id(sample_id, fragment) for sample_id, fragment in selected],
        "variants": {
            name: {"prompt_sha256": _text_hash(prompt)} for name, prompt in variants.items()
        },
        "run_metadata": run_metadata,
    }
    records = _load_records(attempts_path)
    _lock_config(output_dir / "config.json", config, resume=resume, has_records=bool(records))
    completed = {
        (str(item["probe_id"]), int(item["repeat"]), str(item["variant"])) for item in records
    }

    captured: dict[str, list[ExtractionAttempt]] = {name: [] for name in names}
    extractors = {
        name: Extractor(llm_client, prompt=prompt, on_attempt=captured[name].append)
        for name, prompt in variants.items()
    }
    for fragment_index, (sample_id, fragment) in enumerate(selected):
        probe_id = _probe_id(sample_id, fragment)
        source_tokens = _specific_tokens(fragment)
        for repeat in range(1, repeats + 1):
            order = names if (fragment_index + repeat) % 2 else tuple(reversed(names))
            for order_position, name in enumerate(order, start=1):
                key = (probe_id, repeat, name)
                if key in completed:
                    continue
                before = len(captured[name])
                output = extractors[name].extract(fragment, [])
                if len(captured[name]) != before + 1:
                    raise RuntimeError("抽取 attempt observer 未收到本次调用结果")
                preserved = _preserved_tokens(source_tokens, output)
                record = {
                    "probe_id": probe_id,
                    "sample_id": sample_id,
                    "repeat": repeat,
                    "variant": name,
                    "order_position": order_position,
                    "attempt": dataclasses.asdict(captured[name][-1]),
                    "output": dataclasses.asdict(output),
                    "output_sha256": _output_hash(output),
                    "specific_tokens": list(source_tokens),
                    "preserved_specific_tokens": list(preserved),
                }
                _append_jsonl(attempts_path, record)
                records.append(record)
                completed.add(key)

    summary = _summarize(records, names=names, selected_count=len(selected), repeats=repeats)
    _write_json_atomic(output_dir / "summary.json", summary)
    return summary


def _summarize(
    records: list[dict[str, Any]],
    *,
    names: tuple[str, str],
    selected_count: int,
    repeats: int,
) -> dict[str, object]:
    by_variant = {name: [item for item in records if item["variant"] == name] for name in names}
    variants: dict[str, object] = {}
    for name, items in by_variant.items():
        statuses = Counter(str(item["attempt"]["status"]) for item in items)
        by_probe: dict[str, set[str]] = {}
        preserved = 0
        available = 0
        for item in items:
            by_probe.setdefault(str(item["probe_id"]), set()).add(str(item["output_sha256"]))
            preserved += len(item["preserved_specific_tokens"])
            available += len(item["specific_tokens"])
        variants[name] = {
            "completed_calls": len(items),
            "status_counts": dict(sorted(statuses.items())),
            "semantic_count": sum(
                int(item["attempt"].get("semantic_count") or 0) for item in items
            ),
            "episodic_count": sum(
                int(item["attempt"].get("episodic_count") or 0) for item in items
            ),
            "specific_token_preserved": preserved,
            "specific_token_available": available,
            "specific_token_recall": preserved / available if available else 1.0,
            "unstable_output_probes": sum(len(hashes) > 1 for hashes in by_probe.values()),
        }

    pairs: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for item in records:
        pairs.setdefault((str(item["probe_id"]), int(item["repeat"])), {})[str(item["variant"])] = (
            item
        )
    complete_pairs = [pair for pair in pairs.values() if all(name in pair for name in names)]
    baseline, candidate = names
    regressions = []
    token_deltas = []
    for pair in complete_pairs:
        before, after = pair[baseline], pair[candidate]
        before_success = before["attempt"]["status"] == "success"
        after_success = after["attempt"]["status"] == "success"
        before_recall = _record_token_recall(before)
        after_recall = _record_token_recall(after)
        token_deltas.append(after_recall - before_recall)
        if (before_success and not after_success) or after_recall < before_recall:
            regressions.append(
                {
                    "probe_id": after["probe_id"],
                    "repeat": after["repeat"],
                    "baseline_status": before["attempt"]["status"],
                    "candidate_status": after["attempt"]["status"],
                    "specific_token_recall_delta": after_recall - before_recall,
                }
            )
    return {
        "selected_fragments": selected_count,
        "repeats": repeats,
        "expected_calls": selected_count * repeats * len(names),
        "completed_calls": len(records),
        "variants": variants,
        "paired": {
            "baseline_variant": baseline,
            "candidate_variant": candidate,
            "complete_pairs": len(complete_pairs),
            "baseline_success_candidate_not": sum(
                pair[baseline]["attempt"]["status"] == "success"
                and pair[candidate]["attempt"]["status"] != "success"
                for pair in complete_pairs
            ),
            "candidate_success_baseline_not": sum(
                pair[candidate]["attempt"]["status"] == "success"
                and pair[baseline]["attempt"]["status"] != "success"
                for pair in complete_pairs
            ),
            "mean_specific_token_recall_delta": (
                sum(token_deltas) / len(token_deltas) if token_deltas else 0.0
            ),
            "regression_count": len(regressions),
            "regressions": regressions,
        },
    }


def _record_token_recall(record: dict[str, Any]) -> float:
    available = len(record["specific_tokens"])
    return len(record["preserved_specific_tokens"]) / available if available else 1.0


def _specific_tokens(fragment: ConversationFragment) -> tuple[str, ...]:
    text = "\n".join(utterance.text for utterance in fragment.utterances)
    result: list[str] = []
    seen: set[str] = set()
    for raw in jieba.cut(text):
        token = raw.strip().lower()
        if token in _GENERIC_TOKENS or token in seen:
            continue
        is_latin_or_number = bool(re.search(r"[a-z0-9]", token))
        is_chinese_word = len(token) >= 2 and bool(re.fullmatch(r"[\u4e00-\u9fff]+", token))
        if is_latin_or_number or is_chinese_word:
            seen.add(token)
            result.append(token)
    return tuple(result)


def _preserved_tokens(source_tokens: tuple[str, ...], output: ExtractionOutput) -> tuple[str, ...]:
    rendered = json.dumps(dataclasses.asdict(output), ensure_ascii=False).lower()
    return tuple(token for token in source_tokens if token in rendered)


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


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _output_hash(output: ExtractionOutput) -> str:
    payload = json.dumps(
        dataclasses.asdict(output), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _text_hash(payload)


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
            raise RuntimeError(f"配对抽取 checkpoint 损坏: {path}:{line_number}") from exc
    return records


def _lock_config(path: Path, config: dict[str, object], *, resume: bool, has_records: bool) -> None:
    if path.is_file():
        actual = json.loads(path.read_text(encoding="utf-8"))
        if actual != config:
            raise RuntimeError(
                f"配对抽取配置与 checkpoint 不一致，拒绝混用: stored={actual} requested={config}"
            )
        return
    if resume and has_records:
        raise RuntimeError("配对抽取 checkpoint 缺少 config.json，无法安全续跑")
    _write_json_atomic(path, config)


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
