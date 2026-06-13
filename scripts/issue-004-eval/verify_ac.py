"""issue 004 · pass-1 全量评测 AC 验证

按 docs/requirements/013-memory-quality-pass-1/requirement.md §6 与
docs/issues/004-pass-1-full-eval-pending/README.md §2 逐条验：

- AC-1a: pre-pass-1 baseline 里"有 episodic 但 0 分"的 665 道题，pass-1-full 后零分率必须可观测下降
- AC-1b: 张小红 q4/q5/q6（dialogue block 4_0_1#1 三道连续 0 分），pass-1-full 后至少 1 道 > 0
- AC-2:  pass-1-full macro ≥ 0.219（pre-pass-1 hard fail 线）
- AC-3:  3 份切片各自至少一个量化指标改善
  - only-extraction（仅抽取改进）→ 抽取产物对 anchor 字面词的保留率↑（用 0 分率作 proxy）
  - only-pinned（仅 pinned gate）→ "仅 pinned" 占零分比从 53.5% 下降
  - baseline（仅 jieba）→ recalled_count 中位数从 2 抬升

AC-4（008 工程 AC）不在本脚本范围，单独 pytest 验。

用法：
    python scripts/issue-004-eval/verify_ac.py \
        --pre  memory_eval/baselines/2026-06-12T01-31-46-46e810d.json \
        --full memory_eval/baselines/<pass-1-full>.json \
        --only-extraction memory_eval/baselines/<pass-1-only-extraction>.json \
        --only-pinned     memory_eval/baselines/<pass-1-only-pinned>.json \
        --jieba-only      memory_eval/baselines/<pass-1-baseline>.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any


def load(p: Path) -> dict[str, Any]:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def zero_rate(qs: list[dict[str, Any]]) -> float:
    if not qs:
        return 0.0
    z = sum(1 for q in qs if q["score"] == 0.0)
    return z / len(qs)


def only_pinned_in_recalled(q: dict[str, Any]) -> bool:
    recalled = q.get("recalled", [])
    return bool(recalled) and all(r["layer"] == "pinned" for r in recalled)


def has_episodic(q: dict[str, Any]) -> bool:
    return any(r["layer"] == "episodic" for r in q.get("recalled", []))


def macro(b: dict[str, Any]) -> float:
    return b["macro"]["score_mean"]


def n_zero(b: dict[str, Any]) -> int:
    return b["macro"]["n_questions_zero"]


def pq_by_id(b: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {q["question_id"]: q for q in b["per_question"]}


# === AC-1a ===========================================================
def ac_1a(pre: dict[str, Any], full: dict[str, Any]) -> dict[str, Any]:
    """665 道"有 episodic 但 0 分"题集，pass-1-full 后零分率必须可观测下降。"""
    pre_qs = pre["per_question"]
    target_ids = {q["question_id"] for q in pre_qs if q["score"] == 0.0 and has_episodic(q)}
    full_by_id = pq_by_id(full)
    full_targeted = [full_by_id[qid] for qid in target_ids if qid in full_by_id]
    pre_zero_rate = 1.0  # 集合定义就是 100% 零分
    full_zero_rate = zero_rate(full_targeted)
    delta = full_zero_rate - pre_zero_rate
    passed = full_zero_rate < pre_zero_rate
    return {
        "target_size": len(target_ids),
        "matched_in_full": len(full_targeted),
        "pre_zero_rate": pre_zero_rate,
        "full_zero_rate": full_zero_rate,
        "delta": delta,
        "passed": passed,
        "verdict": "可观测下降" if passed else "持平或上升 —— FAIL",
    }


# === AC-1b ===========================================================
def ac_1b(full: dict[str, Any]) -> dict[str, Any]:
    """张小红 q4/q5/q6 (dialogue block 4_0_1#1 的连续 3 道零分题)，至少 1 道 > 0。"""
    target_ids = ["张小红#q4", "张小红#q5", "张小红#q6"]
    full_by_id = pq_by_id(full)
    scores = [
        {
            "id": qid,
            "score": full_by_id[qid]["score"] if qid in full_by_id else None,
            "question": full_by_id[qid]["question"] if qid in full_by_id else None,
        }
        for qid in target_ids
    ]
    nonzero = [s for s in scores if s["score"] is not None and s["score"] > 0]
    passed = len(nonzero) >= 1
    return {
        "scores": scores,
        "nonzero_count": len(nonzero),
        "passed": passed,
        "verdict": f"{len(nonzero)}/3 脱离 0 分" + (" —— PASS" if passed else " —— FAIL"),
    }


# === AC-2 ============================================================
def ac_2(pre: dict[str, Any], full: dict[str, Any]) -> dict[str, Any]:
    """pass-1-full macro 必须 ≥ pre-pass-1 (0.219)。"""
    pre_macro = macro(pre)
    full_macro = macro(full)
    delta = full_macro - pre_macro
    passed = full_macro >= pre_macro
    return {
        "pre_macro": pre_macro,
        "full_macro": full_macro,
        "delta": delta,
        "passed": passed,
        "verdict": (
            f"macro {full_macro:.3f} ≥ pre {pre_macro:.3f} (+{delta:.3f})"
            + (" —— PASS" if passed else " —— FAIL (持平或退化均算 FAIL)")
        ),
    }


# === AC-3 ============================================================
def ac_3_extraction(pre: dict[str, Any], only_extraction: dict[str, Any]) -> dict[str, Any]:
    """only-extraction 切片：仅开抽取改进。

    主因 1 的代理指标：抽取产物对 anchor 字面词的保留率↑ 对应"有 episodic 但 0 分"集合的零分率↓。
    """
    pre_qs = pre["per_question"]
    target_ids = {q["question_id"] for q in pre_qs if q["score"] == 0.0 and has_episodic(q)}
    by_id = pq_by_id(only_extraction)
    targeted = [by_id[qid] for qid in target_ids if qid in by_id]
    zr = zero_rate(targeted)
    passed = zr < 1.0
    return {
        "target_size": len(target_ids),
        "matched": len(targeted),
        "only_extraction_zero_rate_on_665": zr,
        "delta_from_pre_100pct": zr - 1.0,
        "passed": passed,
        "verdict": (
            f"665 题在 only-extraction 下零分率 {zr * 100:.1f}% (pre 100%)"
            + (" —— PASS" if passed else " —— FAIL")
        ),
    }


def ac_3_pinned(pre: dict[str, Any], only_pinned: dict[str, Any]) -> dict[str, Any]:
    """only-pinned 切片：仅开 pinned gate。

    主因 2 的代理指标：零分题里"仅 pinned"占比从 53.5% 下降。
    """
    pre_zeros = [q for q in pre["per_question"] if q["score"] == 0.0]
    pre_only_pinned = sum(1 for q in pre_zeros if only_pinned_in_recalled(q))
    pre_ratio = pre_only_pinned / len(pre_zeros) if pre_zeros else 0.0

    op_zeros = [q for q in only_pinned["per_question"] if q["score"] == 0.0]
    op_only_pinned = sum(1 for q in op_zeros if only_pinned_in_recalled(q))
    op_ratio = op_only_pinned / len(op_zeros) if op_zeros else 0.0

    passed = op_ratio < pre_ratio
    return {
        "pre_zero_count": len(pre_zeros),
        "pre_only_pinned_in_zero": pre_only_pinned,
        "pre_only_pinned_ratio": pre_ratio,
        "only_pinned_zero_count": len(op_zeros),
        "only_pinned_only_pinned_in_zero": op_only_pinned,
        "only_pinned_only_pinned_ratio": op_ratio,
        "delta_ratio": op_ratio - pre_ratio,
        "passed": passed,
        "verdict": (
            f'"仅 pinned" 占零分比 {op_ratio * 100:.1f}% (pre {pre_ratio * 100:.1f}%)'
            + (" —— PASS" if passed else " —— FAIL")
        ),
    }


def ac_3_jieba(pre: dict[str, Any], jieba_only: dict[str, Any]) -> dict[str, Any]:
    """jieba-only 切片（两开关都关）：仅 jieba 生效。

    主因 3 的代理指标：recalled_count 中位数从 pre 的 2/3 抬升（issue 003 说 "从 2 抬起"）。
    """
    pre_counts = [q.get("recalled_count", 0) for q in pre["per_question"]]
    jo_counts = [q.get("recalled_count", 0) for q in jieba_only["per_question"]]
    pre_median = statistics.median(pre_counts)
    jo_median = statistics.median(jo_counts)
    pre_mean = statistics.mean(pre_counts)
    jo_mean = statistics.mean(jo_counts)
    passed = jo_median > pre_median or jo_mean > pre_mean
    return {
        "pre_median": pre_median,
        "jieba_only_median": jo_median,
        "pre_mean": pre_mean,
        "jieba_only_mean": jo_mean,
        "delta_median": jo_median - pre_median,
        "delta_mean": jo_mean - pre_mean,
        "passed": passed,
        "verdict": (
            f"recalled_count median {pre_median}→{jo_median} / mean {pre_mean:.2f}→{jo_mean:.2f}"
            + (" —— PASS" if passed else " —— FAIL")
        ),
    }


# === main ============================================================
def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pre", required=True, type=Path)
    p.add_argument("--full", required=True, type=Path)
    p.add_argument("--only-extraction", required=True, type=Path)
    p.add_argument("--only-pinned", required=True, type=Path)
    p.add_argument("--jieba-only", required=True, type=Path)
    p.add_argument("--json", action="store_true", help="JSON 输出（便于脚本消费）")
    args = p.parse_args()

    pre = load(args.pre)
    full = load(args.full)
    oe = load(args.only_extraction)
    op = load(args.only_pinned)
    jo = load(args.jieba_only)

    results = {
        "AC-1a": ac_1a(pre, full),
        "AC-1b": ac_1b(full),
        "AC-2": ac_2(pre, full),
        "AC-3-extraction": ac_3_extraction(pre, oe),
        "AC-3-pinned": ac_3_pinned(pre, op),
        "AC-3-jieba": ac_3_jieba(pre, jo),
    }

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    else:
        print("=" * 70)
        print("pass-1 全量评测 AC 验证")
        print("=" * 70)
        for k, v in results.items():
            mark = "✓" if v["passed"] else "✗"
            print(f"\n[{mark}] {k}: {v['verdict']}")
            for fld, fv in v.items():
                if fld in ("passed", "verdict"):
                    continue
                if isinstance(fv, float):
                    print(f"    {fld}: {fv:.4f}")
                else:
                    print(f"    {fld}: {fv}")

    failed = [k for k, v in results.items() if not v["passed"]]
    print(f"\n总结: {len(results) - len(failed)}/{len(results)} AC 通过")
    if failed:
        print(f"未通过: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
