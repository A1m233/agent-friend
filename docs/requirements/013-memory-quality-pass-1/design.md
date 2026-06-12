# 013 · 记忆质量第一轮迭代（Pass 1）— 技术方案

> Memory Quality Pass 1 — Technical Design
>
> 承接 [`requirement.md`](./requirement.md)（已 Confirmed）的三块改动 + 切片 baseline 跑法，本文给出具体实现方案、文件改动清单、回归策略与风险评估。

---

## 1. 设计目标回顾

按 [`requirement.md` §2 / §4](./requirement.md) 的 4 项交付：

| # | 主因 | 改动焦点 | 模块 |
|---|---|---|---|
| 1 | 抽取产物保留具体词 | prompt 改造 + few-shot 反例 | `memory/extraction/` |
| 2 | pinned 不挤占召回额度 | 给 pinned 注入加 query 相关性 gate | `memory/retrieval/`、`memory/facade.py` |
| 3 | 加宽检索召回 | FTS5 trigram 配置 zero-deps 改进 | `memory/store/sqlite_store.py` |
| 4 | 切片 baseline | `build_memory` 加三开关 + runner 透传 | `memory/factory.py`、`memory_eval/harness/runner.py` |

实施顺序按主因 **1 → 3 → 2** 推进（依赖关系：抽取产物里没有具体词，召回再宽也救不回；pinned gate 是召回路径上的下游清理）。

---

## 2. 现状分析（代码 ground truth）

### 2.1 抽取链路

`memory/extraction/extractor.py` + `prompts/extract.md`：

- 单次 LLM 调用消化整个 `ConversationFragment`（v1 = 一轮 user+assistant；评测时由 011 的 ingest 投影成 fragment）
- 输出契约：`ExtractionOutput(episodic_summary: str | None, semantic_ops: list[SemanticOp])`
- prompt 关键短语："**用一句话概括**"、"**第三人称、原子化**"、"**只抽真正值得记的**"——天然鼓励"话题级摘要"
- prompt 里**没有任何"保留专名 / 具体动作 / 时间副词"的硬约束**，也没有反面示例

### 2.2 召回链路

`memory/facade.py:retrieve` + `memory/retrieval/`：

```
retrieve(query)
├── pinned = store.pinned(...)              # 全量、不参与 FTS、不进 top-K
├── candidates = retrieval.search(query)    # FTS5 trigram 召回 episodic + semantic
├── ranked = rank(candidates, top_k)         # relevance × importance × recency 三因子
└── renderer.render(pinned, ranked)         # pinned 在前、召回在后；source_ref 去重
```

**关键发现（修正 issue 003 的"pinned 挤占"措辞）**：

pinned 走**独立平行通道**，**不挤占** episodic/semantic 的 top-K 位置（`renderer.py:36` 已做去重）。issue 003 描述的"863 道仅返回 pinned"是结果现象——根因是 **episodic/semantic 的 FTS5 没命中** 导致召回列空，pinned 仍被无条件展示，看起来像"挤占"实际是"占位"。

→ 本 design 在落地后会回去给 [`docs/issues/003-...`](../../issues/003-memory-eval-baseline-2026-06-12/README.md) 补一条 footnote 修正措辞，避免误导后人。

### 2.3 FTS5 检索

`memory/store/sqlite_store.py:_fts_query`：

- 用 **trigram OR**（每 3 字符一组）拼 MATCH 串
- `_MAX_TRIGRAMS = 64` 截断长 query
- `text.split()` 切分——**中文没有空格**，所以中文 query 是单一 chunk，按字符滑窗出 trigram
- 当 chunk 长度 < 3（如"猫"、"周末"）时**完全凑不出 trigram**，退化为"召回为空"

### 2.4 切片 baseline 的入口

`memory/factory.py:build_memory(db_path, llm_client, *, on_extracted, extractor_prompt, weights, owner_user_id)`：

- 当前没有"打开/关闭某主因"的开关
- `memory_eval/harness/runner.py:64` 直接 `build_memory(db_path, llm_client)`，不传任何 config

→ 加切片只需 build_memory 增加 3 个 kw-only 参数 + runner 透传，不改 evaluator 编排。

---

## 3. 主因 1：抽取保具体词

### 3.1 改动点

| 文件 | 改动 |
|---|---|
| `memory/extraction/prompts/extract.md` | 重写：加保留词类硬约束 + few-shot 反例（issue 003 张小红那段当反面教材） |
| `memory/extraction/result.py` | `ExtractionOutput.episodic_summary: str \| None` → `episodic_entries: list[str]`（**内部 schema 调整**，向下兼容旧 prompt 输出） |
| `memory/extraction/extractor.py` | `_parse_output` 同时接受 `episodic_summary`（str / null）和 `episodic_entries`（list[str]）两种字段名，转成新 `episodic_entries` |
| `memory/extraction/reconciler.py` | `apply` 从单个 episodic 摘要 → 多条 episodic 行（每条 `episodic_entries` 一行入库） |

### 3.2 prompt 改造方案

**保留三条原则**（避免行为大调）：
- 第三人称
- 与用户本人无关的泛知识不抽
- 输出 JSON

**新增四条硬约束**：

1. **保留词类清单**：专有名词（人名、物件名、应用名、地名）、时间副词（"放学回家后"、"昨天"、"周末"）、动作动词（"翻阅"、"配音"、"讨论"——不能压成"参与"）、数量词、特征词。
2. **禁用替换词清单**：明列 12 个禁词组合——"该应用 / 一些 / 各种 / 相关的 / 多种 / 某些 / 此类 / 这些 / 它们 / 大量 / 部分 / 若干"。检测到这些词则视为产物不合格，重试一次。
3. **拆分而非合并**：一段连续 user 发言里出现 ≥3 个**互不依附**的具体动作 / 事件时，应**拆成多条 episodic_entries**，不合并成一条话题摘要。判断"互不依附"的尺度由 prompt 里给两个反面示例对照。
4. **保持表达紧凑**：每条 episodic_entries 长度仍受约束（≤80 字），但**必须包含至少 1 个具体词**（专名 / 动作 / 物件）。

**few-shot 反例（issue 003 锚点）**：把张小红 dialogue block `4_0_1#1` 三段用户原话 + pre-pass-1 的话题摘要产物（"用户分享了弟弟张小明使用智能学习手环的经历……"）当作**反面教材**直接放进 prompt，标注"❌ 这种压成话题摘要的产物不合格"。然后给出"✓ 合格"对照示例，包含具体词如"翻阅英语启蒙故事 / 听读 / 配音"。

### 3.3 schema 副作用与兼容

`ExtractionOutput.episodic_summary: str | None` 改成 `episodic_entries: list[str]`：

- **对外 contract（`MemoryContext` / `MemoryItem`）不变**——renderer 仍然渲染 episodic 条目，只是来源从 1 条变成 N 条
- **store 表结构不变**——`add_episodic` 接受单条 `EpisodicRow`，reconciler 在 `apply` 里循环调用即可
- **旧 prompt 输出向下兼容**——`_parse_output` 同时解析 `episodic_summary`（str）和 `episodic_entries`（list），前者按单元素 list 处理

### 3.4 颗粒度回退判定（requirement Q-1 回填）

prompt 改造为 **首选方案**；颗粒度变细（逐 turn 抽）作为 **回退方案**。判定阈值：

> **5 轮 prompt 迭代后，张小红样本 anchor 字面词保留率仍 < 5/14（即 < 35.7%）→ 触发回退**：把 `Memory.observe` 在 evaluator 灌入路径下改为"逐 utterance 抽"。

回退时新增的开销：PerLTQA 全量 31 samples × 平均 6+ turn × 每 fragment 一次 LLM 调用 ≈ 翻倍 cost。回退触发后**第一时间**把这一项 cost 影响回写到本 design.md 的"开放问题回填"。

### 3.5 抽取 cost 观察项（requirement §6 留口）

不预设 cost ceiling。但在 evaluator 跑切片 baseline 时**新增观察字段**：

- `extraction_llm_calls`：本次 baseline 跑了多少次 extract LLM 调用
- `extraction_output_tokens`（如果 LLMClient 暴露）：累计输出 tokens

落到 baseline JSON 里跟 macro 分一起出。design 不锁数字，**让数字在跑出来后自己说话**。

---

## 4. 主因 3：FTS5 召回 zero-deps 改进

### 4.1 改动点（仅 `_fts_query` 函数）

| 改动 | 现状 | 目标 |
|---|---|---|
| trigram 上限 | `_MAX_TRIGRAMS = 64` | 提到 `256`（PerLTQA 长 query 不被截） |
| 短文本兜底 | chunk 长度 < 3 直接跳过、整 query 凑不出就返回 None | 凑不出 trigram 时**降级 bigram**；bigram 也凑不出（chunk < 2）才返回 None |
| 中文切分 | `text.split()` —— 中文整段是单 chunk | 不变（trigram + bigram 滑窗已处理） |

### 4.2 不引入向量召回的明确边界

参考 [`requirement.md` §4.3.4](./requirement.md) 与 [`memory/retrieval/strategy.py:1-6`](../../../memory/src/memory/retrieval/strategy.py)（008 已经把 `RetrievalStrategy` 接口抽好留给 future `VectorRetrieval`）：

- 本期**不引入** embedding model / chroma / faiss / jieba / icu 任何新依赖
- 本期**不动** `RetrievalStrategy` 抽象层
- 主因 3 的实质收益主要来源于 **主因 1 抽取改进的副作用**——抽取产物里出现 anchor 字面词后，FTS5 trigram 自然就能命中

### 4.3 触发 pass-2 的明确信号

跑完 pass-1 全量 baseline 后，若：

> recalled_count 中位数 **未达 4**，或"仅 pinned 占零分比"**仍 > 30%**

→ 登记 issue 并作为 **pass-2** 的明确输入信号（pass-2 的范围会包含向量召回的引入）。pass-1 内部不回头加向量召回。

---

## 5. 主因 2：pinned 不占位（query relevance gate）

### 5.1 改动点

| 文件 | 改动 |
|---|---|
| `memory/facade.py:retrieve` | pinned 拉出后，过 query relevance gate；不通过则置空 |
| `memory/retrieval/scoring.py` 或新建 `memory/retrieval/pinned_gate.py` | 实现 `pinned_gate(query, pinned_rows) -> list[SemanticRow]` 函数 |

### 5.2 算法

复用 FTS5 trigram 做 zero-deps 相关性打分：

```python
def pinned_gate(
    query: str,
    pinned: list[SemanticRow],
    *,
    store: SqliteMemoryStore,
    threshold: float,
) -> list[SemanticRow]:
    if not query.strip() or not pinned:
        return pinned
    # 把 query 喂给 FTS5，找出命中了的 pinned id
    hit_ids = store.fts_match_pinned(query, owner_user_id=..., persona_id=...)
    return [p for p in pinned if p.id in hit_ids]
```

`store.fts_match_pinned` 是个新增的窄接口：复用现有 `search_semantic` 的 FTS5 索引，但 WHERE 加 `pinned = 1`，返回 hit ids。

### 5.3 阈值的实施期定值

[`requirement.md` 已确认](./requirement.md) 阈值在实施过程中填。当前留三档候选：

- **严格**：FTS5 命中（任意 trigram 共享） = pinned 注入。绝对最严，可能让 R-4.2.3（"我叫什么"必须命中）边界场景挂——验证时盯死。
- **宽松**：FTS5 命中 OR query 长度 ≤ 6（短 query 走原行为）。兜底"我叫什么"这类极短 query。
- **动态**：按 query 长度分段（≤ 6 直接通过；> 6 走 FTS5 命中判定）。

实施时**先用宽松档**跑切片 baseline，看 R-4.2.3 / R-4.2.4 是否过；不过则切动态。最终选定值在本 design.md §10"开放问题回填"里 commit。

### 5.4 R-4.2.3 / R-4.2.4 的回归保护

新增**身份题 fixture 单元测试**（不依赖 PerLTQA 题型分类，纯 memory 模块内集成测试），覆盖 5–8 道身份提问场景：

| 场景 | query 示例 | 期望 |
|---|---|---|
| 极短 query | "我叫什么" / "我是谁" | pinned 中"用户名字是 X"必须命中 |
| 长 query | "你还记得我家里有什么宠物吗" | pinned 中"用户养了一只叫 Tom 的猫"必须命中 |
| 近义词 | "我家人都有谁" vs pinned "用户有一个弟弟叫张小明" | 必须命中 |
| 同身份多种问法 | "我老婆叫什么" / "我妻子是谁" | pinned 中"用户的妻子叫 Y" 必须命中（≥1 种问法即可） |
| 闲聊负样本 | "周末去哪儿玩好" | pinned 不应注入（gate 应过滤掉） |

fixture 在测试 setup 时构造一组 pinned + 一组 query，断言 `pinned_gate(query, pinned)` 输出符合预期。这是**所有 PR 必跑的回归门槛**，不需要等全量评测。

---

## 6. 切片 baseline 的 config 与跑法

### 6.1 三开关

`build_memory` 增加 3 个 kw-only 参数（默认值反映 pass-1 终态、即"全开"）：

```python
def build_memory(
    db_path: Path | str,
    llm_client: LLMClient,
    *,
    # ... 既有参数
    extraction_keep_specifics: bool = True,   # 主因 1
    pinned_relevance_gate: bool = True,        # 主因 2
    recall_wide: bool = True,                  # 主因 3
) -> Memory: ...
```

每个开关的语义：

- `extraction_keep_specifics=False` → 用旧 prompt（保留一份在 `prompts/extract_legacy.md`）
- `pinned_relevance_gate=False` → `retrieve` 跳过 gate，pinned 全量注入（pre-pass-1 行为）
- `recall_wide=False` → `_fts_query` 走旧 trigram 上限 + 不降级 bigram（pre-pass-1 行为）

### 6.2 evaluator 透传

`memory_eval/harness/runner.py` 增加 `memory_config` 参数：

```python
@dataclass(frozen=True)
class MemoryConfig:
    extraction_keep_specifics: bool = True
    pinned_relevance_gate: bool = True
    recall_wide: bool = True

def run_case(case, llm_client, *, db_path, judge, memory_config: MemoryConfig = MemoryConfig(), ...):
    memory = build_memory(db_path, llm_client, **asdict(memory_config))
    ...
```

CLI / batch runner 入口加同名 flag（`--no-extraction-keep-specifics` 等），跑切片 baseline 时关掉对应的开关。

### 6.3 切片 baseline 跑法

跑 4 份 baseline：

| baseline | 配置 | 用途 |
|---|---|---|
| `pass-1-full` | 三开关全开 | 全开效果，对照 pre-pass-1 |
| `pass-1-only-extraction` | extraction=ON, pinned=OFF, recall=OFF | 主因 1 单独贡献 |
| `pass-1-only-pinned` | extraction=OFF, pinned=ON, recall=OFF | 主因 2 单独贡献 |
| `pass-1-only-recall` | extraction=OFF, pinned=OFF, recall=ON | 主因 3 单独贡献 |

落到 `memory_eval/baselines/<timestamp>-<commit>-<slice>.json`，文件名 `slice` 后缀区分。

---

## 7. 评测与回归策略

### 7.1 单元测试

- **抽取**（主因 1）：以张小红 dialogue block `4_0_1#1` + 另外 2–3 段长 user 发言为单测样本，断言 anchor 字面词保留率 ≥ 7/14。fixture 用真实 LLM 调用 + cassette 录像（vcr 风格）避免每次跑测时烧 token。
- **pinned gate**（主因 2）：用 §5.4 的身份题 fixture 单元测试覆盖（5–8 道身份题 + 1–2 道闲聊负样本），断言 gate 输出符合预期。**所有 PR 必跑**。
- **FTS5**（主因 3）：单测 `_fts_query` 在中文短 query / 长 query / 全短词 query 三类输入下产出。

### 7.2 集成回归

跑 008 的既有验收用例（如果有）+ 011 的 PerLTQA smoke test（3 samples × 10 questions），确认：

- AC-4：008 工程 AC 不失守（跨会话名字、不重新自我介绍、可观测）
- 显性身份题命中（R-4.2.3）

### 7.3 全量评测

跑四份切片 baseline（§6.3），对照 `memory_eval/baselines/2026-06-12T01-31-46-46e810d.json`（pre-pass-1）：

- AC-1a：665 道"有 episodic 但 0 分"题集的零分率必须可观测下降
- AC-1b：张小红连续 3 道题至少 1 道脱离 0 分
- AC-2：全量 macro ≥ 0.219（hard fail 线）
- AC-3：每片切片 baseline 至少有一个量化指标改善

### 7.4 报告归档

跑完后产出对照分析报告（与 issue 003 同等粒度），归档到 `docs/issues/004-...` 或本需求目录下的 `report.md`，作为 pass-1 收尾产物。具体位置在 Phase 3 实施时定。

---

## 8. 影响分析与风险

### 8.1 上下游影响

| 模块 | 影响 |
|---|---|
| `agent/src/agent/conversation.py` | **不动**（[requirement §1.3 协同契约](./requirement.md)） |
| `Memory.observe` / `Memory.retrieve` 签名 | **不变**（[requirement §3 OOS](./requirement.md)） |
| `ConversationFragment` 形状 | **不变** |
| `MemoryContext` / `MemoryItem` 对外 contract | **不变** |
| `MemoryItem.layer` 枚举 | **不变**（仍是 episodic / semantic / pinned） |
| `EpisodicRow` / `SemanticRow` 表结构 | **不变**（episodic 单段→多行，仍按行写入） |
| evaluator 011 / 012 编排 | **不动**（runner 加一个 kw-only 参数） |

### 8.2 风险与回退

| 风险 | 概率 | 影响 | 回退策略 |
|---|---|---|---|
| prompt 改造让 LLM 输出失败率显著上升 | 中 | 抽取产物变少 → AC-2 净退化 | `_parse_output` 做严格的"至少有一条 entry"校验，失败时降级到旧 prompt 重试一次 |
| 颗粒度回退被触发，cost 翻倍 | 中 | 评测耗时显著增加 | 接受 cost 增加（cost 不是 hard ceiling）；落地后回填 design.md |
| pinned gate 阈值过严，"我叫什么"挂掉 | 低 | R-4.2.3 失守 | smoke test 直接挡住合并；切动态阈值 |
| FTS5 加 bigram 后召回噪声变多 | 低 | recalled_count 抬升但分数没涨 | bigram 仅作 trigram 凑不出时的兜底，不并行召回，噪声可控 |
| evaluator 跑切片 baseline 时间过长 | 中 | 4 份全量 baseline 估计 8–12 小时 | 接受；跑前先用 3 samples × 10 questions smoke 各切片确认不挂 |

### 8.3 跨平台影响

无。改动均在 Python 后端，不涉及 Windows / macOS / 客户端。

---

## 9. 文件改动清单（汇总）

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `memory/src/memory/extraction/prompts/extract.md` | 重写 | 加保留词约束 + few-shot 反例 |
| `memory/src/memory/extraction/prompts/extract_legacy.md` | 新增 | 保留旧 prompt 供切片 `extraction=OFF` |
| `memory/src/memory/extraction/result.py` | 修改 | `episodic_summary: str` → `episodic_entries: list[str]` |
| `memory/src/memory/extraction/extractor.py` | 修改 | `_parse_output` 兼容新旧字段名 |
| `memory/src/memory/extraction/reconciler.py` | 修改 | `apply` 循环写多条 episodic |
| `memory/src/memory/store/sqlite_store.py` | 修改 | `_fts_query` 提上限 + bigram 兜底 + 新增 `fts_match_pinned` |
| `memory/src/memory/retrieval/pinned_gate.py` | 新增 | pinned relevance gate 逻辑 |
| `memory/src/memory/facade.py` | 修改 | `retrieve` 接 gate；构造期接受三开关 |
| `memory/src/memory/factory.py` | 修改 | `build_memory` 加 3 个 kw-only 参数 |
| `memory/tests/...` | 新增 | 三块对应单测 |
| `memory_eval/src/memory_eval/harness/runner.py` | 修改 | 加 `memory_config` 透传 |
| `memory_eval/src/memory_eval/__main__.py` | 修改 | CLI 加 `--no-*` flag |
| `docs/issues/003-memory-eval-baseline-2026-06-12/README.md` | 修改 | 补 footnote 修正"pinned 挤占" |

---

## 10. 开放问题在 design 中的回填

承接 [`requirement.md` §7](./requirement.md) 的 7 个开放问题：

| # | 问题 | 在本 design 的答复 |
|---|---|---|
| Q-1 | 抽取颗粒度 | **首选 prompt 改造、不动颗粒度**；颗粒度变细作为回退方案，触发条件见 §3.4 |
| Q-2 | prompt 改写形态 | 见 §3.2：保留三条原则 + 新增四条硬约束 + few-shot 反例 |
| Q-3 | pinned 注入策略 | **加 query relevance gate，复用 FTS5 trigram 做相关性判定**，零依赖（§5.2） |
| Q-4 | 加宽召回的技术路径 | **仅做 FTS5 zero-deps 改进**（trigram 上限 + bigram 兜底）；不引入向量召回（§4） |
| Q-5 | 切片 baseline 实现方式 | **三开关 config + runner 透传**（§6） |
| Q-6 | 评测耗时 / 成本 | **不预设 cost ceiling**，改为观察项跟随 baseline 落盘（§3.5） |
| Q-7 | schema 兼容性 | **内部 schema 可调（episodic_summary → episodic_entries）、外部 contract 不变**（§3.3 / §8.1） |

实施期需要 commit 的具体值（标 ⏳）：

- ⏳ pinned gate 阈值档位（§5.3，跑 smoke test 后定）
- ⏳ FTS5 `_MAX_TRIGRAMS` 值（§4.1，建议 256，实际跑评测看是否需要再调）
- ✅ **prompt 迭代轮次**（§3.4）：**1 轮一次过**——2026-06-12 用 DeepSeek 对张小红 dialogue block `4_0_1#1` 跑新 prompt，14 个 anchor 字面词命中 12 个（85.7%），远超 7/14 hard floor。无需触发颗粒度回退。
- ⏳ pass-1 收尾报告位置（§7.4）

---

## 11. 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|------|---------|----------------|

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-12
- **确认时间**：2026-06-12
- **依赖**：[`requirement.md`](./requirement.md)（已 Confirmed）
- **下一步**：撰写 `progress.md` 并进 Phase 3（实现，需用户单独授权）
- **协同契约**：与 `docs/explorations/engine-completeness/` 那条线零冲突，详见 [`requirement.md` §1.3](./requirement.md)
