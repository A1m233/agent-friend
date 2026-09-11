"""memory_eval CLI 入口：在中文/英文记忆基准上跑 memory 的 ingest + retrieve + 判分。

⚠️ 运行会触发**真实 LLM 抽取调用**（每段对话一次抽取）。按项目 ``llm-api-confirm``
规则需先获授权。默认从 runtime_config 凭证中心读取配置；如需读取存量
``.env``，显式设置 ``AGENT_FRIEND_CONFIG_MODE=legacy-dotenv``。单测用 fake LLM，不触发真实调用。

用法::

    # 默认 PerLTQA（原生中文，dialogues 子集）+ AnchorRecallJudge
    ./scripts/memory-eval/run.sh --retrieval-mode keyword --limit-samples 1 --limit-questions 5
    # 英文基线 LoCoMo + NoopJudge（无 anchor，不打分）
    ./scripts/memory-eval/run.sh --dataset locomo --retrieval-mode keyword --limit-samples 1 --limit-questions 5
    # 加注解（说明本次跑的目的，便于未来回看）
    ./scripts/memory-eval/run.sh --retrieval-mode keyword --note "before extraction prompt v2"

跑完会在 ``memory_eval/baselines/`` 落一份 ``<ISO-datetime>-<short-sha>.json``，含 macro
平均、每题分数、完整召回内容、运行条件（commit / dirty / 数据集 hash / provider 参数 / note），
随仓库入 git 归档供持续对比。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from agent.paths import memory_db_path

from llm_providers import LLMAuthError, LLMClient, ProviderSpec
from memory import ExtractionAttempt
from memory_eval.adapters import case_fragments
from memory_eval.datasets import (
    EvalCase,
    load_core_profile_cases,
    load_locomo,
    load_perltqa,
    load_semantic_change_cases,
)
from memory_eval.harness import (
    AnchorRecallJudge,
    CaseOutcome,
    EvalWorkspace,
    Judge,
    LayerConstrainedAnchorJudge,
    MemoryConfig,
    NoopJudge,
    RetrievalMode,
    WorkspaceManifest,
    build_factorial_corpora,
    evaluate_case_database,
    factorial_db_path,
    ingest_case_database,
    print_outcome,
    print_summary,
    run_paired_extraction_probe,
    run_stability_probe,
    summarize_factorial,
)
from memory_eval.harness.baseline import BaselineRun
from memory_eval.harness.baseline import write as write_baseline
from runtime_config import CredentialMissingError, CredentialSource

# __file__ = memory_eval/src/memory_eval/__main__.py → parents[2] = memory_eval/
_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_BASELINES_DIR = Path(__file__).resolve().parents[2] / "baselines"
_LOCOMO_PATH = _DATA_DIR / "locomo10.json"
_PERLTQA_DIR = _DATA_DIR / "perltqa"
_PERLTQA_MEM = _PERLTQA_DIR / "perltmem.json"
_PERLTQA_QA = _PERLTQA_DIR / "perltqa.json"
_DEFAULT_DENSE_MODEL_DIR = memory_db_path().parent / "models" / "bge-small-zh-v1.5"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXTRACTION_PROMPT_REPO_PATH = "memory/src/memory/extraction/prompts/extract.md"
_CURRENT_EXTRACTION_PROMPT = _REPO_ROOT / _EXTRACTION_PROMPT_REPO_PATH
_CORE_PROFILE_DATASET_SOURCE = Path(__file__).resolve().parent / "datasets" / "core_profile.py"
_SEMANTIC_CHANGE_DATASET_SOURCE = (
    Path(__file__).resolve().parent / "datasets" / "semantic_changes.py"
)

_DATASETS = ("perltqa", "locomo", "core-profile", "semantic-change")


def _credential_source() -> CredentialSource:
    from runtime_config import config_mode_from_env, credential_source_for_mode

    return credential_source_for_mode(config_mode_from_env(), dotenv_path=Path.cwd() / ".env")


def _optional_int(source: CredentialSource, name: str) -> int | None:
    from runtime_config import optional_secret

    raw = optional_secret(source, name)
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _provider_spec(
    model: str | None,
    source: CredentialSource,
    *,
    timeout_seconds: float | None = None,
    temperature: float | None = None,
) -> ProviderSpec:
    from llm_providers import build_provider_spec, infer_provider_id_from_model, provider_definition
    from runtime_config import DEEPSEEK_MODEL, optional_secret, require_secret

    selected = model or optional_secret(source, DEEPSEEK_MODEL) or "deepseek/deepseek-flash"
    provider = provider_definition(infer_provider_id_from_model(selected))
    prefix = provider.credential_name.removesuffix("_API_KEY")
    spec = build_provider_spec(
        provider_id=provider.provider_id,
        model_id=selected,
        api_key=require_secret(source, provider.credential_name),
        context_window=_optional_int(source, f"{prefix}_CONTEXT_WINDOW"),
        timeout_seconds=timeout_seconds,
    )
    base = optional_secret(source, f"{prefix}_API_BASE")
    if base:
        spec = replace(spec, api_base=base)
    if temperature is None:
        return spec
    return replace(spec, defaults={**spec.defaults, "temperature": temperature})


def _build_llm(
    model: str | None,
    source: CredentialSource,
    *,
    timeout_seconds: float | None = None,
    temperature: float | None = None,
) -> tuple[LLMClient, ProviderSpec]:
    """返回 (client, 实际生效的 spec)。spec 给 baseline 记录 model / api_base / defaults。"""
    spec = _provider_spec(
        model,
        source,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
    )
    return LLMClient(spec), spec


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("必须大于等于 0")
    return parsed


def _pick_judge(dataset: str) -> Judge:
    """PerLTQA 用 AnchorRecallJudge（数据自带 anchor）；其它数据集继续 NoopJudge。"""
    if dataset in ("core-profile", "semantic-change"):
        return LayerConstrainedAnchorJudge()
    if dataset == "perltqa":
        return AnchorRecallJudge()
    return NoopJudge()


def _dataset_paths(dataset: str) -> tuple[Path, ...]:
    """返回该 dataset 用到的原始数据文件路径，供 baseline 计算 hash。"""
    if dataset == "locomo":
        return (_LOCOMO_PATH,)
    if dataset == "core-profile":
        return (_CORE_PROFILE_DATASET_SOURCE,)
    if dataset == "semantic-change":
        return (_SEMANTIC_CHANGE_DATASET_SOURCE,)
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

    if dataset == "core-profile":
        return load_core_profile_cases(limit_samples=limit_samples), None
    if dataset == "semantic-change":
        return load_semantic_change_cases(limit_samples=limit_samples), None

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
        help=(
            "评测基准：perltqa（默认）/ locomo / core-profile（084 产品回归切片）/ "
            "semantic-change（086 变更图切片）"
        ),
    )
    p.add_argument("--limit-samples", type=int, default=1, help="只跑前 N 个样本（控制成本）")
    p.add_argument("--limit-questions", type=int, default=5, help="每个样本只问前 N 个问题")
    p.add_argument(
        "--retrieval-mode",
        choices=(
            "legacy-keyword",
            "keyword",
            "hybrid",
            "unified-keyword",
            "unified-hybrid",
        ),
        default=None,
        help="显式选择检索路径；hybrid 未 ready 时直接失败，不允许静默降级",
    )
    p.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="持久评测工作区；默认写 memory_eval/data/runs/<timestamp>",
    )
    p.add_argument("--resume", action="store_true", help="续跑已有 workspace，跳过已完成 case")
    p.add_argument(
        "--replay",
        action="store_true",
        help="只复放已有 case DB，不初始化凭证、不执行抽取 LLM",
    )
    p.add_argument(
        "--stability-probe",
        action="store_true",
        help="只重复抽取固定 fragment 并记录原始响应/解析状态；不建 memory DB、不跑检索",
    )
    p.add_argument(
        "--paired-extraction-probe",
        action="store_true",
        help="AB/BA 交错比较 baseline 与当前抽取 prompt；不建 memory DB、不跑检索",
    )
    p.add_argument(
        "--factorial-eval",
        action="store_true",
        help="执行 E0/E1 × legacy/unified-hybrid 全量终局；抽取 AB/BA 且逐 fragment checkpoint",
    )
    p.add_argument(
        "--paired-baseline-ref",
        default="origin/main",
        help="配对抽取 baseline prompt 的 git ref（默认 origin/main；实际 commit 会写入 config lock）",
    )
    p.add_argument(
        "--stability-fragments",
        type=_positive_int,
        default=30,
        help="稳定性 probe 的固定 fragment 数（按稳定 hash 选择，默认 30）",
    )
    p.add_argument(
        "--stability-repeats",
        type=_positive_int,
        default=3,
        help="每个 fragment 重复抽取次数（默认 3）",
    )
    p.add_argument(
        "--dense-model-dir",
        type=Path,
        default=_DEFAULT_DENSE_MODEL_DIR,
        help="hybrid 使用的本地 ONNX 模型目录",
    )
    p.add_argument("--model", default=None, help="覆盖抽取用 model（默认取 runtime_config）")
    p.add_argument(
        "--timeout-seconds",
        type=_positive_float,
        default=None,
        help="覆盖单次抽取 LLM 调用超时；Pro 全量评测可提高到 300",
    )
    p.add_argument(
        "--temperature",
        type=_nonnegative_float,
        default=None,
        help="显式覆盖抽取 temperature；稳定性对照建议使用 0，并记录到 manifest/baseline",
    )
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
    args = _parse_args()

    if args.resume and args.replay:
        print("--resume 与 --replay 不能同时使用。", file=sys.stderr)
        return 1
    special_modes = sum(
        bool(item)
        for item in (args.stability_probe, args.paired_extraction_probe, args.factorial_eval)
    )
    if special_modes > 1:
        print(
            "--stability-probe / --paired-extraction-probe / --factorial-eval 只能选一个。",
            file=sys.stderr,
        )
        return 1
    if special_modes and args.replay:
        print("抽取 probe 不支持 --replay；续跑请使用 --resume。", file=sys.stderr)
        return 1
    if not special_modes and args.retrieval_mode is None:
        print("普通评测必须显式提供 --retrieval-mode。", file=sys.stderr)
        return 1

    cases, error = _load_cases(args.dataset, args.limit_samples)
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if not cases:
        print("数据集为空或解析不出任何 case。", file=sys.stderr)
        return 1

    judge = _pick_judge(args.dataset)
    memory_config = MemoryConfig(
        extraction_keep_specifics=args.extraction_keep_specifics,
        pinned_relevance_gate=args.pinned_relevance_gate,
    )
    started_at = datetime.now(UTC)
    workspace_path = args.workspace or (
        _DATA_DIR / "runs" / started_at.strftime("%Y-%m-%dT%H-%M-%S")
    )
    workspace = EvalWorkspace(workspace_path)
    dataset_paths = _dataset_paths(args.dataset)

    if args.retrieval_mode in ("hybrid", "unified-hybrid") or args.factorial_eval:
        error = _dense_assets_error(args.dense_model_dir)
        if error is not None:
            print(f"[hybrid preflight] {error}", file=sys.stderr)
            return 1

    llm: LLMClient | None = None
    if args.replay:
        try:
            stored_manifest = workspace.load_manifest()
            expected = WorkspaceManifest.build(
                dataset=args.dataset,
                sample_ids=[case.sample_id for case in cases],
                model=stored_manifest.model,
                api_base=stored_manifest.api_base,
                provider_defaults=stored_manifest.provider_defaults,
                extraction_keep_specifics=args.extraction_keep_specifics,
                pinned_relevance_gate=args.pinned_relevance_gate,
                dataset_paths=dataset_paths,
                extraction_code_fingerprint=stored_manifest.extraction_code_fingerprint,
            )
            manifest = workspace.assert_compatible(expected)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"[workspace 错误] {exc}", file=sys.stderr)
            return 1
    else:
        try:
            source = _credential_source()
            llm, spec = _build_llm(
                args.model,
                source,
                timeout_seconds=args.timeout_seconds,
                temperature=args.temperature,
            )
        except (CredentialMissingError, LLMAuthError, ValueError) as exc:
            print(
                f"[配置错误] {exc}\n"
                "默认请使用 scripts/credentials/run.* set-bundle deepseek 配置；"
                "如需读取存量 .env，请设置 AGENT_FRIEND_CONFIG_MODE=legacy-dotenv。",
                file=sys.stderr,
            )
            return 1
        manifest = WorkspaceManifest.build(
            dataset=args.dataset,
            sample_ids=[case.sample_id for case in cases],
            model=spec.model,
            api_base=spec.api_base,
            provider_defaults=dict(spec.defaults),
            extraction_keep_specifics=args.extraction_keep_specifics,
            pinned_relevance_gate=args.pinned_relevance_gate,
            dataset_paths=dataset_paths,
            extraction_code_fingerprint=_extraction_code_fingerprint(),
        )
        try:
            if args.resume:
                workspace.assert_compatible(manifest)
            else:
                workspace.create(manifest)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"[workspace 错误] {exc}", file=sys.stderr)
            return 1

    if args.stability_probe:
        assert llm is not None
        available_fragments = sum(
            len({turn.session_index for turn in case.turns}) for case in cases
        )
        expected_calls = min(args.stability_fragments, available_fragments) * args.stability_repeats
        print(f"workspace: {workspace.root}")
        print(
            "stability probe: "
            f"最多 {args.stability_fragments} fragments × {args.stability_repeats} repeats "
            f"= {expected_calls} 次 LLM 调用"
        )
        try:
            summary = run_stability_probe(
                cases,
                llm,
                output_dir=workspace.root / "stability",
                fragment_count=args.stability_fragments,
                repeats=args.stability_repeats,
                resume=args.resume,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"[stability probe 失败] {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.paired_extraction_probe:
        assert llm is not None
        try:
            baseline_commit, baseline_prompt = _git_prompt_at_ref(args.paired_baseline_ref)
            current_prompt = _CURRENT_EXTRACTION_PROMPT.read_text(encoding="utf-8")
        except (OSError, RuntimeError) as exc:
            print(f"[paired extraction preflight 失败] {exc}", file=sys.stderr)
            return 1
        available_fragments = sum(
            len({turn.session_index for turn in case.turns}) for case in cases
        )
        expected_calls = (
            min(args.stability_fragments, available_fragments) * args.stability_repeats * 2
        )
        print(f"workspace: {workspace.root}")
        print(
            "paired extraction probe: "
            f"最多 {args.stability_fragments} fragments × 2 prompts × "
            f"{args.stability_repeats} repeats = {expected_calls} 次 LLM 调用"
        )
        try:
            summary = run_paired_extraction_probe(
                cases,
                llm,
                variants={"pass1": baseline_prompt, "current": current_prompt},
                output_dir=workspace.root / "paired-extraction",
                fragment_count=args.stability_fragments,
                repeats=args.stability_repeats,
                run_metadata={
                    "baseline_ref": args.paired_baseline_ref,
                    "baseline_commit": baseline_commit,
                    "model": manifest.model,
                    "api_base": manifest.api_base,
                    "provider_defaults": manifest.provider_defaults,
                    "dataset_files": list(manifest.dataset_files),
                    "extraction_code_fingerprint": manifest.extraction_code_fingerprint,
                },
                resume=args.resume,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"[paired extraction probe 失败] {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.factorial_eval:
        assert llm is not None
        try:
            baseline_commit, baseline_prompt = _git_prompt_at_ref(args.paired_baseline_ref)
            current_prompt = _CURRENT_EXTRACTION_PROMPT.read_text(encoding="utf-8")
        except (OSError, RuntimeError) as exc:
            print(f"[factorial preflight 失败] {exc}", file=sys.stderr)
            return 1
        factorial_dir = workspace.root / "factorial"
        expected_calls = sum(len(case_fragments(case)) for case in cases) * 2
        print(f"workspace: {workspace.root}")
        print(
            f"factorial extraction: {len(cases)} cases, E0/E1 AB/BA 共 {expected_calls} 次 LLM 调用"
        )
        try:
            extraction_summary = build_factorial_corpora(
                cases,
                llm,
                variants={"E0": baseline_prompt, "E1": current_prompt},
                output_dir=factorial_dir,
                run_metadata={
                    "baseline_ref": args.paired_baseline_ref,
                    "baseline_commit": baseline_commit,
                    "model": manifest.model,
                    "api_base": manifest.api_base,
                    "provider_defaults": manifest.provider_defaults,
                    "dataset_files": list(manifest.dataset_files),
                    "extraction_code_fingerprint": manifest.extraction_code_fingerprint,
                },
                resume=args.resume,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"[factorial extraction 失败] {exc}", file=sys.stderr)
            return 1
        print(json.dumps(extraction_summary, ensure_ascii=False, indent=2))

        conditions: dict[str, tuple[str, RetrievalMode]] = {
            "E0R0": ("E0", "legacy-keyword"),
            "E0R1": ("E0", "unified-hybrid"),
            "E1R0": ("E1", "legacy-keyword"),
            "E1R1": ("E1", "unified-hybrid"),
        }
        condition_outcomes: dict[str, list[CaseOutcome]] = {}
        baseline_paths: dict[str, str] = {}
        for condition, (variant, retrieval_mode) in conditions.items():
            condition_started = datetime.now(UTC)
            print(f"[{condition}] replay {retrieval_mode}")
            outcomes = []
            for case_index, case in enumerate(cases):
                try:
                    factorial_outcome = evaluate_case_database(
                        case,
                        db_path=factorial_db_path(factorial_dir, case_index, variant),
                        judge=judge,
                        retrieval_mode=retrieval_mode,
                        limit_questions=args.limit_questions,
                        pinned_relevance_gate=memory_config.pinned_relevance_gate,
                        dense_model_dir=args.dense_model_dir,
                    )
                except Exception as exc:
                    print(f"[{condition} case 检索失败] {case.sample_id}: {exc}", file=sys.stderr)
                    return 1
                outcomes.append(factorial_outcome)
                print(f"  [{case_index + 1}/{len(cases)}] {case.sample_id}")
            condition_outcomes[condition] = outcomes
            baseline_path = write_baseline(
                outcomes,
                BaselineRun(
                    started_at=condition_started,
                    ended_at=datetime.now(UTC),
                    dataset=args.dataset,
                    model=manifest.model,
                    api_base=manifest.api_base,
                    provider_defaults=manifest.provider_defaults,
                    limit_samples=args.limit_samples,
                    limit_questions=args.limit_questions,
                    note=f"048 factorial {condition}: {variant}+{retrieval_mode}; {args.note}",
                    dataset_paths=dataset_paths,
                    retrieval_mode=retrieval_mode,
                    workspace=str(workspace.root),
                    code_fingerprint=_retrieval_code_fingerprint(),
                ),
                factorial_dir / "baselines" / condition,
            )
            baseline_paths[condition] = str(baseline_path)
        report = summarize_factorial(condition_outcomes)
        report["baseline_paths"] = baseline_paths
        report_path = factorial_dir / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"factorial report 已落盘: {report_path}")
        return 0

    assert args.retrieval_mode is not None
    print(f"workspace: {workspace.root}")
    all_outcomes: list[CaseOutcome] = []
    code_fingerprint = _retrieval_code_fingerprint()
    result_key = f"{args.retrieval_mode}-q{args.limit_questions}-{code_fingerprint[:12]}"
    for index, case in enumerate(cases):
        db_path = workspace.case_db(index)
        if not workspace.is_ingested(index, case.sample_id):
            if args.replay:
                print(
                    f"[workspace 错误] case {index} ({case.sample_id}) 尚未完成抽取，不能 replay。",
                    file=sys.stderr,
                )
                return 1
            assert llm is not None
            workspace.reset_incomplete_case(index)
            print(f"[{index + 1}/{len(cases)}] ingest {case.sample_id}")

            def record_attempt(attempt: ExtractionAttempt, case_index: int = index) -> None:
                workspace.append_extraction_attempt(case_index, attempt)

            try:
                ingest_case_database(
                    case,
                    llm,
                    db_path=db_path,
                    memory_config=memory_config,
                    on_extraction_attempt=record_attempt,
                )
                summary = workspace.mark_ingested(index, case.sample_id)
                print(f"  extraction summary: {json.dumps(summary, ensure_ascii=False)}")
            except Exception as exc:
                print(f"[case 抽取失败] {case.sample_id}: {exc}", file=sys.stderr)
                return 1
        else:
            print(f"[{index + 1}/{len(cases)}] reuse extracted DB {case.sample_id}")

        outcome = workspace.load_outcome(index, result_key=result_key)
        if outcome is None:
            try:
                outcome = evaluate_case_database(
                    case,
                    db_path=db_path,
                    judge=judge,
                    retrieval_mode=args.retrieval_mode,
                    limit_questions=args.limit_questions,
                    pinned_relevance_gate=memory_config.pinned_relevance_gate,
                    dense_model_dir=args.dense_model_dir,
                )
            except Exception as exc:
                print(f"[case 检索失败] {case.sample_id}: {exc}", file=sys.stderr)
                return 1
            workspace.save_outcome(index, outcome, result_key=result_key)
        else:
            print(f"  checkpoint hit: {result_key}")
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
            model=manifest.model,
            api_base=manifest.api_base,
            provider_defaults=manifest.provider_defaults,
            limit_samples=args.limit_samples,
            limit_questions=args.limit_questions,
            note=args.note,
            dataset_paths=dataset_paths,
            retrieval_mode=args.retrieval_mode,
            workspace=str(workspace.root),
            code_fingerprint=code_fingerprint,
        ),
        _BASELINES_DIR,
    )
    print(f"baseline 已落盘: {path}")
    return 0


def _dense_assets_error(model_dir: Path) -> str | None:
    required = (
        model_dir / "onnx" / "model_quantized.onnx",
        model_dir / "tokenizer.json",
        model_dir / "manifest.json",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if not missing:
        return None
    return "dense 资产不完整，拒绝以 hybrid 名义运行；缺少: " + ", ".join(missing)


def _retrieval_code_fingerprint() -> str:
    """让 checkpoint 随检索/评测源码变化失效，避免复用旧代码算出的结果。"""
    digest = hashlib.sha256()
    roots = (
        _REPO_ROOT / "memory" / "src" / "memory",
        _REPO_ROOT / "memory_eval" / "src" / "memory_eval",
    )
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            digest.update(path.relative_to(_REPO_ROOT).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _git_prompt_at_ref(ref: str) -> tuple[str, str]:
    """从固定 repo path 读取基线 prompt，并把移动 ref 解析成不可变 commit。"""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        prompt = subprocess.run(
            ["git", "show", f"{commit}:{_EXTRACTION_PROMPT_REPO_PATH}"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"无法从 {ref} 读取 pass-1 prompt: {detail}") from exc
    if not prompt.strip():
        raise RuntimeError(f"{ref} 的 pass-1 prompt 为空")
    return commit, prompt


def _extraction_code_fingerprint() -> str:
    """锁定真实抽取所依赖的 prompt、解析、投影与装配代码。"""
    digest = hashlib.sha256()
    paths = [
        _REPO_ROOT / "memory" / "src" / "memory" / "factory.py",
        _REPO_ROOT / "memory_eval" / "src" / "memory_eval" / "adapters" / "memory_adapter.py",
    ]
    paths.extend(sorted((_REPO_ROOT / "memory" / "src" / "memory" / "extraction").rglob("*.py")))
    paths.extend(sorted((_REPO_ROOT / "memory" / "src" / "memory" / "extraction").rglob("*.md")))
    for path in paths:
        digest.update(path.relative_to(_REPO_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


if __name__ == "__main__":
    sys.exit(main())
