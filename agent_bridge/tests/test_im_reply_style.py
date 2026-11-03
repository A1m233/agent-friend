import json
from unittest.mock import MagicMock

import pytest
from agent_bridge.protocols.im.provider import OutboundSendResult
from agent_bridge.protocols.im.reply_style import Bubble, IMReplyStyle, parse_bubbles, send_bubbles


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("可能需要 10 分钟。", "可能需要 1 分钟。"),
        ("打开 https://example.com/a", "打开 https://example.com/b"),
    ],
)
def test_rewrite_keeps_numbers_and_links(original: str, rewritten: str) -> None:
    raw = json.dumps([{"source": original, "bubbles": [rewritten]}])
    with pytest.raises(ValueError):
        parse_bubbles(raw, original)


def test_model_receives_complete_user_message_and_answer() -> None:
    user_message = "之前的经过。" * 400 + "我最想知道的是结尾那段。"
    answer = "结尾还有一小段琴声。"
    model = MagicMock()
    model.generate.return_value.text = json.dumps([{"source": answer, "bubbles": [answer]}])
    style = IMReplyStyle(lambda: True, lambda _: model, lambda: "test")
    style.prepare("s", answer, user_message)
    (request,) = model.generate.call_args.args
    assert json.loads(request.messages[1]["content"]) == {
        "user_message": user_message,
        "answer": answer,
    }


def test_share_can_quote_one_sentence_while_reply_must_cover_all() -> None:
    answer = "我翻了简介。记忆淡了不直接删除。至于效果，我还没有验证。"
    segment = "记忆淡了不直接删除。"
    text = "记忆淡了不会直接删。"
    raw = json.dumps([{"source": segment, "bubbles": [text]}])
    start = answer.index(segment)
    assert parse_bubbles(raw, answer, proactive=True) == [Bubble(text, start, start + len(segment))]
    with pytest.raises(ValueError):
        parse_bubbles(raw, answer)


@pytest.mark.parametrize(
    ("answer", "segment", "text"),
    [
        ("还没确认开通。", "确认开通。", "确认开通。"),
        ("价格是 1.99 元。", "99 元。", "99 元。"),
    ],
)
def test_share_rejects_partial_source(answer: str, segment: str, text: str) -> None:
    with pytest.raises(ValueError):
        parse_bubbles(json.dumps([{"source": segment, "bubbles": [text]}]), answer, proactive=True)


def test_reply_keeps_each_answer_and_accepts_equivalent_uncertainty() -> None:
    answer = "插件能启动。效果我还没有核验。"
    parts = [
        {"source": "插件能启动。", "bubbles": ["插件能启动。"]},
        {"source": "效果我还没有核验。", "bubbles": ["不过，效果我还没核验。"]},
    ]
    assert [b.text for b in parse_bubbles(json.dumps(parts), answer)] == [
        p["bubbles"][0] for p in parts
    ]
    with pytest.raises(ValueError):
        parse_bubbles(json.dumps(parts[:1]), answer)


def test_two_chat_beats_share_one_source_without_counting_negations() -> None:
    source = "对心情的判断不够确定，就不拿出来说。"
    texts = ["这个细节我喜欢", "拿不准心情就先不说 不一定要猜"]
    payload = json.dumps([{"source": source, "bubbles": texts}])
    assert parse_bubbles(payload, source, proactive=True) == [
        Bubble(t, 0, len(source)) for t in texts
    ]


@pytest.mark.parametrize("texts", [[], [""], ["一", "二", "三"], ["一句", 2]])
def test_invalid_share_bubbles_are_rejected(texts: list[object]) -> None:
    payload = json.dumps([{"source": "一句。", "bubbles": texts}])
    with pytest.raises(ValueError):
        parse_bubbles(payload, "一句。", proactive=True)


def test_omitted_source_and_model_failure_use_original() -> None:
    with pytest.raises(ValueError):
        parse_bubbles('[{"source":"找到了。","bubbles":["找到啦。"]}]', "找到了。不过还没验证。")
    model = MagicMock(side_effect=TimeoutError)
    style = IMReplyStyle(lambda: True, model, lambda: "test")
    text = "一个普通的回答。"
    assert style.prepare("s", text) == [Bubble(text, 0, len(text))]
    model.assert_called_once()
    model.reset_mock()
    code = "`git status`"
    assert style.prepare("s", code)[0].text == code
    model.assert_not_called()


@pytest.mark.asyncio
async def test_partial_delivery_never_replays_previous_bubbles() -> None:
    sent = []

    async def send(index: int, bubble: Bubble) -> OutboundSendResult:
        sent.append(bubble.text)
        return OutboundSendResult("failed" if index else "delivered")

    result = await send_bubbles([Bubble(t, i, i + 1) for i, t in enumerate("甲乙丙")], send)
    assert sent == ["甲", "乙"]
    assert result == OutboundSendResult("unknown", error_kind="partial_delivery")
