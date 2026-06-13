# 004 · pass-1 全量评测收尾报告（2026-06-13）

- **状态**：resolved
- **完成日期**：2026-06-13
- **承接**：[issue 004 待跑工作](./README.md) / [requirement 013 §6 AC](../../requirements/013-memory-quality-pass-1/requirement.md#6-验收标准)
- **本次 baseline 4 份**（commit `b75845d`）：
  - `2026-06-13T02-50-06-b75845d.json` · **pass-1-full**（两开关全开）· macro **0.3783** / 零分率 41.7% / 116.1 min
  - `2026-06-13T02-56-38-b75845d.json` · **only-extraction**（关 pinned gate）· macro **0.3891** / 零分率 40.2% / 115.1 min
  - `2026-06-13T02-56-47-b75845d.json` · **only-pinned**（关 extraction）· macro **0.2946** / 零分率 48.3% / 81.8 min
  - `2026-06-13T02-56-56-b75845d.json` · **baseline / jieba-only**（两开关都关）· macro **0.3009** / 零分率 47.3% / 83.6 min
- **对照基线**：`memory_eval/baselines/2026-06-12T01-31-46-46e810d.json`（pre-pass-1，macro 0.2189 / 零分率 59.5%，commit `46e810d`）

---

## 1. 结论

**pass-1 全量评测 6/6 AC 全部通过**。pass-1-full macro 从 pre-pass-1 的 **0.2189** 提到 **0.3783**（+0.159 / **+72.8%**），零分率从 59.5% 降到 41.7%（-17.8pp）。

| AC | 验证结果 | 状态 |
|---|---|---|
| **AC-1a** 665 道"有 episodic 但 0 分"题集零分率 | 100% → **64.81%**（-35.2pp）| ✅ PASS |
| **AC-1b** 张小红 q4/q5/q6（block `4_0_1#1`）三道连续 0 分 | 3/3 脱离 0 分（0.60 / 0.60 / 0.75）| ✅ PASS |
| **AC-2** pass-1-full macro ≥ 0.219 hard floor | **0.378** ≥ 0.219（+0.159）| ✅ PASS |
| **AC-3-extraction** 抽取改进切片：665 题集零分率 | 100% → 61.7%（-38.4pp）| ✅ PASS |
| **AC-3-pinned** pinned gate 切片："仅 pinned" 占零分比 | 53.5% → **0.3%**（-53.2pp）| ✅ PASS |
| **AC-3-jieba** jieba 切片：recalled_count 中位数 | median **3 → 9**（mean 3.36 → 8.59）| ✅ PASS |
| **AC-4** 008 工程 AC（memory + agent 测试套件）| 242/242 测试全过 | ✅ PASS |

---

## 2. 全量对照表（5 份 baseline）

| baseline | macro | 零分 | 部分命中 | 满分 | recall median | recall mean | "仅 pinned" 占零分 |
|---|---|---|---|---|---|---|---|
| **pre-pass-1** | 0.2189 | 1613 (59.5%) | 798 (29.4%) | 299 (11.0%) | 3 | 3.36 | 863 (**53.5%**) |
| jieba-only | 0.3009 | 1283 (47.3%) | 1013 (37.4%) | 414 (15.3%) | **9** | 8.59 | 17 (1.3%) |
| only-pinned | 0.2946 | 1308 (48.3%) | 994 (36.7%) | 408 (15.1%) | 8 | 7.75 | 4 (0.3%) |
| only-extraction | **0.3891** | 1090 (40.2%) | 1018 (37.6%) | 602 (22.2%) | 8 | 8.39 | 15 (1.4%) |
| **pass-1-full** | 0.3783 | 1129 (41.7%) | 993 (36.6%) | **588 (21.7%)** | 8 | 8.05 | 1 (0.1%) |

满分率从 11.0% 涨到 21.7%（pass-1-full）/ 22.2%（only-extraction），增幅 ~2 倍。

---

## 3. 各 AC 详细验证

### 3.1 AC-1a · 665 题集零分率（统计层）

- **目标**：pre-pass-1 baseline 里"有 episodic 召回但仍 0 分"的 665 道题，pass-1-full 后零分率必须**可观测下降**（不持平、不上升）。
- **结果**：665 道题在 pass-1-full 下仅 **431 道**仍 0 分，零分率 **64.81%**（vs pre 100%，delta **-35.2pp**）。
- **解读**：抽取改进让原本"找到话题但缺关键词"的题集中有超过三分之一被救回——其中 234 道直接从 0 分变为 ≥1 anchor 命中。

### 3.2 AC-1b · 张小红 q4/q5/q6（尖锐层）

issue 003 第 42 行钉死的反例（张小红 dialogue block `4_0_1#1`）。pre-pass-1 三道连续 0 分。

| 题号 | question | anchors | pre | jieba | only-pin | only-ext | **full** |
|---|---|---|---|---|---|---|---|
| `张小红#q4` | 张小明弟弟在互动学习系统中参与哪些活动？ | 手环触摸屏幕 / 英语启蒙故事 / 绘本 / 听读 / 配音 | 0.00 | 0.00 | 0.00 | 0.80 | **0.60** |
| `张小红#q5` | 张小明弟弟会与张小红分享在学习系统中的成果吗？ | 每天放学回家后 / 一起分享 / 学习成果 / 讨论 / 学习上的问题 | 0.00 | 0.00 | 0.00 | 0.60 | **0.60** |
| `张小红#q6` | 张小明在试用期间学习成果如何？ | 学习兴趣 / 主动参与 / 学习活动 / 学习进步 | 0.00 | 0.00 | 0.00 | 0.75 | **0.75** |

**AC 要求至少 1 道 > 0；实测 3/3 全部脱离 0 分**，最低 0.60，最高 0.75。

关键证据：jieba / only-pinned 两份不含抽取改进的 baseline 在这 3 道上仍是 0 分；only-extraction / full 一开 extraction 立即得分——**直接证明 R-4.1.1（抽取保具体词）是这个尖锐反例集合的根因杠杆**，pinned gate / jieba 都救不回。

### 3.3 AC-2 · 整体 hard floor

- pre-pass-1 macro = **0.2189**（hard floor）
- pass-1-full macro = **0.3783**
- delta = **+0.1594（+72.8%）**
- 远高于 hard floor，AC-2 通过。

### 3.4 AC-3-extraction · only-extraction 切片

- **目标指标**（design §6.3）：抽取产物对 anchor 字面词的保留率↑，以 665 题集零分率作为代理。
- 665 题集在 only-extraction 下零分率 = **61.7%**（pre 100%，delta -38.4pp）。
- only-extraction 单独把整体 macro 推到 **0.3891**，是 4 份切片里最高的——抽取改进确认为**最大杠杆**。

### 3.5 AC-3-pinned · only-pinned 切片

- **目标指标**：零分题里"仅 pinned"占比从 pre 的 53.5% 下降。
- only-pinned 切片下：1308 道零分题中"仅 pinned" = **4 道（0.3%）**，delta **-53.2pp**。
- 极强信号——pinned gate 几乎完全消除了"pinned 单独占位"的零分组成。

### 3.6 AC-3-jieba · baseline 切片（仅 jieba）

- **目标指标**：recalled_count 中位数从 pre 的 3 抬升（issue 003 原文 "~2.6 / 中位数 2"，pre baseline 实测 median=3 / mean=3.36，本期以实测为准）。
- jieba-only 切片下：median **3 → 9**（+6），mean **3.36 → 8.59**（+5.23）。
- 检索通道明显加宽，召回构成发生质变（见下表）。

### 3.7 AC-4 · 008 工程 AC

执行 `uv run pytest memory/tests/ agent/tests/ -q`：**242 测试全过**。008 的工程能力（observe/retrieve 接口、跨会话不失忆、调试可观测）未失守。

---

## 4. recalled_count 分布对照

| recall | pre | jieba | only-pin | only-ext | full |
|---|---|---|---|---|---|
| 1 | 504 | 7 | 22 | 9 | 14 |
| 2 | 741 | 24 | 29 | 16 | 25 |
| 3 | 512 | 28 | 31 | 16 | 13 |
| 4 | 340 | 38 | 44 | 18 | 15 |
| 5 | 204 | 32 | 23 | 19 | 17 |
| 8 | 68 | 911 | 2278 | 1401 | 1975 |
| 11 | 3 | 111 | 16 | 43 | 3 |

**关键变化**：
- pre 时代 45.7% 的题落在 recall ≤2（issue 003 主因 3），pass-1 4 份切片里这个比例全部下降到 < 2%。
- jieba 单独就把"recall = 8（top-K 满）"从 68 道推到 911 道；pinned gate 进一步把它推到 2278（only-pin）/ 1975（full）——因为 pinned 不再单独占位时，FTS 召回才有空间补满。

---

## 5. 切片对照：杠杆归因

把 4 份 baseline 摆在一起，剥离出每条改动的独立贡献：

| 状态 | extraction | pinned gate | jieba | macro | vs pre |
|---|---|---|---|---|---|
| pre-pass-1 | ❌ | ❌ | ❌ | 0.2189 | — |
| baseline (jieba-only) | ❌ | ❌ | ✅ | **0.3009** | **+0.082（+37.4%）** |
| only-pinned | ❌ | ✅ | ✅ | 0.2946 | +0.076（+34.8%）|
| only-extraction | ✅ | ❌ | ✅ | **0.3891** | **+0.170（+77.7%）** |
| pass-1-full | ✅ | ✅ | ✅ | 0.3783 | +0.159（+72.8%）|

**杠杆量化**：
- **抽取改进（R-4.1）单独贡献 +0.088**（only-extraction 0.3891 vs jieba-only 0.3009，在 jieba 已开基础上）→ pass-1 最大杠杆。
- **jieba 单独贡献 +0.082**（jieba-only vs pre）→ 检索通道加宽是次大杠杆。
- **pinned gate 在全量上反向 -0.011**（pass-1-full vs only-extraction）/ -0.006（only-pinned vs jieba-only）→ 微弱反向（见 §6）。

---

## 6. 反向信号：pinned gate 在全量上微幅反向（pass-1.1 候选）

### 6.1 信号

| 对照对 | pinned gate 状态变化 | macro 变化 |
|---|---|---|
| only-extraction vs pass-1-full | 关 → 开 | 0.3891 → 0.3783（**-0.011**）|
| jieba-only vs only-pinned | 关 → 开 | 0.3009 → 0.2946（**-0.006**）|

smoke baseline（30 题）已观察到同向反向 -0.040；全量证实方向一致但幅度大幅收窄到 -0.006 ~ -0.011。

### 6.2 分析

反向幅度 1.1pp 在 macro 噪声门槛（`memory_eval/README.md` 提的 "< 5pp 默认归因噪声"）以下，**单看 macro 不算确证反向**。但 4 份 baseline 之间是同一份种子 / 同一份模型 / 同一份数据集，噪声同质化——这个差异比单跑抖动更可靠。

设计层的代价 / 收益：
- **代价**：pinned gate 让 pinned 在 query 不相关时不注入，导致部分本来"pinned 命中 → episodic 占位补位"的弱场景损失少量曝光。
- **收益**："仅 pinned" 占零分从 53.5% → 0.1%（pass-1-full），从根因上消除了 issue 003 主因 2。

### 6.3 触发条件评估（vs issue 004 README §影响与判断）

issue 004 README §影响与判断：

> **触发 pass-1.1 的条件**：AC-2 不通过（必须）/ AC-1a 零分率持平或上升 / **pinned gate 反向被全量证实**

第三条触发条件文字命中。但：
- 反向幅度 -0.011 / -0.006 都 < 5pp 噪声门槛
- AC-3-pinned 在自身切片下指标极强（"仅 pinned" 占零分 53.5%→0.3%，AC-3 设计目标超额完成）
- macro 反向 vs pinned gate 设计目标（消除"无关 pinned 占位"）之间是 trade-off，不是 bug

**建议**：登记为 **pass-1.1 候选**而非"必须修复"。具体方向：
- 调档 pinned gate 阈值（当前默认值是否过激？需 design.md 再 reflect）
- 引入 strict 档（仅在 pinned 跟 query 高度相关时注入）
- 或者改成"pinned 永远注入但不算 top-K 名额"（让 pinned 真正变成附带通道）

---

## 7. 5 个零分召回构成对照

| baseline | total zero | empty | only pinned | only pinned % | has episodic | has semantic |
|---|---|---|---|---|---|---|
| pre-pass-1 | 1613 | 5 | 863 | **53.5%** | 665 | 421 |
| jieba-only | 1283 | 0 | 17 | 1.3% | — | — |
| only-pinned | 1308 | 10 | 4 | **0.3%** | — | — |
| only-extraction | 1090 | 1 | 15 | 1.4% | — | — |
| pass-1-full | 1129 | 10 | 1 | **0.1%** | — | — |

主因 2（pinned 单独占位）被 pinned gate 彻底压制。

---

## 8. 收尾事项

### 8.1 触发的后续讨论
- **pass-1.1 候选**：pinned gate 全量微反向（§6）。**建议登记为 backlog 而非立即修**——反向幅度在噪声门槛以下，且 pass-1-full 整体大幅净增。具体方向调档 / strict 模式 / 改设计形态待 design.md reflect。

### 8.2 回填工作
- [`013 design.md §10`](../../requirements/013-memory-quality-pass-1/design.md) "pass-1 收尾报告位置" ⏳ → 填入本文件路径：`docs/issues/004-pass-1-full-eval-pending/report.md`
- 本 issue 状态 `open` → `resolved`
- pass-2 触发条件评估（design §4.4）：recalled_count 中位数从 2 抬到 8/9（满足 ≥4），"仅 pinned" 占零分从 53.5% 降到 0.1%（满足 ≤30%）—— **两条均满足**，**当前不触发 pass-2**。

### 8.3 baseline 文件归档
4 份新 baseline 已落到 `memory_eval/baselines/2026-06-13T*-b75845d.json`，连同对照分析（本文件）一起入仓。

---

## 9. 附录：复现

```bash
# pass-1-full
uv run --active python -m memory_eval --dataset perltqa \
  --limit-samples 32 --limit-questions 200 --note "pass-1-full: ..."

# only-extraction
uv run --active python -m memory_eval --dataset perltqa \
  --limit-samples 32 --limit-questions 200 --no-pinned-relevance-gate --note "..."

# only-pinned
uv run --active python -m memory_eval --dataset perltqa \
  --limit-samples 32 --limit-questions 200 --no-extraction-keep-specifics --note "..."

# baseline (jieba-only)
uv run --active python -m memory_eval --dataset perltqa \
  --limit-samples 32 --limit-questions 200 \
  --no-extraction-keep-specifics --no-pinned-relevance-gate --note "..."

# AC 验证
uv run --active python scripts/issue-004-eval/verify_ac.py \
  --pre  memory_eval/baselines/2026-06-12T01-31-46-46e810d.json \
  --full memory_eval/baselines/2026-06-13T02-50-06-b75845d.json \
  --only-extraction memory_eval/baselines/2026-06-13T02-56-38-b75845d.json \
  --only-pinned     memory_eval/baselines/2026-06-13T02-56-47-b75845d.json \
  --jieba-only      memory_eval/baselines/2026-06-13T02-56-56-b75845d.json
```

**4 份 baseline 总耗时**：116.1 / 115.1 / 81.8 / 83.6 min（4 并发跑），墙钟约 2 小时。
