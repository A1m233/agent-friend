# 017 · pinned gate 在某些 query 下漏掉身份核心记忆,体感像"丢失了我是谁"

- **状态**：open
- **严重度**：high（核心体验:"agent 记得用户" 是项目 [决策 0001 §1.3](../../decisions/0001-product-vision-and-roadmap/README.md) 列为不可妥协的"记忆是第一护城河"原则）
- **发现日期**：2026-06-18
- **关联需求**：[022-im-channel-and-qq-adapter](../../requirements/022-im-channel-and-qq-adapter/)（IM 真跑 AC-4 首次清晰暴露，但非 IM 特有 — 桌宠新开 session 同样复现）

## 现象

memory.db 里 `用户的名字是 Alex`(pinned=1, importance=1.0)早已沉淀。用户在**新开 session**(无任何 history)里跟 agent 打招呼,**不同措辞决定 agent 是否"认出你"**:

桌宠新开 session 实测(2026-06-18 真跑):

| Session | 用户首条消息 | Agent 回复 |
|---|---|---|
| `9bf326b8-...` | `"你好"` | **"嗨,Alex!这么晚了还没睡啊..."** ✓ 认出 |
| `91c6dd02-...` | `"你还记得我吗"` | "嗯…说实话我不太确定,我印象里我们的对话片段有限..." ✗ 不认 |
| `91c6dd02-...` (续) | `"你还记得我们之前聊过什么吗"` | "嗯,让我想想…我当时好像说我没有长期记忆来着..." ✗ 不认 |

IM 通道(QQ Bot)首条消息也是同样 query 同样症状(`im:qq:demo-user-1.jsonl`)。

## 根因初判

`memory.retrieve` 默认开启 `pinned_relevance_gate=True` / `pinned_gate_mode="lenient"`(013 pass-1 M13.3 引入,目的:按 query 相关性过滤 pinned,减少无关 pinned 灌进 prompt 的噪声)。

实测对比(同 memory.db × 同 persona_id × 不同 query):

| Query | retrieve 返回 pinned 含 "Alex"? |
|---|---|
| `"我是谁"` | ✓ 有 |
| `"我叫什么名字"` | ✓ 有 |
| `"你好"` | ✓ 有 |
| `"我们之前在桌面聊过的事你还记得吗"`(长句) | ✓ 有 |
| `"你还记得我吗"` | **没** |
| `"我们之前聊过什么"` | **没** |
| `"你能想起我吗"` | **没** |

`pinned_relevance_gate` 在 "你还记得我吗" / "你能想起我吗" / "我们之前聊过什么" 这类**只表达记忆查询意图、不包含身份关键词**的 query 上,把 `用户的名字是 Alex` 这种**身份核心 pinned** 误判为"与当前 query 不相关",直接过滤掉。

LLM 收到的 prompt 里因此没有 "用户的名字是 Alex",自然回不出。它的 system prompt 又禁止说"作为 AI / 我没有长期记忆"(见 [issue 015 防失忆话术红线](../015-agent-fabricates-named-entities/README.md)),所以只能用"嗯…我不太确定"这种模糊兜底,体感等价于**完整记忆丢失**。

## 影响与判断

- **违反 决策 0001 §1.3 第 2 条核心原则**:"记忆是第一护城河 · 让用户几乎察觉不到记忆的边界" — agent 在"你还记得我吗"这种**最经典的记忆查询触发句**上反而失忆,正是该原则最不希望发生的反例。
- 不限 IM 通道:桌宠新开 session 复现完全一致,IM 端只是首次清晰复现。
- pre-existing,022 之前可能存在但未暴露(桌宠老用户多在同一 session 长跑,history 已含名字 event)。

## 可选修复方向

1. **022 范围 hot-fix(已实施)**:bridge 装 memory 时显式传 `pinned_relevance_gate=False`,pinned 全量注入,回退 013 pass-1 M13.3 优化。代价 = pinned 噪声略多。这是**立即可用的修法**,本 issue 保持 open 以追踪长期方案。

2. **gate 设计区分"身份核心 pinned"vs"普通 pinned"**:让 importance==1.0 且 pinned=1 的 statement 始终绕过 gate(身份/AI 名字这种"项目协议层"事实),其他 pinned 仍走 gate。改动局限在 `memory/src/memory/retrieval/pinned_gate.py`,不破坏 013 pass-1 噪声控制目标。

3. **改进 gate 的 query 解析**:加"记忆查询意图识别"(如 query 含 `记得 / 想起 / 之前 / 上次` 这类时间/记忆类关键词时,pinned 全量注入,因为 LLM 本就需要"想起来"的素材)。比 ② 更精细,但实现复杂度高一截。

4. **跟踪 issue 不关闭**:即便 ① hot-fix 已落,留 issue 持续观察 — 用户真用一段时间后,验证 hot-fix 是否真够用(pinned 全量注入会不会真带来 prompt 噪声 / token 浪费);若无明显副作用,可考虑下沉到 memory 模块默认值变更;若有副作用,推进 ② 或 ③。
