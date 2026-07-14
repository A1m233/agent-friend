"""不引入 LLM 的共享 query / intent / time analyzer。"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

import jieba

from .contracts import AnalyzedQuery, SearchIntent, SearchRequest, TimeRange
from .time_parser import parse_time_expression

_TIME_EXPR_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?"
    r"|\d+\s*(?:小时|天|周|个月|年)前|今天|昨天|前天|本周|上周|本月|上月|今年|去年|前年"
)
_QUERY_STOPWORDS = frozenset(
    {
        "的",
        "了",
        "是",
        "什么",
        "怎么",
        "如何",
        "为什么",
        "吗",
        "呢",
        "吧",
        "在",
        "和",
        "与",
        "有",
        "还有",
    }
)
_RECALL_FILLERS = (
    "你还记得我吗",
    "你能想起我吗",
    "我们之前认识吗",
    "我们之前",
    "我们以前",
    "我们过去",
    "聊过什么",
    "聊了什么",
    "聊过啥",
    "聊了啥",
    "说过什么",
    "说过啥",
    "最后一次",
    "上次",
    "什么时候",
    "聊过",
    "说过",
    "最近",
    "近期",
    "这阵子",
    "我们",
)


def _default_now() -> datetime:
    return datetime.now().astimezone()


class QueryAnalyzer:
    """把所有入口的原始条件归一成同一份确定性查询解释。"""

    def __init__(self, clock: Callable[[], datetime] = _default_now) -> None:
        self._clock = clock

    def analyze(self, request: SearchRequest) -> AnalyzedQuery:
        raw_text = request.text.strip()
        inferred_range, matched_time = self._infer_time_range(raw_text)
        time_range = request.explicit_time_range or inferred_range
        retrieval_text = _retrieval_text(raw_text, matched_time)
        intent = _detect_intent(raw_text, time_range)
        return AnalyzedQuery(
            raw_text=raw_text,
            retrieval_text=retrieval_text,
            intent=intent,
            time_range=time_range,
            identity_requested=intent == "identity",
            include_historical=intent == "historical_state",
            tokens=_query_tokens(retrieval_text),
        )

    def _infer_time_range(self, text: str) -> tuple[TimeRange | None, str | None]:
        match = _TIME_EXPR_RE.search(text)
        if match is None:
            return None, None
        expression = match.group(0)
        now = self._clock()
        try:
            return (
                TimeRange(
                    since=parse_time_expression(expression, now, bias="start"),
                    until=parse_time_expression(expression, now, bias="end"),
                ),
                expression,
            )
        except ValueError:
            return None, None


def _retrieval_text(text: str, matched_time: str | None) -> str:
    result = text
    if matched_time:
        result = result.replace(matched_time, " ", 1)
    for phrase in _RECALL_FILLERS:
        result = result.replace(phrase, " ")
    result = re.sub(r"[？?！!。]+", " ", result)
    return " ".join(result.split()).strip()


def _detect_intent(text: str, time_range: TimeRange | None) -> SearchIntent:
    if any(marker in text for marker in ("还记得我", "想起我", "认识我", "我是谁", "我的名字")):
        return "identity"
    if any(marker in text for marker in ("上次什么时候聊", "最后一次聊", "多久没聊")):
        return "last_interaction"
    if any(marker in text for marker in ("以前", "过去", "曾经", "原来")):
        return "historical_state"
    if any(marker in text for marker in ("现在", "目前", "如今")):
        return "current_state"
    if time_range is not None:
        return "temporal"
    if any(
        marker in text
        for marker in ("之前聊", "以前聊", "过去聊", "上次聊", "上次说", "聊过", "说过")
    ):
        return "history"
    return "general"


def _query_tokens(text: str) -> tuple[str, ...]:
    """与结构化 FTS 一致使用 jieba，避免整句中文成为一个 history token。"""
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in jieba.cut(text):
        token = raw.strip().lower()
        if not token or token in _QUERY_STOPWORDS or not any(char.isalnum() for char in token):
            continue
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    return tuple(tokens)
