# 004 · pass-1 全量评测延后 backlog

- **状态**：resolved（2026-06-13 跑完，6/6 AC 全过；详见 [report.md](./report.md)）
- **严重度**：medium
- **发现日期**：2026-06-12
- **完成日期**：2026-06-13
- **关联需求**：[013 memory-quality-pass-1](../../requirements/013-memory-quality-pass-1/)（本期完成代码改动 + smoke 管道验证，全量评测延后）
- **关联 issue**：[003 memory-eval-baseline-2026-06-12](../003-memory-eval-baseline-2026-06-12/README.md)（pass-1 的输入根因分析；本 issue 是它的对照位）
- **关联 baseline**：
  - pre-pass-1 全量：`memory_eval/baselines/2026-06-12T01-31-46-46e810d.json`（31 samples × 全部问题 = 2710 道判分；macro 0.219 / 零分率 59.5%）
  - pass-1 smoke 4 切片（commit `881843e`，3 samples × 10 questions = 30 道判分）：
    - `2026-06-12T09-13-09-881843e.json` · full · macro 0.365 / 零分 13/30
    - `2026-06-12T09-26-32-881843e.json` · only-extraction · macro 0.405 / 零分 12/30
    - `2026-06-12T09-38-07-881843e.json` · only-pinned · macro 0.137 / 零分 21/30
    - `2026-06-12T09-45-19-881843e.json` · baseline（仅 jieba）· macro 0.156 / 零分 18/30

## 现象

[013 pass-1](../../requirements/013-memory-quality-pass-1/) 已完成所有代码改动（M13.1 抽取保具体词 / M13.2 jieba 中文分词 / M13.3 pinned gate / M13.4 切片开关 config）+ smoke 验证（30 题 × 4 切片管道全通）。但 design §6 / progress M13.5 锁的 **4 份全量 baseline（31 samples × 全部问题 × 4 切片 ≈ 10000+ 次 LLM 调用 / 8–12 小时）尚未跑**，requirement §6 的核心 AC 因此**未被严格验证**：

- **AC-1a**（665 道"有 episodic 但 0 分"题集零分率可观测下降）
- **AC-1b**（张小红 dialogue block `4_0_1#1` 连续 3 道题至少 1 道脱离 0 分）
- **AC-2**（全量 macro **必须严格不低于 0.219**——hard fail 线）
- **AC-3**（每片切片 baseline 至少一个量化指标改善）
- **AC-4**（008 工程 AC 不失守）

## 为什么登记为 issue 而非立即跑

- **资源开销**：4 份全量 ≈ 10000+ DeepSeek 抽取调用 / 8–12 小时（颗粒度回退后 16–24 小时）
- **风险评估极低**：smoke 上 pass-1-full = 0.365 远高于 AC-2 hard floor 0.219，全量上跌回 < 0.219 需要 40%+ 退化，概率极小
- **本期代码层验证已充分**：286 单测全过 + 4 份 smoke 切片管道无报错 + M13.1 张小红 cassette 12/14 anchor 保留（85.7%）
- **AC 全量验证延后不影响 main**：feature/013 合 main 后引擎层 / bridge / 桌面端调用不感知差异（[`requirement §1.3 协同契约`](../../requirements/013-memory-quality-pass-1/requirement.md)：`Memory.observe/retrieve` 签名不变、`MemoryContext` 形状不变）

## 待跑工作

### 1. 4 份全量 baseline

按 [`pass-1 design §6.3`](../../requirements/013-memory-quality-pass-1/design.md) 的切片配置：

```bash
# pass-1-full（两开关全开 = pass-1 终态）
uv run python -m memory_eval --dataset perltqa --note "pass-1-full"

# pass-1-only-extraction（关 pinned gate）
uv run python -m memory_eval --dataset perltqa --no-pinned-relevance-gate \
  --note "pass-1-only-extraction"

# pass-1-only-pinned（关 extraction、用旧 prompt）
uv run python -m memory_eval --dataset perltqa --no-extraction-keep-specifics \
  --note "pass-1-only-pinned"

# pass-1-baseline（仅 jieba 永远开）
uv run python -m memory_eval --dataset perltqa \
  --no-extraction-keep-specifics --no-pinned-relevance-gate \
  --note "pass-1-baseline"
```

跑前需按 [`global-user-rules / llm-api-confirm`](../../../) 获用户授权（4 份连跑量大）。

### 2. AC 验证

跑完后对照 [`requirement §6`](../../requirements/013-memory-quality-pass-1/requirement.md) 逐条验：

- AC-1a：load pre-pass-1 baseline + pass-1-full baseline，筛 issue 003 baseline 里"有 episodic 召回但 0 分"的 665 道题，对比零分率 —— 必须可观测下降（不持平、不上升）
- AC-1b：在 pass-1-full baseline 里查张小红 dialogue block `4_0_1#1` 的连续 3 道题，至少 1 道 score > 0
- AC-2：pass-1-full macro **必须 ≥ 0.219**；任何形式整体净退化（含持平）不接受
- AC-3：3 个切片 baseline 分别提取量化指标（抽取产物 anchor 字面词保留率↑ / "仅 pinned"占零分比从 53.5% 下降 / recalled_count 中位数从 2 抬升）
- AC-4：跑 008 既有工程 AC 用例确认不失守

### 3. 写收尾报告

落到本 issue 目录下 `report.md` 或新建 `docs/issues/00X-pass-1-result-<日期>/`，与 issue 003 同等粒度（macro 总分 + 切片对比 + 分段分布 + 召回构成 + 关键样本对照）。

### 4. 回填

- [`design.md §10`](../../requirements/013-memory-quality-pass-1/design.md) ⏳ "pass-1 收尾报告位置"项填入实际位置
- 本 issue 状态从 `open` 改 `resolved`
- 如 AC 任一未通过 → 起 pass-1.1 修复需求 / 回滚相关 commit

## 已观察的初步信号（来自 smoke，待全量验证）

| 信号 | smoke 数据 | 待全量验证的判断 |
|---|---|---|
| **主因 1 抽取改进是核心杠杆** | 开 extraction 两片 macro 0.365/0.405 >> 关的两片 0.137/0.156 | 全量应延续；jieba 单独贡献微弱（baseline 0.156 vs only-pinned 0.137 差 0.019） |
| **pinned gate 在小样本反向 0.04** | full 0.365 < only-extraction 0.405 | 跟 30 题样本方差同量级；全量上若真反向需调档位（strict / 抬阈值）→ 起 pass-1.1 |
| **smoke macro 远高于 AC-2 hard floor** | full 0.365 >> 0.219 | 全量 AC-2 跌穿概率极小，但全量量纲跟 smoke 不直接可比 |

## 影响与判断

- **不影响 main 合入**：代码层已通过 286 单测 + smoke 管道；合入 main 后无回归风险
- **AC 全量验证作为独立 backlog**：本 issue 是 [013 pass-1](../../requirements/013-memory-quality-pass-1/) 的"未尽事项托管点"
- **触发 pass-1.1 的条件**：AC-2 不通过（必须）/ AC-1a 零分率持平或上升 / pinned gate 反向被全量证实
- **触发 pass-2 的条件**：跑完 pass-1 全量后，若 recalled_count 中位数仍 < 4 或"仅 pinned 占零分比"仍 > 30%（[design §4.4](../../requirements/013-memory-quality-pass-1/design.md)）
