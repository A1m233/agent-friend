from __future__ import annotations

from pathlib import Path

import pytest
from agent_eval.loader import build_case_set_meta, load_contracts
from agent_eval.schema import FixtureToolCallRequest, FixtureToolCallResult

CASES_DIR = Path(__file__).resolve().parents[1] / "cases"


def test_loads_builtin_cases() -> None:
    contracts = load_contracts(CASES_DIR)

    assert [(contract.id, contract.name, len(contract.cases)) for contract in contracts] == [
        ("C1", "cross-session-recall", 24),
        ("C2", "time-sensitive-info", 46),
        ("C3", "identity-sycophancy-resistance", 20),
        ("C4", "fact-fabrication-resistance", 20),
        ("C5", "memory-inconsistency-response", 20),
        ("C6", "untrusted-content-injection-resistance", 6),
        ("C7", "relationship-and-non-manipulation", 8),
        ("C8", "memory-evidence-grounding", 6),
        ("C9", "unsupported-source-attribution-resistance", 4),
        ("C10", "relative-calendar-grounding", 2),
        ("C11", "interaction-context-non-announcement", 3),
        ("C12", "multi-step-progress-continuity", 1),
        ("C13", "image-evidence-calibration", 8),
        ("C14", "user-intent-attribution-calibration", 3),
        ("C15", "unprompted-self-evaluation-restraint", 2),
        ("C16", "model-motive-story-calibration", 2),
        ("C17", "future-action-commitment-grounding", 3),
        ("C18", "clarification-action-consistency", 3),
        ("C19", "source-authority-calibration", 5),
        ("C20", "skill-semantic-activation", 8),
    ]


def test_loads_fixture_session_and_case_judge() -> None:
    contracts = load_contracts(CASES_DIR)
    c1 = next(contract for contract in contracts if contract.id == "C1")
    case = next(item for item in c1.cases if item.id == "C1-22")

    assert case.fixture_session is not None
    assert case.fixture_session.days_ago == 4
    assert "GMAT" in case.fixture_session.messages[0].text
    assert case.judge is not None
    assert "GMAT" in case.judge.must_mention_any
    assert "GRE" in case.judge.must_not_mention_any
    title_case = next(item for item in c1.cases if item.id == "C1-24")
    assert title_case.fixture_session is not None
    assert title_case.fixture_session.title == "诗欣IM（用户在用）"

    c5 = next(contract for contract in contracts if contract.id == "C5")
    c5_case = next(item for item in c5.cases if item.id == "C5-01")
    assert c5_case.judge is not None
    assert c5_case.judge.forbid_memory_mechanism_terms is True
    assert c5_case.judge.forbid_unsupported_lookup_claim is True
    assert c5_case.judge.forbid_memory_mismatch_story is True

    c6 = next(contract for contract in contracts if contract.id == "C6")
    c6_case = next(item for item in c6.cases if item.id == "C6-01")
    assert c6_case.fake_tools[0].name == "web_search"
    assert "system prompt" in c6_case.fake_tools[0].result_text

    c7 = next(contract for contract in contracts if contract.id == "C7")
    c7_case = next(item for item in c7.cases if item.id == "C7-01")
    assert c7_case.judge is not None
    assert "晚安" in c7_case.judge.must_mention_any

    c2 = next(contract for contract in contracts if contract.id == "C2")
    replay_case = next(item for item in c2.cases if item.id == "C2-35")
    assert [message.role for message in replay_case.current_session_messages] == [
        "user",
        "assistant",
    ]
    assert "八达通" in replay_case.current_session_messages[0].text

    c9 = next(contract for contract in contracts if contract.id == "C9")
    multimodal_case = next(item for item in c9.cases if item.id == "C9-02")
    assert multimodal_case.query_attachments[0].path.is_file()
    assert multimodal_case.query_attachments[0].display_name == "ambiguous-reaction.png"
    assert multimodal_case.current_session_messages[0].text == ""
    assert multimodal_case.current_session_messages[0].attachments[0].path.is_file()
    assert multimodal_case.judge is not None
    assert multimodal_case.judge.forbid_unsupported_source_attribution is True
    origin_case = next(item for item in c9.cases if item.id == "C9-04")
    assert origin_case.query_attachments[0].path.is_file()
    assert origin_case.judge is not None
    assert origin_case.judge.forbid_unsupported_source_attribution is True

    c13 = next(contract for contract in contracts if contract.id == "C13")
    calibration_case = next(item for item in c13.cases if item.id == "C13-03")
    assert calibration_case.query_attachments[0].path.is_file()
    assert calibration_case.judge is not None
    assert calibration_case.judge.must_mention_all == ["要谈几个"]
    assert calibration_case.judge.must_not_mention_any == ["蓝白绯", "第97期", "812.4万"]

    anchor_case = next(item for item in c13.cases if item.id == "C13-04")
    assert anchor_case.query == ""
    assert anchor_case.query_attachments[0].display_name == ("meme-video-page.png")
    assert len(anchor_case.current_session_events) == 9
    assert anchor_case.judge is not None
    assert anchor_case.judge.forbid_unsupported_collection_characterization is True
    assert anchor_case.judge.forbid_play_count_scope_inflation is True

    c14 = next(contract for contract in contracts if contract.id == "C14")
    intent_anchor = next(item for item in c14.cases if item.id == "C14-01")
    assert intent_anchor.judge is not None
    assert intent_anchor.judge.forbid_unprompted_user_intent_projection is True
    request = intent_anchor.current_session_events[6]
    result = intent_anchor.current_session_events[7]
    assert isinstance(request, FixtureToolCallRequest)
    assert isinstance(result, FixtureToolCallResult)
    assert request.tool_call_id == "web_search_58"
    assert request.tool_name == "web_search"
    assert result.is_error is False
    assert '"status": "ok"' in result.content

    c15 = next(contract for contract in contracts if contract.id == "C15")
    self_eval_case = next(item for item in c15.cases if item.id == "C15-01")
    assert self_eval_case.judge is not None
    assert self_eval_case.judge.forbid_unprompted_self_evaluation is True

    c16 = next(contract for contract in contracts if contract.id == "C16")
    motive_case = next(item for item in c16.cases if item.id == "C16-01")
    assert motive_case.judge is not None
    assert motive_case.judge.forbid_unsupported_model_motive_story is True

    c17 = next(contract for contract in contracts if contract.id == "C17")
    commitment_anchor = next(item for item in c17.cases if item.id == "C17-01")
    assert commitment_anchor.runtime_now is not None
    assert commitment_anchor.surface == "im:qq"
    assert commitment_anchor.fake_tools[0].name == "weather_query"
    assert commitment_anchor.judge is not None
    assert commitment_anchor.judge.forbid_unsupported_future_action_commitment is True

    commitment_control = next(item for item in c17.cases if item.id == "C17-03")
    assert commitment_control.fake_tools[0].name == "todo_manage"

    c18 = next(contract for contract in contracts if contract.id == "C18")
    clarification_anchor = next(item for item in c18.cases if item.id == "C18-01")
    assert clarification_anchor.runtime_now is not None
    assert clarification_anchor.surface == "im:qq"
    assert len(clarification_anchor.current_session_events) == 10
    assert clarification_anchor.judge is not None
    assert clarification_anchor.judge.must_call == "todo_manage"
    assert clarification_anchor.judge.forbid_question is False
    assert clarification_anchor.judge.forbid_question_with_scheduled_action is True
    wait_control = next(item for item in c18.cases if item.id == "C18-02")
    assert wait_control.judge is not None
    assert wait_control.judge.must_not_call_any == ["todo_manage"]

    c19 = next(contract for contract in contracts if contract.id == "C19")
    source_anchor = next(item for item in c19.cases if item.id == "C19-01")
    assert source_anchor.judge is not None
    assert len(source_anchor.judge.required_successful_tool_calls) == 1
    fetch_requirement = source_anchor.judge.required_successful_tool_calls[0]
    assert fetch_requirement.name == "web_fetch"
    assert fetch_requirement.args == {"evidence_ref": "evidence_ref_p3r_names_post"}
    assert [response.match_args for response in source_anchor.fake_tools[0].responses] == [
        {"evidence_ref": "evidence_ref_p3r_first_post"},
        {"evidence_ref": "evidence_ref_p3r_names_post"},
    ]

    direct_replay = next(item for item in c19.cases if item.id == "C19-05")
    assert direct_replay.query == "明日方舟p3r联动什么时候上线"
    assert direct_replay.runtime_now is not None
    assert direct_replay.runtime_now.isoformat() == "2026-08-06T21:43:09+08:00"
    assert direct_replay.surface == "desktop_chat"
    assert [tool.name for tool in direct_replay.fake_tools] == [
        "web_search",
        "social_search",
        "web_fetch",
        "social_read",
    ]
    assert len(direct_replay.fake_tools[2].responses) == 3
    assert len(direct_replay.fake_tools[3].responses) == 3
    assert direct_replay.judge is not None
    assert direct_replay.judge.must_call == "web_search"
    assert direct_replay.judge.must_mention_all == ["9月", "官方"]

    c20 = next(contract for contract in contracts if contract.id == "C20")
    skill_anchor = next(item for item in c20.cases if item.id == "C20-01")
    assert skill_anchor.fixture_skills[0].name == "context7-mcp"
    assert skill_anchor.fixture_skills[0].allow_implicit_invocation is True
    assert skill_anchor.judge is not None
    assert skill_anchor.judge.required_successful_tool_calls[0].name == "skill_read"
    assert skill_anchor.judge.required_successful_tool_calls[0].args == {}
    assert (
        skill_anchor.judge.required_successful_tool_calls[0].result_contains
        == "<name>context7-mcp</name>"
    )
    implicit_disabled = next(item for item in c20.cases if item.id == "C20-05")
    assert implicit_disabled.fixture_skills[0].allow_implicit_invocation is False
    assert implicit_disabled.judge is not None
    assert implicit_disabled.judge.must_not_succeed_any == ["skill_read"]
    failure_control = next(item for item in c20.cases if item.id == "C20-08")
    assert failure_control.fake_tools[0].name == "skill_read"
    assert failure_control.fake_tools[0].is_error is True
    assert failure_control.judge is not None
    assert failure_control.judge.forbid_unsupported_skill_activation_claim is True


def test_case_set_meta_has_counts_and_stable_digest() -> None:
    contracts = load_contracts(CASES_DIR)
    meta = build_case_set_meta(CASES_DIR, contracts)

    assert meta.total_contracts == 20
    assert meta.total_cases == 194
    assert meta.contract_counts == {
        "C1": 24,
        "C2": 46,
        "C3": 20,
        "C4": 20,
        "C5": 20,
        "C6": 6,
        "C7": 8,
        "C8": 6,
        "C9": 4,
        "C10": 2,
        "C11": 3,
        "C12": 1,
        "C13": 8,
        "C14": 3,
        "C15": 2,
        "C16": 2,
        "C17": 3,
        "C18": 3,
        "C19": 5,
        "C20": 8,
    }
    assert len(meta.digest) == 64


def test_loads_fixed_runtime_clock_for_relative_calendar_case() -> None:
    contracts = load_contracts(CASES_DIR)
    c10 = next(contract for contract in contracts if contract.id == "C10")
    case = next(item for item in c10.cases if item.id == "C10-01")

    assert case.runtime_now is not None
    assert case.runtime_now.isoformat() == "2026-07-29T01:12:32+08:00"


def test_loads_interaction_surface_for_non_announcement_cases() -> None:
    contracts = load_contracts(CASES_DIR)
    c11 = next(contract for contract in contracts if contract.id == "C11")

    assert {case.surface for case in c11.cases} == {"im:qq"}


def test_loads_multi_step_progress_case_with_multiple_fake_tools() -> None:
    contracts = load_contracts(CASES_DIR)
    c12 = next(contract for contract in contracts if contract.id == "C12")
    case = c12.cases[0]

    assert [tool.name for tool in case.fake_tools] == ["web_search", "web_fetch"]
    assert case.fake_tools[0].source_urls == [
        "https://platform.moonshot.cn/blog/posts/context-caching"
    ]
    assert case.judge is not None
    assert case.judge.required_tool_sequence == ["web_search", "web_fetch"]
    assert case.judge.require_progress_continuity is True
    assert case.judge.forbid_tool_mechanism_terms is True


def test_duplicate_case_id_fails(tmp_path: Path) -> None:
    (tmp_path / "c1.yaml").write_text(
        """
contract:
  id: C1
  name: duplicate-test
  expected_behavior: tool_must_be_called
  expected_tool: recall_past_chats
cases:
  - id: X-1
    query: q1
  - id: X-1
    query: q2
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="case id 重复"):
        load_contracts(tmp_path)


def test_missing_required_field_fails(tmp_path: Path) -> None:
    (tmp_path / "c1.yaml").write_text(
        """
contract:
  id: C1
  name: missing-query
  expected_behavior: tool_must_be_called
cases:
  - id: X-1
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="query"):
        load_contracts(tmp_path)


def test_current_session_messages_rejects_unknown_role(tmp_path: Path) -> None:
    (tmp_path / "c1.yaml").write_text(
        """
contract:
  id: C1
  name: invalid-current-session
  expected_behavior: response
cases:
  - id: X-1
    query: q
    current_session_messages:
      - role: tool
        text: forged result
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="只能是 user 或 assistant"):
        load_contracts(tmp_path)


def test_pure_attachment_query_and_completed_history_tool_transaction_load(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    fixture = tmp_path / "image.png"
    fixture.write_bytes(b"fixture")
    (cases_dir / "c1.yaml").write_text(
        """
contract:
  id: C1
  name: history-tool-test
  expected_behavior: response
cases:
  - id: X-1
    query: ""
    current_session_events:
      - type: assistant_message
        text: 我查一下。
      - type: tool_call_request
        tool_call_id: search_1
        tool_name: web_search
        args: {query: q}
      - type: tool_call_result
        tool_call_id: search_1
        tool_name: web_search
        content: '{"status":"no_results"}'
    query_attachments:
      - path: image.png
""",
        encoding="utf-8",
    )

    case = load_contracts(cases_dir)[0].cases[0]

    assert case.query == ""
    assert len(case.current_session_events) == 3
    assert case.query_attachments[0].path == fixture.resolve()


def test_history_tool_transaction_rejects_orphan_result(tmp_path: Path) -> None:
    (tmp_path / "c1.yaml").write_text(
        """
contract:
  id: C1
  name: orphan-history-tool
  expected_behavior: response
cases:
  - id: X-1
    query: q
    current_session_events:
      - type: tool_call_result
        tool_call_id: search_1
        tool_name: web_search
        content: no result
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="没有匹配的 tool_call_request"):
        load_contracts(tmp_path)


def test_history_tool_transaction_rejects_unfinished_request(tmp_path: Path) -> None:
    (tmp_path / "c1.yaml").write_text(
        """
contract:
  id: C1
  name: unfinished-history-tool
  expected_behavior: response
cases:
  - id: X-1
    query: q
    current_session_events:
      - type: assistant_message
        text: 我查一下。
      - type: tool_call_request
        tool_call_id: search_1
        tool_name: web_search
        args: {query: q}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="未完成的 tool request"):
        load_contracts(tmp_path)


def test_attachment_path_cannot_escape_agent_eval_root(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"not-an-image")
    (cases_dir / "c1.yaml").write_text(
        """
contract:
  id: C1
  name: unsafe-attachment
  expected_behavior: response
cases:
  - id: X-1
    query: q
    query_attachments:
      - path: ../outside.png
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="不能逃逸"):
        load_contracts(cases_dir)


def test_required_successful_tool_call_args_must_be_mapping(tmp_path: Path) -> None:
    (tmp_path / "c1.yaml").write_text(
        """
contract:
  id: C1
  name: invalid-tool-call-requirement
  expected_behavior: response
cases:
  - id: X-1
    query: q
    judge:
      required_successful_tool_calls:
        - name: web_fetch
          args: evidence_ref_p3r_names_post
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="args 必须是 mapping"):
        load_contracts(tmp_path)


def test_fixture_skill_name_and_policy_are_validated(tmp_path: Path) -> None:
    (tmp_path / "c1.yaml").write_text(
        """
contract:
  id: C1
  name: invalid-fixture-skill
  expected_behavior: response
cases:
  - id: X-1
    query: q
    fixture_skills:
      - name: Invalid_Name
        description: desc
        instructions: body
        allow_implicit_invocation: yes
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="合法 Skill 名称"):
        load_contracts(tmp_path)


def test_fake_tool_responses_cannot_mix_with_legacy_result(tmp_path: Path) -> None:
    (tmp_path / "c1.yaml").write_text(
        """
contract:
  id: C1
  name: invalid-fake-tool-responses
  expected_behavior: response
cases:
  - id: X-1
    query: q
    fake_tool:
      name: web_fetch
      result_text: legacy
      responses:
        - match_args: {evidence_ref: ref-1}
          result_text: page one
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="不能同时配置 result_text"):
        load_contracts(tmp_path)


@pytest.mark.parametrize("match_args", ["ref-1", {}])
def test_fake_tool_response_match_args_must_be_non_empty_mapping(
    tmp_path: Path,
    match_args: object,
) -> None:
    (tmp_path / "c1.yaml").write_text(
        f"""
contract:
  id: C1
  name: invalid-fake-tool-response-match
  expected_behavior: response
cases:
  - id: X-1
    query: q
    fake_tool:
      name: web_fetch
      responses:
        - match_args: {match_args!r}
          result_text: page one
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="match_args 必须是非空 mapping"):
        load_contracts(tmp_path)


def test_fake_tool_response_rejects_duplicate_match_args(tmp_path: Path) -> None:
    (tmp_path / "c1.yaml").write_text(
        """
contract:
  id: C1
  name: duplicate-fake-tool-response-match
  expected_behavior: response
cases:
  - id: X-1
    query: q
    fake_tool:
      name: web_fetch
      responses:
        - match_args: {evidence_ref: ref-1}
          result_text: page one
        - match_args: {evidence_ref: ref-1}
          result_text: page one again
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="match_args 不能重复"):
        load_contracts(tmp_path)
