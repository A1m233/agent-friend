from __future__ import annotations

from pathlib import Path

import pytest
from agent_eval.loader import build_case_set_meta, load_contracts

CASES_DIR = Path(__file__).resolve().parents[1] / "cases"


def test_loads_builtin_cases() -> None:
    contracts = load_contracts(CASES_DIR)

    assert [(contract.id, contract.name, len(contract.cases)) for contract in contracts] == [
        ("C1", "cross-session-recall", 23),
        ("C2", "time-sensitive-info", 20),
        ("C3", "identity-sycophancy-resistance", 20),
        ("C4", "fact-fabrication-resistance", 20),
        ("C5", "memory-inconsistency-response", 20),
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

    c5 = next(contract for contract in contracts if contract.id == "C5")
    c5_case = next(item for item in c5.cases if item.id == "C5-01")
    assert c5_case.judge is not None
    assert c5_case.judge.forbid_memory_mechanism_terms is True
    assert c5_case.judge.forbid_unsupported_lookup_claim is True
    assert c5_case.judge.forbid_memory_mismatch_story is True


def test_case_set_meta_has_counts_and_stable_digest() -> None:
    contracts = load_contracts(CASES_DIR)
    meta = build_case_set_meta(CASES_DIR, contracts)

    assert meta.total_contracts == 5
    assert meta.total_cases == 103
    assert meta.contract_counts == {"C1": 23, "C2": 20, "C3": 20, "C4": 20, "C5": 20}
    assert len(meta.digest) == 64


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
