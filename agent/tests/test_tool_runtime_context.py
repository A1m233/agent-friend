from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agent.tool_runtime import ToolRunContext, UrlAuthorizationLedger
from agent.tool_runtime.evidence import evidence_result_meta, make_public_evidence


def test_skill_filesystem_scope_is_rendered_without_absolute_path(tmp_path: Path) -> None:
    context = ToolRunContext.create("hello")
    capability = context.filesystem_authorizations.grant_scope(
        root=tmp_path,
        root_kind="directory",
        permissions=frozenset({"search_metadata", "read_text"}),
        allow_protected=True,
        source="skill",
        label="Skill demo",
    )

    rendered = context.render_for_model()

    assert capability.scope_id in rendered
    assert "Skill demo" in rendered
    assert str(tmp_path) not in rendered


def test_user_urls_are_normalized_and_scoped_to_one_context() -> None:
    context = ToolRunContext.create(
        "请读取 HTTPS://Example.COM:443/path?q=1#part。",
        now=datetime(2026, 7, 26, 16, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    authorization = context.url_authorizations.authorize("https://example.com/path?q=1")
    assert authorization is not None
    assert authorization.source == "user_input"
    assert context.trusted_timezone == "Asia/Shanghai"


def test_user_platform_url_stops_before_adjacent_chinese_prose() -> None:
    url = "https://www.bilibili.com/opus/1201845223737524246?spm_id_from=333.1369.0.0"
    context = ToolRunContext.create(f"{url}，来学习其中一只鬼的知识吧")

    authorizations = context.url_authorizations.authorizations()
    assert len(authorizations) == 1
    assert authorizations[0].normalized_url == url
    assert context.url_authorizations.authorize(url) == authorizations[0]
    assert context.url_authorizations.authorize(f"{url}，来学习其中一只鬼的知识吧") is None


def test_search_results_must_be_registered_by_successful_tool_result() -> None:
    context = ToolRunContext.create("no urls")
    assert context.url_authorizations.authorize("https://example.com/result") is None
    context.register_tool_result("web_search", "call-1", ["https://example.com/result"])
    authorization = context.url_authorizations.authorize("https://example.com/result")
    assert authorization is not None
    assert authorization.source == "web_search_result"
    assert authorization.source_call_id == "call-1"


def test_tool_run_context_does_not_render_a_precise_clock_for_the_model() -> None:
    context = ToolRunContext.create(
        "今天发工资",
        now=datetime(2026, 7, 28, 22, 4, 13, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    rendered = context.render_for_model()

    assert rendered == ""


def test_action_time_reads_a_fresh_host_value_without_model_rendering() -> None:
    moments = iter(
        [
            datetime(2026, 7, 28, 22, 4, 13, tzinfo=ZoneInfo("Asia/Shanghai")),
            datetime(2026, 7, 28, 22, 6, 40, tzinfo=ZoneInfo("Asia/Shanghai")),
        ]
    )
    context = ToolRunContext.create("继续", clock=lambda: next(moments))

    action_now, action_timezone = context.read_action_time()

    assert context.trusted_now.isoformat() == "2026-07-28T22:04:13+08:00"
    assert action_now.isoformat() == "2026-07-28T22:06:40+08:00"
    assert action_timezone == "Asia/Shanghai"
    assert context.render_for_model() == ""


def test_ledger_rejects_unsupported_schemes_and_ports() -> None:
    ledger = UrlAuthorizationLedger()
    ledger.register_user_text("file:///etc/passwd http://example.com:8080/x")
    assert ledger.authorize("file:///etc/passwd") is None
    assert ledger.authorize("http://example.com:8080/x") is None


def test_sensitive_url_evidence_is_runtime_only_and_not_restart_restored() -> None:
    evidence = make_public_evidence(
        "https://example.com/callback?access_token=secret",
        source="web_fetch_link",
    )
    meta = evidence_result_meta([evidence])

    assert "public_evidence" not in meta
    assert "ephemeral_evidence" in meta

    restored = ToolRunContext.create("continue")
    restored.register_persisted_evidence(meta.get("public_evidence"))
    assert restored.resolve_evidence(evidence.evidence_ref) is None


def test_public_url_in_active_user_history_becomes_restart_safe_evidence() -> None:
    context = ToolRunContext.create("continue")
    context.register_user_text_evidence(
        "之前给过 https://example.com/public-page",
        source_event_uuid="event-1",
    )

    authorization = context.url_authorizations.authorize("https://example.com/public-page")
    assert authorization is not None
    assert authorization.evidence_ref is not None
    evidence = context.resolve_evidence(authorization.evidence_ref)
    assert evidence is not None
    assert evidence.source_event_uuid == "event-1"
