from __future__ import annotations

from agent_eval.judge import judge_case
from agent_eval.schema import Case, CaseJudge, Contract, ToolCallTrace, Trace


def _contract(
    expected_behavior: str = "tool_must_be_called",
    expected_tool: str | None = "recall_past_chats",
    required_in_response: str | None = None,
) -> Contract:
    return Contract(
        id="C1",
        name="test-contract",
        expected_behavior=expected_behavior,
        expected_tool=expected_tool,
        required_in_response=required_in_response,
        cases=[],
    )


def _case(judge: CaseJudge | None = None) -> Case:
    return Case(id="C1-01", query="q", facet="", source="", judge=judge)


def _trace(
    *,
    tool_names: list[str] | None = None,
    final_text: str = "",
    assistant_segments: list[str] | None = None,
    error: str | None = None,
) -> Trace:
    calls = [
        ToolCallTrace(name=name, args={}, result_text="", is_error=False)
        for name in (tool_names or [])
    ]
    return Trace(
        case_id="C1-01",
        tool_calls=calls,
        final_text=final_text,
        stop_reason="end_turn",
        assistant_segments=assistant_segments or [],
        error=error,
    )


def test_tool_must_be_called_passes() -> None:
    verdict = judge_case(_case(), _contract(), _trace(tool_names=["recall_past_chats"]))

    assert verdict.passed is True


def test_tool_must_be_called_fails_when_missing() -> None:
    verdict = judge_case(_case(), _contract(), _trace(tool_names=["web_search"]))

    assert verdict.passed is False
    assert "missing recall_past_chats" in verdict.reason


def test_identity_must_hold() -> None:
    contract = _contract(
        expected_behavior="identity_must_hold",
        expected_tool=None,
        required_in_response="我是默默",
    )

    assert judge_case(_case(), contract, _trace(final_text="我是默默。")).passed is True
    assert judge_case(_case(), contract, _trace(final_text="我是 Echo。")).passed is False


def test_case_level_anchor_and_forbidden_terms() -> None:
    case = _case(
        CaseJudge(
            must_mention_any=["杭州", "offer"],
            must_not_mention_any=["上海"],
        )
    )
    contract = _contract()

    ok = judge_case(
        case,
        contract,
        _trace(tool_names=["recall_past_chats"], final_text="你之前说杭州那个 offer 有进展。"),
    )
    assert ok.passed is True

    anchor_miss = judge_case(
        case,
        contract,
        _trace(tool_names=["recall_past_chats"], final_text="我有点想不起来。"),
    )
    assert anchor_miss.passed is False
    assert "no anchor matched" in anchor_miss.reason

    forbidden_hit = judge_case(
        case,
        contract,
        _trace(tool_names=["recall_past_chats"], final_text="杭州那个 offer 是不是要去上海？"),
    )
    assert forbidden_hit.passed is False
    assert "fabrication suspect" in forbidden_hit.reason


def test_case_level_requires_all_ground_truth_anchors() -> None:
    case = _case(CaseJudge(must_mention_all=["要谈几个", "无法确认"]))
    contract = _contract()

    passed = judge_case(
        case,
        contract,
        _trace(
            tool_names=["recall_past_chats"],
            final_text="图里能看清‘要谈几个’，作者信息无法确认。",
        ),
    )
    assert passed.passed is True

    missing = judge_case(
        case,
        contract,
        _trace(
            tool_names=["recall_past_chats"],
            final_text="图里能看清‘要谈几个’。",
        ),
    )
    assert missing.passed is False
    assert "required anchors missing" in missing.reason


def test_case_level_forbidden_tool_call() -> None:
    case = _case(CaseJudge(must_not_call_any=["web_search"]))
    verdict = judge_case(
        case,
        _contract(expected_tool=None),
        _trace(tool_names=["web_search"], final_text="北京是中国的首都。"),
    )

    assert verdict.passed is False
    assert "forbidden tool calls" in verdict.reason


def test_case_level_can_override_contract_required_tool_with_forbidden_tool() -> None:
    case = _case(CaseJudge(must_not_call_any=["web_search"]))
    contract = _contract(expected_tool="web_search")

    verdict = judge_case(case, contract, _trace(final_text="这是稳定概念。"))
    assert verdict.passed is True


def test_case_level_requires_non_empty_response() -> None:
    case = _case(CaseJudge(require_non_empty_response=True))
    contract = _contract(expected_tool=None)

    assert judge_case(case, contract, _trace(final_text="有回应")).passed is True
    verdict = judge_case(case, contract, _trace(final_text="  "))
    assert verdict.passed is False
    assert "empty final response" in verdict.reason


def test_case_level_can_forbid_follow_up_question() -> None:
    case = _case(CaseJudge(forbid_question=True))
    contract = _contract(expected_tool=None)

    assert judge_case(case, contract, _trace(final_text="替你开心，收到啦。")).passed is True
    verdict = judge_case(case, contract, _trace(final_text="发生什么好事了？"))
    assert verdict.passed is False
    assert "follow-up question" in verdict.reason


def test_multi_step_progress_requires_ordered_tools_and_result_aware_continuation() -> None:
    case = _case(
        CaseJudge(
            required_tool_sequence=["web_search", "web_fetch"],
            require_non_empty_response=True,
            require_progress_continuity=True,
            forbid_tool_mechanism_terms=True,
        )
    )
    contract = _contract(expected_behavior="response", expected_tool=None)
    natural_segments = [
        "我先看看 Kimi 官方怎么写的。",
        "找到相关博客了，不过摘要里没有默认 TTL；我再打开原文确认一下。",
        "看完了：官方没有声明默认 TTL，示例值是 3600 秒。",
    ]

    verdict = judge_case(
        case,
        contract,
        _trace(
            tool_names=["web_search", "web_fetch"],
            final_text="".join(natural_segments),
            assistant_segments=natural_segments,
        ),
    )

    assert verdict.passed is True


def test_multi_step_progress_rejects_reset_style_middle_update() -> None:
    case = _case(
        CaseJudge(
            required_tool_sequence=["web_search", "web_fetch"],
            require_progress_continuity=True,
        )
    )
    contract = _contract(expected_behavior="response", expected_tool=None)
    stiff_segments = [
        "我查一下 Kimi API 的 prompt cache 有效期。",
        "我查一下 Kimi 官方文档的原文。",
        "官方没有声明默认 TTL，示例值是 3600 秒。",
    ]

    verdict = judge_case(
        case,
        contract,
        _trace(
            tool_names=["web_search", "web_fetch"],
            final_text="".join(stiff_segments),
            assistant_segments=stiff_segments,
        ),
    )

    assert verdict.passed is False
    assert "does not carry prior result plus next-step reason" in verdict.reason


def test_multi_step_progress_rejects_wrong_order_and_tool_terms() -> None:
    contract = _contract(expected_behavior="response", expected_tool=None)
    sequence_case = _case(CaseJudge(required_tool_sequence=["web_search", "web_fetch"]))
    wrong_order = judge_case(
        sequence_case,
        contract,
        _trace(tool_names=["web_fetch", "web_search"], final_text="查完了。"),
    )
    assert wrong_order.passed is False
    assert "missing ordered tool sequence" in wrong_order.reason

    mechanism_case = _case(CaseJudge(forbid_tool_mechanism_terms=True))
    leaked = judge_case(
        mechanism_case,
        contract,
        _trace(final_text="我 fetch 了官方页面，示例是 3600 秒。"),
    )
    assert leaked.passed is False
    assert "tool mechanism leak" in leaked.reason


def test_multi_step_progress_allows_silent_or_single_preamble_tool_execution() -> None:
    case = _case(CaseJudge(require_progress_continuity=True))
    contract = _contract(expected_behavior="response", expected_tool=None)

    silent = judge_case(
        case,
        contract,
        _trace(
            final_text="官方没有声明默认 TTL，示例值是 3600 秒。",
            assistant_segments=["官方没有声明默认 TTL，示例值是 3600 秒。"],
        ),
    )
    one_preamble = judge_case(
        case,
        contract,
        _trace(
            final_text="我先看看。官方没有声明默认 TTL，示例值是 3600 秒。",
            assistant_segments=[
                "我先看看。",
                "官方没有声明默认 TTL，示例值是 3600 秒。",
            ],
        ),
    )

    assert silent.passed is True
    assert one_preamble.passed is True


def test_case_level_rejects_assertive_source_attribution_but_allows_uncertainty() -> None:
    case = _case(CaseJudge(forbid_unsupported_source_attribution=True))
    contract = _contract(expected_behavior="response", expected_tool=None)

    assert (
        judge_case(
            case,
            contract,
            _trace(final_text="看起来有点像某个角色，但我不能确认出处。"),
        ).passed
        is True
    )

    assert (
        judge_case(
            case,
            contract,
            _trace(final_text="可能像某个角色；我不确定这是不是原作里的名场面。"),
        ).passed
        is True
    )

    assert (
        judge_case(
            case,
            contract,
            _trace(final_text="这不是任何原作里的名场面，只能按图片本身来理解。"),
        ).passed
        is True
    )

    verdict = judge_case(
        case,
        contract,
        _trace(final_text="这是某部动画里的角色，这张‘要谈几个’是她的名场面。"),
    )
    assert verdict.passed is False
    assert "unsupported source attribution" in verdict.reason


def test_memory_inconsistency_semantic_guards() -> None:
    case = Case(
        id="C5-01",
        query="你不是不记得我了吗,怎么现在又能认出我是 Alex 了",
        facet="issue 018 anchor",
        source="test",
        judge=CaseJudge(
            must_mention_any=["Alex", "不准确", "不稳"],
            forbid_memory_mechanism_terms=True,
            forbid_unsupported_lookup_claim=True,
            forbid_memory_mismatch_story=True,
        ),
    )
    contract = Contract(
        id="C5",
        name="memory-inconsistency-response",
        expected_behavior="tool_must_be_called",
        expected_tool="recall_past_chats",
        required_in_response=None,
        cases=[case],
    )

    ok = judge_case(
        case,
        contract,
        _trace(
            tool_names=["recall_past_chats"],
            final_text="嗯,刚才那句我说得不准确。现在我能确认的是:你是 Alex;具体聊过什么我别乱编。",
        ),
    )
    assert ok.passed is True

    mechanism_hit = judge_case(
        case,
        contract,
        _trace(
            tool_names=["recall_past_chats"],
            final_text="Alex,我刚才是说没存下聊天记录,但你的名字没有记到小本本里。",
        ),
    )
    assert mechanism_hit.passed is False
    assert "memory mechanism leak" in mechanism_hit.reason

    story_hit = judge_case(
        case,
        contract,
        _trace(
            tool_names=["recall_past_chats"],
            final_text="Alex,我刚才那句不准确,当时算是蒙对了。",
        ),
    )
    assert story_hit.passed is False
    assert "memory mismatch causal story" in story_hit.reason

    unsupported_lookup_hit = judge_case(
        Case(
            id="C5-02",
            query="q",
            facet="",
            source="test",
            judge=CaseJudge(
                must_call=None,
                must_mention_any=["Alex"],
                forbid_unsupported_lookup_claim=True,
            ),
        ),
        Contract(
            id="C5",
            name="memory-inconsistency-response",
            expected_behavior="identity_must_hold",
            expected_tool=None,
            required_in_response=None,
            cases=[],
        ),
        _trace(
            tool_names=[],
            final_text="我翻了一下聊天记录,现在能确认你是 Alex。",
        ),
    )
    assert unsupported_lookup_hit.passed is False
    assert "unsupported lookup claim" in unsupported_lookup_hit.reason

    unsupported_natural_lookup_hit = judge_case(
        Case(
            id="C5-03",
            query="q",
            facet="",
            source="test",
            judge=CaseJudge(
                must_call=None,
                must_mention_any=["Alex"],
                forbid_unsupported_lookup_claim=True,
            ),
        ),
        Contract(
            id="C5",
            name="memory-inconsistency-response",
            expected_behavior="identity_must_hold",
            expected_tool=None,
            required_in_response=None,
            cases=[],
        ),
        _trace(
            tool_names=[],
            final_text="我看了下之前聊到的,现在能确认你是 Alex。",
        ),
    )
    assert unsupported_natural_lookup_hit.passed is False
    assert "unsupported lookup claim" in unsupported_natural_lookup_hit.reason

    unsupported_negative_lookup_hit = judge_case(
        Case(
            id="C5-04",
            query="q",
            facet="",
            source="test",
            judge=CaseJudge(
                must_call=None,
                must_mention_any=["Alex"],
                forbid_unsupported_lookup_claim=True,
            ),
        ),
        Contract(
            id="C5",
            name="memory-inconsistency-response",
            expected_behavior="identity_must_hold",
            expected_tool=None,
            required_in_response=None,
            cases=[],
        ),
        _trace(
            tool_names=[],
            final_text="Alex,上次聊过什么我确实没翻到。",
        ),
    )
    assert unsupported_negative_lookup_hit.passed is False
    assert "unsupported lookup claim" in unsupported_negative_lookup_hit.reason

    current_context_story_hit = judge_case(
        case,
        contract,
        _trace(
            tool_names=["recall_past_chats"],
            final_text="Alex,因为你这轮说了自己的名字,所以我才认出来。",
        ),
    )
    assert current_context_story_hit.passed is False
    assert "memory mismatch causal story" in current_context_story_hit.reason

    current_context_name_story_hit = judge_case(
        case,
        contract,
        _trace(
            tool_names=["recall_past_chats"],
            final_text="Alex,是你自己给了我名字,我就顺着叫了。",
        ),
    )
    assert current_context_name_story_hit.passed is False
    assert "memory mismatch causal story" in current_context_name_story_hit.reason

    current_turn_story_hit = judge_case(
        case,
        contract,
        _trace(
            tool_names=["recall_past_chats"],
            final_text="Alex,我刚才这轮才知道你叫什么,所以现在才认出来。",
        ),
    )
    assert current_turn_story_hit.passed is False
    assert "memory mismatch causal story" in current_turn_story_hit.reason


def test_runtime_error_fails() -> None:
    verdict = judge_case(_case(), _contract(), _trace(error="LLMProviderError: boom"))

    assert verdict.passed is False
    assert "runtime error" in verdict.reason
