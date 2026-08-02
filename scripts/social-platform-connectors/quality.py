"""Run the frozen social-platform search quality comparison."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from agent.tools.builtin.social_platforms import (
    SocialContentType,
    SocialPlatform,
    SocialPlatformError,
    SocialSearchRequest,
)
from agent.tools.builtin.social_platforms.connectors import (
    XIAOHONGSHU_DEFAULT_SEARCH_MCP_TIMEOUT_SECONDS,
)
from agent.tools.builtin.social_platforms.service import SocialSearchItem
from agent.tools.builtin.web_search.providers.tavily import TavilyProvider
from agent.tools.builtin.web_search.tool import WebSearchTool
from agent.tools.factory import make_default_social_platform_service

from runtime_config import TAVILY_API_KEY, credential_source_for_mode, optional_secret

CASES_PATH = Path(__file__).with_name("quality-cases.json")
QueryKind = Literal["exact_title", "title_fragment", "author_theme", "theme_time"]
QUERY_KINDS: tuple[QueryKind, ...] = (
    "exact_title",
    "title_fragment",
    "author_theme",
    "theme_time",
)
PLATFORMS: tuple[SocialPlatform, ...] = ("xiaohongshu", "bilibili")
PLATFORM_DOMAINS: dict[SocialPlatform, str] = {
    "xiaohongshu": "xiaohongshu.com",
    "bilibili": "bilibili.com",
}
PRECISE_UNAVAILABLE_STATUSES = {
    "auth_required",
    "auth_expired",
    "access_restricted",
    "not_found",
    "rate_limited",
}
PLATFORM_STOP_STATUSES = {
    "auth_required",
    "auth_expired",
    "access_restricted",
    "rate_limited",
}
DEFAULT_CASE_INTERVAL_SECONDS = 15.0


@dataclass(frozen=True)
class QualityCase:
    id: str
    platform: SocialPlatform
    content_type: SocialContentType
    query_kind: QueryKind
    query: str
    target_id: str
    target_url: str
    target_title: str
    target_author: str
    reason: str


@dataclass(frozen=True)
class CaseResult:
    id: str
    platform: SocialPlatform
    content_type: SocialContentType
    query_kind: QueryKind
    target_id: str
    query: str
    platform_rank: int | None
    web_rank: int | None
    incremental_hit: bool
    read_status: str
    search_elapsed_ms: int
    read_elapsed_ms: int | None
    web_elapsed_ms: int
    platform_error: str | None
    platform_error_stage: str | None
    web_error: str | None
    web_result_count: int
    web_domain_correct: bool


def platform_identity(url: str) -> str | None:
    """Return the public stable id for one supported canonical URL."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if host == "www.xiaohongshu.com":
        match = re.fullmatch(r"/explore/([0-9a-fA-F]+)", path)
        return match.group(1).lower() if match is not None else None
    if host == "www.bilibili.com":
        match = re.fullmatch(r"/(?:video/(BV[0-9A-Za-z]+)|read/(cv\d+))", path)
        return (match.group(1) or match.group(2)) if match is not None else None
    return None


def _is_on_domain(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host == domain or host.endswith(f".{domain}")


def load_cases(path: Path = CASES_PATH) -> tuple[str, tuple[QualityCase, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sampled_at = payload.get("sampled_at")
    raw_cases = payload.get("cases")
    if not isinstance(sampled_at, str) or not isinstance(raw_cases, list):
        raise ValueError("quality case file must contain sampled_at and cases")
    cases = tuple(QualityCase(**item) for item in raw_cases)
    _validate_cases(cases)
    return sampled_at, cases


def _validate_cases(cases: tuple[QualityCase, ...]) -> None:
    if len(cases) != 16 or len({case.id for case in cases}) != 16:
        raise ValueError("quality suite requires 16 cases with unique ids")
    for platform in PLATFORMS:
        selected = [case for case in cases if case.platform == platform]
        if len(selected) != 8:
            raise ValueError(f"{platform} requires exactly 8 cases")
        kinds = Counter(case.query_kind for case in selected)
        if kinds != Counter({kind: 2 for kind in QUERY_KINDS}):
            raise ValueError(f"{platform} requires two cases for every query kind")
    xhs_types = Counter(case.content_type for case in cases if case.platform == "xiaohongshu")
    bili_types = Counter(case.content_type for case in cases if case.platform == "bilibili")
    if xhs_types != Counter(note=8) or bili_types != Counter(video=4, article=4):
        raise ValueError("case content-type distribution does not match the frozen design")
    for case in cases:
        if not case.query.strip() or not case.reason.strip():
            raise ValueError(f"case {case.id} has an empty query or reason")
        identity = platform_identity(case.target_url)
        expected = case.target_id.lower() if case.platform == "xiaohongshu" else case.target_id
        if identity != expected:
            raise ValueError(f"case {case.id} target id does not match its URL")


def _rank(items: list[str], target_id: str, *, platform: SocialPlatform) -> int | None:
    expected = target_id.lower() if platform == "xiaohongshu" else target_id
    for index, url in enumerate(items[:10], start=1):
        if platform_identity(url) == expected:
            return index
    return None


def _elapsed_ms(started: float, monotonic: Callable[[], float]) -> int:
    return max(0, round((monotonic() - started) * 1000))


def _read_status(
    service: Any,
    *,
    session_id: str,
    item: SocialSearchItem,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[str, int]:
    started = monotonic()

    def finish(status: str) -> tuple[str, int]:
        return status, _elapsed_ms(started, monotonic)

    expected_kinds = {
        "note": {"note_body"},
        "video": {"video_description", "video_subtitle"},
        "article": {"article_body"},
    }[item.content_type]
    try:
        response = service.read(session_id=session_id, resource_ref=item.resource_ref).response
    except SocialPlatformError as exc:
        if exc.status in PRECISE_UNAVAILABLE_STATUSES:
            return finish(f"unavailable:{exc.status}")
        return finish(f"invalid:{exc.status}")
    if platform_identity(response.canonical_url) != platform_identity(item.url):
        return finish("invalid:identity_mismatch")
    if any(
        evidence.kind in expected_kinds and evidence.text.strip() for evidence in response.evidence
    ):
        return finish("content")
    if response.unavailable_parts or response.unavailable_reasons:
        return finish("unavailable:structured")
    return finish("invalid:empty")


def _run_case(
    case: QualityCase,
    service: Any,
    web_tool: WebSearchTool,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> CaseResult:
    session_id = f"social-quality-{case.id}"
    platform_rank: int | None = None
    read_status = "not_attempted"
    read_elapsed_ms: int | None = None
    platform_error: str | None = None
    platform_error_stage: str | None = None
    search_started = monotonic()
    try:
        result = service.search(
            session_id=session_id,
            request=SocialSearchRequest(
                platform=case.platform,
                query=case.query,
                content_types=(case.content_type,),
                sort="relevance",
                limit=10,
            ),
        )
        platform_rank = _rank(
            [item.url for item in result.items],
            case.target_id,
            platform=case.platform,
        )
        search_elapsed_ms = _elapsed_ms(search_started, monotonic)
        if platform_rank is not None:
            read_status, read_elapsed_ms = _read_status(
                service,
                session_id=session_id,
                item=result.items[platform_rank - 1],
                monotonic=monotonic,
            )
    except SocialPlatformError as exc:
        search_elapsed_ms = _elapsed_ms(search_started, monotonic)
        platform_error = exc.status
        platform_error_stage = "search"

    web_started = monotonic()
    web_result = web_tool.invoke(
        {
            "query": case.query,
            "include_domains": [PLATFORM_DOMAINS[case.platform]],
        }
    )
    web_elapsed_ms = _elapsed_ms(web_started, monotonic)
    source_urls = web_result.meta.get("source_urls", [])
    web_urls = [url for url in source_urls if isinstance(url, str)]
    web_error = str(web_result.meta.get("error_kind", "unknown")) if web_result.is_error else None
    web_rank = _rank(web_urls, case.target_id, platform=case.platform)
    domain = PLATFORM_DOMAINS[case.platform]
    return CaseResult(
        id=case.id,
        platform=case.platform,
        content_type=case.content_type,
        query_kind=case.query_kind,
        target_id=case.target_id,
        query=case.query,
        platform_rank=platform_rank,
        web_rank=web_rank,
        incremental_hit=platform_rank is not None and web_rank is None,
        read_status=read_status,
        search_elapsed_ms=search_elapsed_ms,
        read_elapsed_ms=read_elapsed_ms,
        web_elapsed_ms=web_elapsed_ms,
        platform_error=platform_error,
        platform_error_stage=platform_error_stage,
        web_error=web_error,
        web_result_count=len(web_urls),
        web_domain_correct=all(_is_on_domain(url, domain) for url in web_urls),
    )


def summarize(results: tuple[CaseResult, ...]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    selected_platforms = tuple(
        platform for platform in PLATFORMS if any(result.platform == platform for result in results)
    )
    for platform in selected_platforms:
        selected = [result for result in results if result.platform == platform]
        hits = [result for result in selected if result.platform_rank is not None]
        kind_hits = {
            kind: sum(
                result.platform_rank is not None for result in selected if result.query_kind == kind
            )
            for kind in QUERY_KINDS
        }
        read_valid = sum(
            result.read_status == "content" or result.read_status.startswith("unavailable:")
            for result in hits
        )
        web_domain_correct = all(result.web_domain_correct for result in selected)
        platform_errors = sum(result.platform_error is not None for result in selected)
        platform_completed = len(selected) - platform_errors
        web_errors = sum(result.web_error is not None for result in selected)
        incremental_hits = sum(result.incremental_hit for result in selected)
        passed = (
            len(selected) == 8
            and len(hits) >= 6
            and all(value >= 1 for value in kind_hits.values())
            and incremental_hits >= 3
            and read_valid == len(hits)
            and web_domain_correct
            and platform_errors == 0
            and web_errors == 0
        )
        summaries[platform] = {
            "passed": passed,
            "platform_top10_hits": len(hits),
            "web_hits": sum(result.web_rank is not None for result in selected),
            "incremental_hits": incremental_hits,
            "query_kind_hits": kind_hits,
            "read_valid": read_valid,
            "read_attempted": len(hits),
            "web_domain_correct": web_domain_correct,
            "platform_errors": platform_errors,
            "platform_completed": platform_completed,
            "conditional_top10_hit_rate": (
                len(hits) / platform_completed if platform_completed else None
            ),
            "web_errors": web_errors,
        }
    return summaries


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--platform", choices=PLATFORMS)
    parser.add_argument("--allow-platform-network", action="store_true")
    parser.add_argument("--allow-web-network", action="store_true")
    parser.add_argument(
        "--case-interval-seconds",
        type=_non_negative_seconds,
        default=DEFAULT_CASE_INTERVAL_SECONDS,
        help="wait between cases; defaults to 15 seconds and never waits after the last case",
    )
    parser.add_argument(
        "--xiaohongshu-search-timeout-seconds",
        type=_positive_seconds,
        help="search-only MCP timeout; defaults to the 75-second production value",
    )
    return parser.parse_args()


def _non_negative_seconds(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("seconds must be non-negative")
    return parsed


def _positive_seconds(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("seconds must be positive")
    return parsed


def run_cases(
    cases: tuple[QualityCase, ...],
    service: Any,
    web_tool: WebSearchTool,
    *,
    case_interval_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[CaseResult, ...]:
    results: list[CaseResult] = []
    for index, case in enumerate(cases, start=1):
        print(f"quality: {index}/{len(cases)} {case.id}", file=sys.stderr, flush=True)
        result = _run_case(case, service, web_tool)
        results.append(result)
        if result.platform_error in PLATFORM_STOP_STATUSES:
            print(
                f"quality: stopped after platform status {result.platform_error}",
                file=sys.stderr,
                flush=True,
            )
            break
        if index < len(cases) and case_interval_seconds > 0:
            print(
                f"quality: waiting {case_interval_seconds:g}s before next case",
                file=sys.stderr,
                flush=True,
            )
            sleep(case_interval_seconds)
    return tuple(results)


def main() -> int:
    args = _parse_args()
    if not args.allow_platform_network or not args.allow_web_network:
        raise RuntimeError("quality requires --allow-platform-network and --allow-web-network")
    sampled_at, all_cases = load_cases(args.cases)
    cases = tuple(
        case for case in all_cases if args.platform is None or case.platform == args.platform
    )
    source = credential_source_for_mode("desktop")
    api_key = optional_secret(source, TAVILY_API_KEY)
    if not api_key:
        raise RuntimeError("quality requires a configured Tavily credential")
    web_tool = WebSearchTool(TavilyProvider(api_key))
    service = make_default_social_platform_service(
        xiaohongshu_search_timeout_seconds=args.xiaohongshu_search_timeout_seconds,
    )
    try:
        frozen_results = run_cases(
            cases,
            service,
            web_tool,
            case_interval_seconds=args.case_interval_seconds,
        )
    finally:
        service.close()
    summaries = summarize(frozen_results)
    passed = all(summary["passed"] for summary in summaries.values())
    payload = {
        "status": "ok" if passed else "threshold_failed",
        "sampled_at": sampled_at,
        "requested_case_count": len(cases),
        "executed_case_count": len(frozen_results),
        "stopped_early": len(frozen_results) < len(cases),
        "case_interval_seconds": args.case_interval_seconds,
        "xiaohongshu_search_timeout_seconds": (
            args.xiaohongshu_search_timeout_seconds
            or XIAOHONGSHU_DEFAULT_SEARCH_MCP_TIMEOUT_SECONDS
        ),
        "platforms": summaries,
        "cases": [asdict(result) for result in frozen_results],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
