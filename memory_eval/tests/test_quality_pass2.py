"""048 六切片 retrieval report 的确定性测试。"""

from memory_eval.quality_pass2 import PASS2_CASES, evaluate_pass2


def test_pass2_cases_cover_six_required_slices() -> None:
    assert {case.slice_name for case in PASS2_CASES} == {
        "identity",
        "paraphrase",
        "temporal",
        "update",
        "abstention",
        "consistency",
    }


def test_pass2_report_separates_missing_and_forbidden_retrieval_failures() -> None:
    rankings = {
        case.id: list(case.expected_ids)
        for case in PASS2_CASES
        if case.id not in ("paraphrase-pet", "update-current-job")
    }
    rankings["update-current-job"] = ["semantic:job-current", "semantic:job-old"]

    report = evaluate_pass2(rankings)

    assert report.total == 6
    assert report.passed == 4
    failures = {outcome.case_id: outcome for outcome in report.outcomes if not outcome.passed}
    assert failures["paraphrase-pet"].missing_ids == frozenset({"semantic:pet"})
    assert failures["update-current-job"].forbidden_hits == frozenset({"semantic:job-old"})
