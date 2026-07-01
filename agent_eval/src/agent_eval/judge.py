"""Deterministic 判分：看 trace 是否满足契约。"""

from __future__ import annotations

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

    required_tool = spec.must_call or contract.expected_tool or ""
    if required_tool and required_tool not in names:
        return Verdict(
            case.id,
            contract.id,
            False,
            f"missing tool {required_tool} (actual names: {names or 'none'})",
        )

    text_lc = trace.final_text.lower()

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

    return Verdict(
        case.id,
        contract.id,
        True,
        f"grounded: tool={required_tool} anchors={anchor_hit}",
    )


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
