"""agent_eval CLI 入口。

运行会触发真实 LLM 调用；含 C2 时还可能触发 Tavily 搜索调用。
按项目 llm-api-confirm 规则，执行前需获得用户明确授权。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from .baseline import ProviderRunInfo, ReportRun, ToolRunInfo, write_report
from .judge import judge_case
from .loader import build_case_set_meta, load_contracts
from .report import CaseRunResult, render_contract_summary, render_failure_details
from .selection import Selection, select_contracts

if TYPE_CHECKING:
    from llm_providers.spec import ProviderSpec

    from runtime_config import CredentialSource

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_CASES_DIR = _PACKAGE_ROOT / "cases"
_REPORTS_DIR = _PACKAGE_ROOT / "reports"
_BASELINES_DIR = _PACKAGE_ROOT / "baselines"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent-eval",
        description="agent 行为契约评测（fixtures + deterministic judge）",
    )
    parser.add_argument(
        "--contract",
        default="all",
        help="只跑指定契约，如 C1 或 C26；默认 all",
    )
    parser.add_argument(
        "--case",
        dest="case_id",
        default=None,
        metavar="CASE_ID",
        help="只跑指定 case id，如 C4-07；指定后忽略 --contract / --limit-cases",
    )
    parser.add_argument(
        "--limit-cases",
        type=int,
        default=0,
        metavar="N",
        help="每个 contract 只跑前 N 个 case；0 表示不限制",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="覆盖被测 agent 用的 model；默认取 runtime_config 中的 DEEPSEEK_MODEL",
    )
    parser.add_argument(
        "--reasoning-preset",
        default=None,
        help="覆盖被测 model 的产品推理档位（如 off / low / high / max）",
    )
    parser.add_argument(
        "--note",
        default="",
        help="写入 report / baseline 的人工注解",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="写入 agent_eval/baselines/；默认写入 agent_eval/reports/",
    )
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=_CASES_DIR,
        help="覆盖 case fixture 目录（主要用于本地验证 / 测试）",
    )
    return parser.parse_args()


def _credential_source() -> CredentialSource:
    from runtime_config import config_mode_from_env, credential_source_for_mode

    mode = config_mode_from_env()
    return credential_source_for_mode(mode, dotenv_path=Path.cwd() / ".env")


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
    reasoning_preset: str | None = None,
) -> ProviderSpec:
    from llm_providers import (
        build_provider_spec,
        default_model_for_provider,
        infer_provider_id_from_model,
        provider_definition,
    )
    from runtime_config import DEEPSEEK_MODEL, optional_secret, require_secret

    model_id = (
        model or optional_secret(source, DEEPSEEK_MODEL) or default_model_for_provider("deepseek")
    )
    from llm_providers.model_identity import is_custom_model_reference

    if is_custom_model_reference(model_id):
        from llm_providers.connections import ModelDirectory
        from llm_providers.connections.codec import decode_connections

        from llm_providers import ModelRuntimeConfig
        from runtime_config import DesktopSettingsReader

        reader = DesktopSettingsReader()
        settings = reader.read()
        if settings is None and reader.path.exists():
            raise ValueError("Model settings cannot be read")
        directory = ModelDirectory(decode_connections((settings or {}).get("modelConnections")))
        config = (
            ModelRuntimeConfig(model_id, reasoning_preset)
            if reasoning_preset is not None
            else directory.default_config(model_id)
        )
        return directory.build_spec(
            config, api_key=require_secret(source, directory.credential_name(model_id))
        )
    try:
        provider_id = infer_provider_id_from_model(model_id)
    except ValueError:
        provider_id = "deepseek"
    provider = provider_definition(provider_id)
    prefix = provider.credential_name.removesuffix("_API_KEY")
    spec = build_provider_spec(
        provider_id=provider_id,
        api_key=require_secret(source, provider.credential_name),
        model_id=model_id,
        reasoning_preset=reasoning_preset,
        context_window=_optional_int(source, f"{prefix}_CONTEXT_WINDOW"),
    )
    return replace(
        spec,
        api_base=optional_secret(source, f"{prefix}_API_BASE") or spec.api_base,
    )


def _tavily_api_key(source: CredentialSource) -> str | None:
    from runtime_config import TAVILY_API_KEY, optional_secret

    return optional_secret(source, TAVILY_API_KEY)


def _tool_info(source: CredentialSource) -> ToolRunInfo:
    tavily_present = _tavily_api_key(source) is not None
    return ToolRunInfo(
        tavily_key_present=tavily_present,
        web_search_available=tavily_present,
        recall_past_chats_available=True,
    )


def main() -> int:
    args = _parse_args()
    console = Console()

    try:
        contracts = load_contracts(args.cases_dir)
        case_set = build_case_set_meta(args.cases_dir, contracts)
        selection = Selection(
            contract=args.contract,
            case_id=args.case_id,
            limit_cases=max(0, args.limit_cases),
        )
        selected = select_contracts(contracts, selection)
    except ValueError as exc:
        print(f"[err] {exc}", file=sys.stderr)
        return 1

    from llm_providers.errors import LLMAuthError, LLMModelUnavailableError

    from runtime_config import CredentialMissingError

    try:
        source = _credential_source()
        spec = _provider_spec(args.model, source, args.reasoning_preset)
    except (CredentialMissingError, LLMAuthError, LLMModelUnavailableError, ValueError) as exc:
        print(
            f"[配置错误] {exc}\n"
            "请使用 scripts/credentials/run.* 配置被测 model 对应的 provider credential；"
            "如需读取存量 .env，请设置 AGENT_FRIEND_CONFIG_MODE=legacy-dotenv。",
            file=sys.stderr,
        )
        return 1

    tavily_api_key = _tavily_api_key(source)
    tools = _tool_info(source)
    if (
        any(contract.id == "C2" for contract in selected.contracts)
        and not tools.web_search_available
    ):
        console.print(
            "[yellow]提示：本次包含 C2，但未配置 TAVILY_API_KEY；web_search 不会注册，"
            "C2 结果会反映该运行环境。[/yellow]"
        )

    out_dir = _BASELINES_DIR if args.baseline else _REPORTS_DIR
    console.print(
        f"[dim]model={spec.product_model_id or spec.model} "
        f"reasoning_preset={spec.reasoning_preset or 'none'} baseline={args.baseline} "
        f"selection={selected.label} total_cases={selected.total_cases}[/dim]\n"
    )

    from .runner import run_case

    started_at = datetime.now(UTC)
    results: dict[str, list[CaseRunResult]] = {}
    for contract in selected.contracts:
        console.print(
            f"[bold]{contract.id} · {contract.name}[/bold] — 跑 {len(contract.cases)} 个 case"
        )
        items: list[CaseRunResult] = []
        for index, case in enumerate(contract.cases, 1):
            console.print(f"  [{index}/{len(contract.cases)}] {case.id} … ", end="")
            trace = run_case(case, spec, tavily_api_key=tavily_api_key)
            verdict = judge_case(case, contract, trace)
            mark = "[green]✓[/green]" if verdict.passed else "[red]✗[/red]"
            console.print(mark)
            items.append(CaseRunResult(case=case, trace=trace, verdict=verdict))
        results[contract.id] = items
        console.print()

    ended_at = datetime.now(UTC)
    console.print("[bold]── 汇总 ──[/bold]")
    for contract in selected.contracts:
        render_contract_summary(contract, results[contract.id], console)
    for contract in selected.contracts:
        render_failure_details(contract, results[contract.id], console)

    path = write_report(
        out_dir=out_dir,
        label=selected.label,
        contracts_run=selected.contracts,
        results=results,
        run=ReportRun(
            started_at=started_at,
            ended_at=ended_at,
            selection=selection,
            provider=ProviderRunInfo(
                model=spec.model,
                api_base=spec.api_base,
                defaults=spec.defaults,
                context_window=spec.context_window,
            ),
            tools=tools,
            case_set=case_set,
            note=args.note,
        ),
    )
    console.print(f"[dim]报告已落盘: {path}[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
