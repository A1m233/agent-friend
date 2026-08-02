from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path


def _load_quality():
    path = Path(__file__).with_name("quality.py")
    spec = importlib.util.spec_from_file_location("social_platform_quality", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


quality = _load_quality()


def test_frozen_cases_match_design_distribution() -> None:
    sampled_at, cases = quality.load_cases()

    assert sampled_at == "2026-08-02"
    assert len(cases) == 16


def test_platform_identity_ignores_tracking_query() -> None:
    assert (
        quality.platform_identity("https://www.bilibili.com/video/BV1DfrdByE2H?spm_id_from=333.1")
        == "BV1DfrdByE2H"
    )
    assert (
        quality.platform_identity(
            "https://www.xiaohongshu.com/explore/69D9D3C8000000001A021C89?xsec_source=pc"
        )
        == "69d9d3c8000000001a021c89"
    )


def test_summary_enforces_incremental_read_and_query_kind_thresholds() -> None:
    _, cases = quality.load_cases()
    results = [
        quality.CaseResult(
            id=case.id,
            platform=case.platform,
            content_type=case.content_type,
            query_kind=case.query_kind,
            target_id=case.target_id,
            query=case.query,
            platform_rank=1,
            web_rank=None,
            incremental_hit=True,
            read_status="content",
            search_elapsed_ms=100,
            read_elapsed_ms=50,
            web_elapsed_ms=25,
            platform_error=None,
            platform_error_stage=None,
            web_error=None,
            web_result_count=1,
            web_domain_correct=True,
        )
        for case in cases
    ]

    summaries = quality.summarize(tuple(results))
    assert summaries["xiaohongshu"]["passed"] is True
    assert summaries["bilibili"]["passed"] is True

    results[0] = replace(results[0], read_status="invalid:empty")
    assert quality.summarize(tuple(results))["xiaohongshu"]["passed"] is False


def test_run_cases_waits_only_between_cases(monkeypatch) -> None:
    _, cases = quality.load_cases()
    selected = cases[:3]
    events: list[str] = []

    def fake_run_case(case, service, web_tool):
        del service, web_tool
        events.append(f"case:{case.id}")
        return quality.CaseResult(
            id=case.id,
            platform=case.platform,
            content_type=case.content_type,
            query_kind=case.query_kind,
            target_id=case.target_id,
            query=case.query,
            platform_rank=None,
            web_rank=None,
            incremental_hit=False,
            read_status="not_attempted",
            search_elapsed_ms=100,
            read_elapsed_ms=None,
            web_elapsed_ms=25,
            platform_error=None,
            platform_error_stage=None,
            web_error=None,
            web_result_count=1,
            web_domain_correct=True,
        )

    monkeypatch.setattr(quality, "_run_case", fake_run_case)

    results = quality.run_cases(
        selected,
        object(),
        object(),
        case_interval_seconds=2.5,
        sleep=lambda seconds: events.append(f"sleep:{seconds:g}"),
    )

    assert tuple(result.id for result in results) == tuple(case.id for case in selected)
    assert events == [
        f"case:{selected[0].id}",
        "sleep:2.5",
        f"case:{selected[1].id}",
        "sleep:2.5",
        f"case:{selected[2].id}",
    ]


def test_run_cases_stops_after_platform_wide_block(monkeypatch) -> None:
    _, cases = quality.load_cases()
    selected = cases[:3]
    events: list[str] = []

    def fake_run_case(case, service, web_tool):
        del service, web_tool
        events.append(f"case:{case.id}")
        return quality.CaseResult(
            id=case.id,
            platform=case.platform,
            content_type=case.content_type,
            query_kind=case.query_kind,
            target_id=case.target_id,
            query=case.query,
            platform_rank=None,
            web_rank=None,
            incremental_hit=False,
            read_status="not_attempted",
            search_elapsed_ms=100,
            read_elapsed_ms=None,
            web_elapsed_ms=25,
            platform_error="auth_expired",
            platform_error_stage="search",
            web_error=None,
            web_result_count=1,
            web_domain_correct=True,
        )

    monkeypatch.setattr(quality, "_run_case", fake_run_case)

    results = quality.run_cases(
        selected,
        object(),
        object(),
        case_interval_seconds=2.5,
        sleep=lambda seconds: events.append(f"sleep:{seconds:g}"),
    )

    assert len(results) == 1
    assert results[0].platform_error == "auth_expired"
    assert events == [f"case:{selected[0].id}"]


def test_summary_reports_completion_conditional_hit_rate() -> None:
    _, cases = quality.load_cases()
    selected = [case for case in cases if case.platform == "xiaohongshu"]
    results = tuple(
        quality.CaseResult(
            id=case.id,
            platform=case.platform,
            content_type=case.content_type,
            query_kind=case.query_kind,
            target_id=case.target_id,
            query=case.query,
            platform_rank=1 if index < 3 else None,
            web_rank=None,
            incremental_hit=index < 3,
            read_status="content" if index < 3 else "not_attempted",
            search_elapsed_ms=100,
            read_elapsed_ms=50 if index < 3 else None,
            web_elapsed_ms=25,
            platform_error="network_error" if index >= 4 else None,
            platform_error_stage="search" if index >= 4 else None,
            web_error=None,
            web_result_count=1,
            web_domain_correct=True,
        )
        for index, case in enumerate(selected)
    )

    summary = quality.summarize(results)["xiaohongshu"]

    assert summary["platform_completed"] == 4
    assert summary["conditional_top10_hit_rate"] == 0.75
