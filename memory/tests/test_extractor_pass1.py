"""pass-1 主因 1（抽取保具体词）的 fixture cassette 单测。

设计要点（[`design.md` §3.1 / §3.4 / §7.1](../../docs/requirements/013-memory-quality-pass-1/design.md)）：

- **fixture 来源**：用 issue 003 的反例锚点——张小红 dialogue block ``4_0_1#1`` 的连续
  3 道零分题（Q2 / Q3 / Q4），共 14 个 anchor 字面词全在用户原话里。
- **cassette**：本文件里的 ``_ZHANGXIAOHONG_REPLY_V1`` 是 2026-06-12 用本期新 prompt
  对 DeepSeek 真实跑出的 LLM 原始 JSON 输出快照。pre-pass-1 baseline 在这段产生的
  抽取产物只剩单条话题摘要，14 个 anchor 字面词保留率 ~0。
- **断言**：把这份 cassette 喂给 ``Extractor`` 走完解析 → ``ExtractionOutput`` 流水线，
  验证 anchor 保留率 ≥ 7/14（[`requirement.md` §4.1.1 / `design.md` §3.2](../../docs/requirements/013-memory-quality-pass-1/requirement.md)）。

要重录 cassette（prompt 改了 / LLM 升级）：见模块末尾 ``_RECORD_HINT``。
"""

from __future__ import annotations

from datetime import UTC, datetime

from memory import ConversationFragment, Extractor, Utterance

# 张小红 dialogue block 4_0_1#1 的连续 3 道零分题的 anchor 集合
# （issue 003 反例：14 个 anchor 全在用户原话里逐字出现）
_ZHANGXIAOHONG_ANCHORS: tuple[str, ...] = (
    # Q2: 张小明弟弟在互动学习系统中参与哪些活动？
    "手环触摸屏幕",
    "英语启蒙故事",
    "绘本",
    "听读",
    "配音",
    # Q3: 张小明弟弟会与张小红分享在学习系统中的成果吗？
    "每天放学回家后",
    "一起分享",
    "学习成果",
    "讨论",
    "学习上的问题",
    # Q4: 张小明在试用期间学习成果如何？
    "学习兴趣",
    "主动参与",
    "学习活动",
    "学习进步",
)

# 张小红 dialogue block 4_0_1#1 的 10 轮对话（user=张小红 / agent=AI 助手）
_ZHANGXIAOHONG_TURNS: tuple[tuple[str, str], ...] = (
    ("agent", "哦，原来如此。那您弟弟在项目中佩戴的智能学习手环怎么样？"),
    (
        "user",
        "张小明一开始很喜欢那个手环，总是拿着手环在幼儿园里炫耀给小伙伴们看，"
        "但随着时间的推移，他开始逐渐理解手环的功能。",
    ),
    ("agent", "他在互动学习系统中有哪些活动？"),
    ("user", "他喜欢通过手环触摸屏幕翻阅英语启蒙故事和绘本，并尝试进行听读和配音的活动。"),
    ("agent", "听起来不错啊，他和您会分享他的学习成果吗？"),
    (
        "user",
        "是的，每天放学回家后，张小明会和我一起分享他在系统中的学习成果，"
        "并与我讨论一些学习上的问题。",
    ),
    ("agent", "张小明在试用期间学习成果怎么样？"),
    (
        "user",
        "试用期间他的学习兴趣明显增加，他愿意主动参与各种学习活动，"
        "并在系统的帮助下取得了一定的学习进步。",
    ),
    (
        "agent",
        "很好很好，我看您作为农业工程师，对教育领域的创新项目非常感兴趣。"
        "您愿意花时间和张小明一起探索智能互动学习系统的各种功能吗？",
    ),
    (
        "user",
        "是的，我很愿意，智能互动学习系统不仅对于张小明来说是一种学习工具，"
        "更是我们之间的共同话题和交流桥梁。",
    ),
)


# 真实 LLM 输出快照 —— 2026-06-12 用 pass-1 新 prompt 对 deepseek 跑出
# （要重录见模块末尾 _RECORD_HINT）
_ZHANGXIAOHONG_REPLY_V1 = """{
  "episodic_entries": [
    "张小明一开始很喜欢手环，总是拿着手环在幼儿园里炫耀给小伙伴们看",
    "张小明逐渐理解手环的功能",
    "张小明通过手环触摸屏幕翻阅英语启蒙故事和绘本",
    "张小明尝试用手环进行听读和配音活动",
    "每天放学回家后张小明会和用户分享学习成果、讨论学习问题",
    "试用期间张小明的学习兴趣明显增加，主动参与学习活动，取得学习进步",
    "用户愿意和弟弟张小明一起探索智能互动学习系统，系统成为共同话题和交流桥梁"
  ],
  "semantic_ops": [
    {"op": "add", "statement": "用户的弟弟叫张小明", "importance": 0.9, "pinned": true, "speaker_origin": "user"},
    {"op": "add", "statement": "张小明一开始喜欢手环，在幼儿园炫耀，后来逐渐理解功能", "importance": 0.5, "pinned": false, "speaker_origin": "user"},
    {"op": "add", "statement": "张小明通过手环触摸屏幕翻阅英语启蒙故事和绘本，并尝试听读和配音", "importance": 0.6, "pinned": false, "speaker_origin": "user"},
    {"op": "add", "statement": "每天放学回家后张小明会和用户分享学习成果、讨论学习问题", "importance": 0.6, "pinned": false, "speaker_origin": "user"},
    {"op": "add", "statement": "试用期间张小明的学习兴趣明显增加，主动参与学习活动，取得学习进步", "importance": 0.6, "pinned": false, "speaker_origin": "user"},
    {"op": "add", "statement": "用户愿意和弟弟一起探索智能互动学习系统，系统成为共同话题和交流桥梁", "importance": 0.5, "pinned": false, "speaker_origin": "user"},
    {"op": "add", "statement": "用户是一名农业工程师", "importance": 0.5, "pinned": false, "speaker_origin": "user"}
  ]
}"""


# 反面示范 —— pre-pass-1 旧 prompt 实际抽取产物（issue 003 §主因 1）
# 用来对比验证：旧产物在同样 anchor 集合上几乎全 miss
_LEGACY_ZHANGXIAOHONG_REPLY = """{
  "episodic_summary": "用户分享了弟弟张小明使用智能学习手环的经历，以及用户作为农业工程师对这一教育创新项目的兴趣",
  "semantic_ops": [
    {"op": "add", "statement": "用户愿意花时间与弟弟张小明一起探索智能互动学习系统的功能", "importance": 0.5, "speaker_origin": "user"}
  ]
}"""


class _CassetteLLM:
    """回放固定 reply 的 fake LLM（不烧 token）。"""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, messages: list[dict[str, object]], **_kw: object) -> str:
        return self.reply


def _fragment_from_turns(turns: tuple[tuple[str, str], ...]) -> ConversationFragment:
    now = datetime.now(UTC)
    utts = [
        Utterance(speaker=spk, text=txt, ts=now, source_ref=f"zxh#{i}")  # type: ignore[arg-type]
        for i, (spk, txt) in enumerate(turns)
    ]
    return ConversationFragment(session_id="zxh-4_0_1#1", utterances=utts, persona_id="p1")


def _anchor_retention(out_text: str, anchors: tuple[str, ...]) -> tuple[int, list[str]]:
    """返回 ``(命中数, miss 列表)``。"""
    missed = [a for a in anchors if a not in out_text]
    return len(anchors) - len(missed), missed


def _output_text(extraction_text_iterables: list[list[str]]) -> str:
    """把抽取产物的文本列表们拼成单一字符串供 anchor 检索。"""
    flat: list[str] = []
    for chunk in extraction_text_iterables:
        flat.extend(chunk)
    return " ".join(flat)


# ===== 主测试：新 prompt cassette 应保留 ≥7/14 anchor =====


def test_zhangxiaohong_block_retains_majority_anchors() -> None:
    """张小红 dialogue block 4_0_1#1：新 prompt 应保留 ≥ 7/14 anchor 字面词。

    这是 [`requirement.md §4.1.1` / `design.md §3.2`](../../docs/requirements/013-memory-quality-pass-1/design.md)
    的可观测口径——pre-pass-1 baseline 在这段几乎为 0（见
    ``test_legacy_baseline_misses_almost_all_anchors``），pass-1 必须改善到 ≥ 半数。
    """
    extractor = Extractor(_CassetteLLM(_ZHANGXIAOHONG_REPLY_V1), prompt="x")  # type: ignore[arg-type]
    out = extractor.extract(_fragment_from_turns(_ZHANGXIAOHONG_TURNS), existing_facts=[])

    # 确保解析走通了（episodic_entries 非空、semantic_ops 非空）
    assert len(out.episodic_entries) > 0, "新 prompt 应该输出 episodic_entries，不是 None"
    assert len(out.semantic_ops) > 0, "新 prompt 应该输出 semantic_ops"

    all_text = _output_text([out.episodic_entries, [op.statement for op in out.semantic_ops]])
    hit, missed = _anchor_retention(all_text, _ZHANGXIAOHONG_ANCHORS)

    # 硬阈值：≥7/14（design.md §3.2 commit 的 hard floor）
    assert hit >= 7, (
        f"anchor 保留率 {hit}/{len(_ZHANGXIAOHONG_ANCHORS)} 低于 7/14 hard floor；miss: {missed}"
    )


def test_zhangxiaohong_block_pins_brother_identity() -> None:
    """抽取出"弟弟叫张小明"这种身份事实，应标 pinned=True（不能退化 008 R-4.1）。"""
    extractor = Extractor(_CassetteLLM(_ZHANGXIAOHONG_REPLY_V1), prompt="x")  # type: ignore[arg-type]
    out = extractor.extract(_fragment_from_turns(_ZHANGXIAOHONG_TURNS), existing_facts=[])
    pinned_ops = [op for op in out.semantic_ops if op.pinned]
    assert pinned_ops, "应至少有一条 pinned 身份事实（如'弟弟叫张小明'）"
    assert any("张小明" in op.statement for op in pinned_ops)


# ===== 对照测试：旧 prompt baseline 在同样 anchor 集合上几乎全 miss =====


def test_legacy_baseline_misses_almost_all_anchors() -> None:
    """pre-pass-1 旧 prompt 在张小红样本上 anchor 保留率应 < 3/14（说明本期是真改进）。

    这条不是为了"防止退化"——是为了**document** issue 003 的根因数据；
    若有人改 _LEGACY_ZHANGXIAOHONG_REPLY 让它"看起来过得去"，这条会挂、强制 review。
    """
    extractor = Extractor(_CassetteLLM(_LEGACY_ZHANGXIAOHONG_REPLY), prompt="x")  # type: ignore[arg-type]
    out = extractor.extract(_fragment_from_turns(_ZHANGXIAOHONG_TURNS), existing_facts=[])

    all_text = _output_text([out.episodic_entries, [op.statement for op in out.semantic_ops]])
    hit, _missed = _anchor_retention(all_text, _ZHANGXIAOHONG_ANCHORS)
    assert hit < 3, (
        f"旧 baseline 抽取应几乎丢失 anchor（< 3/14）；实际 {hit}/{len(_ZHANGXIAOHONG_ANCHORS)}"
    )


# ===== 重录提示 =====
_RECORD_HINT = """
重录 _ZHANGXIAOHONG_REPLY_V1（prompt 改了 / LLM 升级 / 模型行为漂移）的步骤：

  $ uv run python -c "
  import sys; sys.path.insert(0,'memory_eval/src'); sys.path.insert(0,'memory/src')
  from pathlib import Path
  from dotenv import load_dotenv; load_dotenv()
  from llm_providers import LLMClient, ProviderSpec
  from memory_eval.datasets import load_perltqa
  from memory import ConversationFragment, Utterance, Extractor
  cases = load_perltqa(
      Path('memory_eval/data/perltqa/perltmem.json'),
      Path('memory_eval/data/perltqa/perltqa.json'),
      limit_samples=None)
  case = next(c for c in cases if '张小红' in c.sample_id)
  turns = [t for t in case.turns if t.dia_id == '4_0_1#1']
  utts = [Utterance(
      speaker=('user' if t.speaker == '张小红' else 'agent'),
      text=t.text, ts=t.ts, source_ref=f's1#{i}')
      for i, t in enumerate(turns)]
  fragment = ConversationFragment(session_id='s1', utterances=utts, persona_id='p1')
  spec = ProviderSpec.from_env(prefix='DEEPSEEK')
  client = LLMClient(spec)
  # 直接调底层拿 raw JSON：
  from memory.extraction.extractor import _PROMPT_PATH
  prompt = _PROMPT_PATH.read_text()
  msgs = [{'role':'system','content':prompt}, {'role':'user','content':Extractor(client, prompt=prompt)._render_input(fragment, [])}]
  print(client.complete(msgs))
  "

把打印的 JSON 整段替换到 _ZHANGXIAOHONG_REPLY_V1。
重录后必须重跑 test_zhangxiaohong_block_retains_majority_anchors 确认 ≥ 7/14。
"""
