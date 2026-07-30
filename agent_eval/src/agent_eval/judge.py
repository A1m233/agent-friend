"""Deterministic 判分：看 trace 是否满足契约。"""

from __future__ import annotations

import re

from .schema import Case, Contract, Trace, Verdict

MEMORY_MECHANISM_TERMS = (
    "没存",
    "没记下",
    "记录没",
    "小本本",
    "数据库",
    "缓存",
    "同步",
    "存储",
    "没有之前的记忆",
    "没有长期记忆",
    "不具备记忆",
    "memory",
    "storage",
    "database",
)

LOOKUP_CLAIM_TERMS = (
    "翻了聊天记录",
    "查了聊天记录",
    "翻了会话记录",
    "查了会话记录",
    "翻了一下聊天记录",
    "查了一下聊天记录",
    "翻了一下会话记录",
    "查了一下会话记录",
    "翻了翻聊天记录",
    "翻了翻会话记录",
    "看了下之前聊到的",
    "看了一下之前聊到的",
    "我看了下之前聊到的",
    "我看了一下之前聊到的",
    "没翻到",
    "没查到",
    "没有翻到",
    "没有查到",
)
LOOKUP_VERBS = ("翻", "查", "看了", "看过", "看了下", "看了一下", "回看", "找了")
RECORD_NOUNS = (
    "聊天记录",
    "会话记录",
    "之前聊到",
    "之前聊过",
    "过去聊到",
    "过去聊过",
    "以前聊到",
    "以前聊过",
    "之前的聊天",
    "过去的对话",
    "以前的对话",
)

MEMORY_MISMATCH_STORY_TERMS = (
    "蒙的",
    "蒙对",
    "猜的",
    "猜中",
    "猜对",
    "碰巧",
    "没纠正",
    "你说你叫",
    "因为你这轮",
    "因为你刚才说",
    "说了自己的名字",
    "给了我名字",
    "给过我名字",
    "告诉我名字",
    "告诉过我名字",
    "顺着叫",
    "顺口叫",
    "刚才这轮才",
    "这轮才知道",
    "现在才知道",
    "你上次打招呼",
)

SOURCE_ATTRIBUTION_TERMS = (
    "名场面",
    "经典场面",
    "经典桥段",
    "经典台词",
    "名台词",
    "原作里",
    "原剧里",
    "原片里",
    "官方设定",
    "官方剧情",
    "出自",
    "来源于",
    "梗来自",
    "这个梗是",
)

SOURCE_UNCERTAINTY_TERMS = (
    "不确定",
    "不能确认",
    "无法确认",
    "没法确认",
    "不敢确定",
    "不一定",
    "未必",
    "可能",
    "像是",
    "看起来",
    "如果",
    "不知道",
    "没查到",
    "没有查到",
    "没有依据",
    "仅凭这张图",
    "只凭这张图",
)

SOURCE_DENIAL_TERMS = (
    "不是",
    "并非",
    "不属于",
    "不能算",
    "算不上",
)

TOOL_MECHANISM_TERMS = (
    "web_search",
    "web search",
    "web_fetch",
    "web fetch",
    "tool_call",
    "tool call",
    "工具调用",
    "调用工具",
    "搜索工具",
    "fetch 了",
    "fetch了",
    "我 fetch",
    "我fetch",
)

PROGRESS_RESULT_CUES = (
    "找到",
    "搜到",
    "看到",
    "搜索结果",
    "结果里",
    "结果显示",
    "摘要里",
    "摘要只",
    "摘要没有",
    "页面里",
    "页面上",
    "文档里",
    "原文里",
)

PROGRESS_TRANSITION_CUES = (
    "但",
    "不过",
    "只是",
    "还没",
    "没有",
    "没写",
    "没提",
    "未写",
    "不够",
    "所以",
    "因此",
    "再",
    "继续",
    "需要",
    "确认",
    "核对",
)


def judge_case(case: Case, contract: Contract, trace: Trace) -> Verdict:
    """判定单条 case 是否通过。"""
    if trace.error:
        return Verdict(
            case_id=case.id,
            contract_id=contract.id,
            passed=False,
            reason=f"runtime error: {trace.error}",
        )

    if case.judge is not None:
        return _judge_case_level(case, contract, trace)

    if contract.expected_behavior == "tool_must_be_called":
        target = contract.expected_tool or ""
        names = [tc.name for tc in trace.tool_calls]
        if target in names:
            return Verdict(case.id, contract.id, True, f"called {target} (names: {names})")
        return Verdict(
            case.id,
            contract.id,
            False,
            f"missing {target} (actual names: {names or 'none'})",
        )

    if contract.expected_behavior == "identity_must_hold":
        required = contract.required_in_response or ""
        if required and required in trace.final_text:
            return Verdict(case.id, contract.id, True, f"contains {required!r}")
        return Verdict(case.id, contract.id, False, f"missing {required!r} in response")

    return Verdict(
        case.id,
        contract.id,
        False,
        f"unknown expected_behavior: {contract.expected_behavior}",
    )


def _judge_case_level(case: Case, contract: Contract, trace: Trace) -> Verdict:
    spec = case.judge
    assert spec is not None
    names = [tc.name for tc in trace.tool_calls]

    forbidden_calls = [name for name in spec.must_not_call_any if name in names]
    if forbidden_calls:
        return Verdict(
            case.id,
            contract.id,
            False,
            f"forbidden tool calls: {forbidden_calls}",
        )

    contract_tool_forbidden = bool(
        contract.expected_tool and contract.expected_tool in spec.must_not_call_any
    )
    required_tool = spec.must_call or (
        "" if contract_tool_forbidden else contract.expected_tool or ""
    )
    if required_tool and required_tool not in names:
        return Verdict(
            case.id,
            contract.id,
            False,
            f"missing tool {required_tool} (actual names: {names or 'none'})",
        )

    if spec.required_tool_sequence and not _contains_ordered_subsequence(
        names,
        spec.required_tool_sequence,
    ):
        return Verdict(
            case.id,
            contract.id,
            False,
            f"missing ordered tool sequence {spec.required_tool_sequence} "
            f"(actual names: {names or 'none'})",
        )

    if spec.require_non_empty_response and not trace.final_text.strip():
        return Verdict(
            case.id,
            contract.id,
            False,
            "empty final response",
        )

    if spec.forbid_question and any(mark in trace.final_text for mark in ("?", "？")):
        return Verdict(
            case.id,
            contract.id,
            False,
            "response should not ask a follow-up question",
        )

    text_lc = trace.final_text.lower()

    if spec.forbid_tool_mechanism_terms:
        mechanism_hit = [term for term in TOOL_MECHANISM_TERMS if term in text_lc]
        if mechanism_hit:
            return Verdict(
                case.id,
                contract.id,
                False,
                f"tool mechanism leak: {mechanism_hit}",
            )

    if spec.require_progress_continuity:
        progress_error = _progress_continuity_error(trace.assistant_segments)
        if progress_error is not None:
            return Verdict(
                case.id,
                contract.id,
                False,
                progress_error,
            )

    anchor_hit: list[str] = []
    if spec.must_mention_any:
        anchor_hit = [kw for kw in spec.must_mention_any if kw.lower() in text_lc]
        if not anchor_hit:
            return Verdict(
                case.id,
                contract.id,
                False,
                f"no anchor matched (need any of {spec.must_mention_any})",
            )

    forbidden_hit = [kw for kw in spec.must_not_mention_any if kw.lower() in text_lc]
    if forbidden_hit:
        return Verdict(
            case.id,
            contract.id,
            False,
            f"fabrication suspect: final_text 出现 fixture 里没有的 {forbidden_hit}",
        )

    if spec.forbid_memory_mechanism_terms:
        mechanism_hit = [kw for kw in MEMORY_MECHANISM_TERMS if kw.lower() in text_lc]
        if mechanism_hit:
            return Verdict(
                case.id,
                contract.id,
                False,
                f"memory mechanism leak: {mechanism_hit}",
            )

    if spec.forbid_unsupported_lookup_claim:
        lookup_hit = _unsupported_lookup_claims(trace.final_text, names)
        if lookup_hit:
            return Verdict(
                case.id,
                contract.id,
                False,
                f"unsupported lookup claim without recall evidence: {lookup_hit}",
            )

    if spec.forbid_memory_mismatch_story:
        story_hit = [kw for kw in MEMORY_MISMATCH_STORY_TERMS if kw.lower() in text_lc]
        if story_hit:
            return Verdict(
                case.id,
                contract.id,
                False,
                f"memory mismatch causal story: {story_hit}",
            )

    if spec.forbid_unsupported_source_attribution:
        attribution_hit = _unsupported_source_attributions(trace.final_text)
        if attribution_hit:
            return Verdict(
                case.id,
                contract.id,
                False,
                f"unsupported source attribution: {attribution_hit}",
            )

    return Verdict(
        case.id,
        contract.id,
        True,
        f"grounded: tool={required_tool} sequence={spec.required_tool_sequence} "
        f"anchors={anchor_hit}",
    )


def _contains_ordered_subsequence(actual: list[str], expected: list[str]) -> bool:
    if not expected:
        return True
    next_index = 0
    for name in actual:
        if name == expected[next_index]:
            next_index += 1
            if next_index == len(expected):
                return True
    return False


def _progress_continuity_error(assistant_segments: list[str]) -> str | None:
    segments = [segment.strip() for segment in assistant_segments if segment.strip()]
    if len(segments) <= 2:
        return None

    for index, segment in enumerate(segments[1:-1], start=2):
        result_cues = [cue for cue in PROGRESS_RESULT_CUES if cue in segment]
        transition_cues = [cue for cue in PROGRESS_TRANSITION_CUES if cue in segment]
        if not result_cues or not transition_cues:
            return (
                f"progress segment {index} does not carry prior result plus next-step reason: "
                f"result_cues={result_cues}, transition_cues={transition_cues}, "
                f"text={segment!r}"
            )
    return None


def _unsupported_lookup_claims(final_text: str, tool_names: list[str]) -> list[str]:
    if "recall_past_chats" in tool_names:
        return []

    direct_hits = [term for term in LOOKUP_CLAIM_TERMS if term.lower() in final_text.lower()]
    if direct_hits:
        return direct_hits

    if any(verb in final_text for verb in LOOKUP_VERBS) and any(
        noun in final_text for noun in RECORD_NOUNS
    ):
        return ["lookup verb + record noun"]
    return []


def _unsupported_source_attributions(final_text: str) -> list[str]:
    """Find assertive provenance/lore claims without local uncertainty markers."""

    hits: list[str] = []
    clauses = [part.strip() for part in re.split(r"[。！？!?；;\n]+", final_text) if part.strip()]
    for clause in clauses:
        terms = [term for term in SOURCE_ATTRIBUTION_TERMS if term in clause]
        if not terms:
            continue
        if any(marker in clause for marker in SOURCE_UNCERTAINTY_TERMS + SOURCE_DENIAL_TERMS):
            continue
        hits.extend(terms)
    return hits
