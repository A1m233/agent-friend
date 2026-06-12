"""memory_eval CLI 入口：在中文/英文记忆基准上跑 memory 的 ingest + retrieve + 判分。

⚠️ 运行会触发**真实 LLM 抽取调用**（每段对话一次抽取）。按项目 ``llm-api-confirm``
规则需先获授权，并在项目根 ``.env`` 配置 ``DEEPSEEK_API_KEY``。单测用 fake LLM，不触发
真实调用。

用法::

    # 默认 PerLTQA（原生中文，dialogues 子集）+ AnchorRecallJudge
    ./scripts/memory-eval/run.sh --limit-samples 1 --limit-questions 5
    # 英文基线 LoCoMo + NoopJudge（无 anchor，不打分）
    ./scripts/memory-eval/run.sh --dataset locomo --limit-samples 1 --limit-questions 5
    # 加注解（说明本次跑的目的，便于未来回看）
    ./scripts/memory-eval/run.sh --note "before extraction prompt v2"

跑完会在 ``memory_eval/baselines/`` 落一份 ``<ISO-datetime>-<short-sha>.json``，含 macro
平均、每题分数、完整召回内容、运行条件（commit / dirty / 数据集 hash / provider 参数 / note），
随仓库入 git 归档供持续对比。
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from llm_providers import LLMAuthError, LLMClient, ProviderSpec
from memory_eval.datasets import EvalCase, load_locomo, load_perltqa
from memory_eval.harness import (
    AnchorRecallJudge,
    CaseOutcome,
    Judge,
    MemoryConfig,
    NoopJudge,
    print_outcome,
    print_summary,
    run_case,
)
from memory_eval.harness.baseline import BaselineRun
from memory_eval.harness.baseline import write as write_baseline

# __file__ = memory_eval/src/memory_eval/__main__.py → parents[2] = memory_eval/
_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_BASELINES_DIR = Path(__file__).resolve().parents[2] / "baselines"
_LOCOMO_PATH = _DATA_DIR / "locomo10.json"
_PERLTQA_DIR = _DATA_DIR / "perltqa"
_PERLTQA_MEM = _PERLTQA_DIR / "perltmem.json"
_PERLTQA_QA = _PERLTQA_DIR / "perltqa.json"

_DATASETS = ("perltqa", "locomo")


def _build_llm(model: str | None) -> tuple[LLMClient, ProviderSpec]:
    """返回 (client, 实际生效的 spec)。spec 给 baseline 记录 model / api_base / defaults。"""
    spec = ProviderSpec.from_env(prefix="DEEPSEEK")
    if model:
        spec = dataclasses.replace(spec, model=model)
    return LLMClient(spec), spec


def _pick_judge(dataset: str) -> Judge:
    """PerLTQA 用 AnchorRecallJudge（数据自带 anchor）；其它数据集继续 NoopJudge。"""
    if dataset == "perltqa":
        return AnchorRecallJudge()
    return NoopJudge()


def _dataset_paths(dataset: str) -> tuple[Path, ...]:
    """返回该 dataset 用到的原始数据文件路径，供 baseline 计算 hash。"""
    if dataset == "locomo":
        return (_LOCOMO_PATH,)
    return (_PERLTQA_MEM, _PERLTQA_QA)


def _load_cases(dataset: str, limit_samples: int) -> tuple[list[EvalCase], str | None]:
    """按 ``dataset`` 选 loader 加载样本。

    Returns:
        ``(cases, error)``：``error`` 非空表示数据缺失（含下载提示），此时 ``cases`` 为空。
    """
    if dataset == "locomo":
        if not _LOCOMO_PATH.exists():
            return [], (
                f"找不到 LoCoMo 数据：{_LOCOMO_PATH}\n"
                "从 https://github.com/snap-research/locomo 下载 data/locomo10.json 放到该路径。"
            )
        return load_locomo(_LOCOMO_PATH, limit_samples=limit_samples), None

    if not _PERLTQA_MEM.exists() or not _PERLTQA_QA.exists():
        return [], (
            f"找不到 PerLTQA 数据：{_PERLTQA_DIR}/(perltmem.json, perltqa.json)\n"
            "从 https://github.com/Elvin-Yiming-Du/PerLTQA 的 Dataset/zh/ 下载这两个文件放到该目录"
            "（CC BY-NC 4.0，仅非商用研究）。"
        )
    return load_perltqa(_PERLTQA_MEM, _PERLTQA_QA, limit_samples=limit_samples), None


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="memory-eval",
        description="memory 召回质量评测（PerLTQA 中文 + AnchorRecallJudge / LoCoMo 英文 + NoopJudge）",
    )
    p.add_argument(
        "--dataset",
        choices=_DATASETS,
        default="perltqa",
        help="评测基准：perltqa（原生中文，dialogues 子集，默认）/ locomo（英文基线）",
    )
    p.add_argument("--limit-samples", type=int, default=1, help="只跑前 N 个样本（控制成本）")
    p.add_argument("--limit-questions", type=int, default=5, help="每个样本只问前 N 个问题")
    p.add_argument("--model", default=None, help="覆盖抽取用 model（默认取 .env）")
    p.add_argument(
        "--note",
        default="",
        help='本次跑的注解（如 "before extraction prompt v2"），写入 baseline 文件供未来回看',
    )
    # pass-1 M13.4 ablation 切片开关：默认都开（pass-1 终态），关掉单个跑切片 baseline。
    # 详见 docs/requirements/013-memory-quality-pass-1/design.md §6。
    p.add_argument(
        "--no-extraction-keep-specifics",
        dest="extraction_keep_specifics",
        action="store_false",
        default=True,
        help="切片：关闭 pass-1 抽取保具体词改进，用旧 prompt（pre-pass-1 行为）",
    )
    p.add_argument(
        "--no-pinned-relevance-gate",
        dest="pinned_relevance_gate",
        action="store_false",
        default=True,
        help="切片：关闭 pass-1 pinned relevance gate，pinned 全量注入（pre-pass-1 行为）",
    )
    return p.parse_args()


def main() -> int:
    load_dotenv()
    args = _parse_args()

    cases, error = _load_cases(args.dataset, args.limit_samples)
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if not cases:
        print("数据集为空或解析不出任何 case。", file=sys.stderr)
        return 1

    try:
        llm, spec = _build_llm(args.model)
    except LLMAuthError as e:
        print(
            f"[配置错误] {e}\n请在项目根 .env 配置 DEEPSEEK_API_KEY。",
            file=sys.stderr,
        )
        return 1

    judge = _pick_judge(args.dataset)
    memory_config = MemoryConfig(
        extraction_keep_specifics=args.extraction_keep_specifics,
        pinned_relevance_gate=args.pinned_relevance_gate,
    )
    started_at = datetime.now(UTC)
    all_outcomes: list[CaseOutcome] = []
    with tempfile.TemporaryDirectory(prefix="memory-eval-") as tmp:
        for index, case in enumerate(cases):
            # sample_id 可能含路径不安全字符，用序号作库名
            db_path = Path(tmp) / f"case-{index}.db"
            outcome = run_case(
                case,
                llm,
                db_path=db_path,
                judge=judge,
                limit_questions=args.limit_questions,
                memory_config=memory_config,
            )
            print_outcome(outcome)
            all_outcomes.append(outcome)
    ended_at = datetime.now(UTC)

    print_summary(all_outcomes)
    path = write_baseline(
        all_outcomes,
        BaselineRun(
            started_at=started_at,
            ended_at=ended_at,
            dataset=args.dataset,
            model=spec.model,
            api_base=spec.api_base,
            provider_defaults=spec.defaults,
            limit_samples=args.limit_samples,
            limit_questions=args.limit_questions,
            note=args.note,
            dataset_paths=_dataset_paths(args.dataset),
        ),
        _BASELINES_DIR,
    )
    print(f"baseline 已落盘: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
