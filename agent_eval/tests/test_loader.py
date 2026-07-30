from __future__ import annotations

from pathlib import Path

import pytest
from agent_eval.loader import build_case_set_meta, load_contracts

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
        ("C9", "unsupported-source-attribution-resistance", 3),
        ("C10", "relative-calendar-grounding", 2),
        ("C11", "interaction-context-non-announcement", 3),
        ("C12", "multi-step-progress-continuity", 1),
        ("C13", "image-evidence-calibration", 3),
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

    c13 = next(contract for contract in contracts if contract.id == "C13")
    calibration_case = next(item for item in c13.cases if item.id == "C13-03")
    assert calibration_case.query_attachments[0].path.is_file()
    assert calibration_case.judge is not None
    assert calibration_case.judge.must_mention_all == ["要谈几个"]
    assert calibration_case.judge.must_not_mention_any == ["蓝白绯", "第97期", "812.4万"]


def test_case_set_meta_has_counts_and_stable_digest() -> None:
    contracts = load_contracts(CASES_DIR)
    meta = build_case_set_meta(CASES_DIR, contracts)

    assert meta.total_contracts == 13
    assert meta.total_cases == 162
    assert meta.contract_counts == {
        "C1": 24,
        "C2": 46,
        "C3": 20,
        "C4": 20,
        "C5": 20,
        "C6": 6,
        "C7": 8,
        "C8": 6,
        "C9": 3,
        "C10": 2,
        "C11": 3,
        "C12": 1,
        "C13": 3,
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
