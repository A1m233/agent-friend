# 012 · PerLTQA 召回判分与首份 baseline

> PerLTQA Anchor Recall Scoring & First Baseline
>
> 给 [011 记忆召回质量评测机制](../011-memory-recall-eval/requirement.md) 的判分扩展点接入一个具体实现（基于 PerLTQA 自带的 `Memory Anchors`），让评测能产出一个**可量化、可对比的数**，并落第一份 baseline 文件。

---

## 1. 背景

### 1.1 当前现状

[011](../011-memory-recall-eval/requirement.md) 落地了召回评测机制，§4.3 给判分留了扩展点 + `NoopJudge` 桩。本期之前，PerLTQA 评测跑完只能输出"召回流水"（每题召回了哪些记忆条目），**判分这一步靠肉眼**。

试跑 3 samples × 10 questions 后立刻碰到问题：

- **无法量化对比**：30 题"哪些召回到了关键信息、哪些没有"全靠人脑数；后续改 memory 时无法快速判断"这次改动是好是坏"。
- **无 baseline 可参照**：没有一个具体的数字作为起点，所有迭代都是相对感觉。

### 1.2 数据条件已具备

PerLTQA 数据**每题自带 `Memory Anchors`**（关键 token + 字符 span，见 011 跑通后的真实样本结构），是天然的判分锚点 —— 不需要任何 LLM 调用、不需要训练，纯 Python 集合匹配就能算出 "召回内容覆盖了多少个 anchor token"。

→ 结论：**把 011 留好的 judge 扩展点用上一个具体实现，输出第一份 baseline。**

### 1.3 与已有需求的边界

| 概念 | 定位 | 与 012 的关系 |
|---|---|---|
| Memory（[008](../008-engine-memory/requirement.md)） | 记忆系统本身 | **不改 008 任何代码**；本期是它的下游观察者 |
| 评测机制（[011](../011-memory-recall-eval/requirement.md)） | benchmark 加载 / 灌入 / 召回 / 展示 / 判分扩展点 | **不改 011 已落地的接口与编排**，仅复用其判分扩展点接入一个具体实现，并扩展 report / 新增 baseline 落盘 |

**本需求交付的是判分器与首份 baseline 产物，不是对 memory 召回质量的优化。** memory 测出来分数高低不在本期验收范围内。

---

## 2. 本期范围（In Scope）

本期交付以下内容，**仅限**这 4 项：

- **PerLTQA AnchorRecallJudge**：基于 PerLTQA 数据自带的 `Memory Anchors`，对每题输出 `命中 anchor 数 / 总 anchor 数` 的 [0, 1] 分数；接入 011 §4.3 既有判分接口，作为 PerLTQA 评测的默认 judge。
- **Report 升级**：评测跑完后，控制台输出 **macro 平均分数** + **所有得分为 0 的错题清单**（含问题、anchor 列表、本题召回到的记忆条目）。
- **首份 baseline 落盘**：评测产出结构化结果文件至 `memory_eval/baselines/`（命名形态由 `design.md` 决定），内容至少包含 git commit / 抽取模型 / limit 参数 / 每题分数 / macro 平均，足以复现条件。
- **判分器自检**：3 个人造 sanity case 单测，覆盖"全命中→满分"/"全不命中→0 分"/"部分命中→部分分"三种行为，纳入 `./scripts/check`。

> 本期验收的对象是**判分器与 baseline 产物的可用性**，不是 "memory 在 PerLTQA 上分数高低"。

---

## 3. 非目标（Out of Scope）

以下本次**明确不做**：

- **Reference Memory 层 Recall@k**：PerLTQA 还自带会话级 evidence (`Reference Memory`)，但要用它需要 memory 反查 chunk 的来源 session —— 涉及 memory 代码改动，本次不做。
- **端到端 QA / 生成答案 / 答案级判分**：本期判的是"召回内容是否覆盖 anchor"，不接"用召回生成答案、再判分答案"链路。
- **LoCoMo F1 / ROUGE 套用**：LoCoMo 的官方指标依赖"生成的答案"，本期无此产物，不套；LoCoMo 路径继续走 `NoopJudge` 兜底。
- **CI 集成 / 评测入 `scripts/check`**：评测要真实 LLM 调用，沿用 011 约定保持手动运行。
- **Report 美化**：控制台 / JSON 输出够用即可，不做表格、HTML、Markdown 等可视化升级。
- **多判分维度叠加**：本期只引入 anchor recall **一种**指标，不同时叠加召回排序、token F1、LLM-as-judge、BERTScore 等其他维度。
- **改动 memory 代码**：与 011 一致。
- **改动 011 已落地的接口与编排**：仅在 011 既有扩展点上新增/替换 judge 与扩展 report；不动 loader / adapter / runner 的对外契约。

---

## 4. 核心需求详述

以下需求点（R-级）只讲"做什么 / 做到什么效果"；anchor 匹配的具体形态（是否分词、包含 vs 相等、大小写、空白处理等）由 `design.md` 决定。

### 4.1 判分器

- **R-4.1.1 PerLTQA 锚点判分**：对 PerLTQA 每题，基于其 `Memory Anchors`，输出一个 `[0, 1]` 区间的分数：`命中 anchor 数 / 总 anchor 数`。
- **R-4.1.2 接入既有扩展点**：使用 011 §4.3 既有 judge 接口，不引入新接口形态，不改 runner 编排。
- **R-4.1.3 PerLTQA 默认 judge**：PerLTQA 评测默认使用 AnchorRecallJudge；LoCoMo 等无 anchor 数据集继续走 NoopJudge。
- **R-4.1.4 防御式取数**：anchor 字段缺失 / 格式异常的单题跳过判分（不计入 macro），不致整体评测失败 —— 与 011 R-4.1.3 防御式解析的精神一致。

### 4.2 报告

- **R-4.2.1 Macro 平均**：评测结束后输出所有题分数的算术平均。
- **R-4.2.2 错题清单**：列出所有得分为 0 的题，每条包含问题文本、anchor 列表、本题召回到的记忆条目（便于人复盘"为什么完全没召回"）。
- **R-4.2.3 非 0 分题保留召回展示**：011 既有的"召回流水"展示对非 0 分题不退化（人仍能肉眼看每道题召回了什么）。

### 4.3 Baseline 产物

- **R-4.3.1 结构化落盘**：每次运行产出一份机器可读的 baseline 文件到 `memory_eval/baselines/`，可被后续运行解析对比。
- **R-4.3.2 上下文齐备**：baseline 文件足以让读者复现条件、独立解释数字，至少包含：
  - 运行条件：git commit、working tree 是否 dirty、抽取 provider（model / api_base / defaults 含 temperature 等）、数据集文件 hash、limit 参数、起止时间 / 时长、人写注解 `note`
  - 单题维度：稳定 question_id、question、gold answer、anchors、score、judge detail、**完整召回内容**（每条 layer / text / source_ref / score）
  - 全局维度：macro 平均、已判分 / 未判分 / 0 分题数
- **R-4.3.3 入 git 归档**：baseline 文件随代码进 git，作为可在历史上溯源、跨设备 / 跨开发者一致的对比基线。文件名带 ISO 时间 + commit short-sha 天然不冲突；目录下附 `README.md` 解释字段语义，新人 clone 即可看到全部历史。
- **R-4.3.4 噪声透明**：抽取链路是真实 LLM、有抖动；单次跑分数自带噪声。要求 README 显式说明这一点（"重大判断建议多次平均"），baseline 文件本身也通过 `provider.defaults.temperature` 暴露关键参数。

### 4.4 判分器自检

- **R-4.4.1 Sanity case 集合**：至少 3 个人造测试用例，覆盖：
  - 召回内容完全包含全部 anchor → 满分（1.0）
  - 召回内容完全不含任何 anchor → 0 分
  - 召回内容部分命中 anchor → 严格落在 (0, 1) 区间，与命中数比例一致
- **R-4.4.2 单测形式**：sanity case 以 pytest 单测形式落地，纳入 `./scripts/check`，不依赖真实 LLM。

---

## 5. 使用约束

本期不引入新约束，**继承 011 §5**：

- 数据集许可：PerLTQA 仍按 CC BY-NC 4.0，本机制只用于内部非商用评测，数据不入 git。
- 真实 LLM 调用：跑评测仍触发抽取阶段的真实 LLM 调用，按 [`llm-api-confirm`](../../../.cursor/rules/llm-api-confirm.mdc) 每次运行前需获授权；本期判分器**不引入额外 LLM 调用**。

---

## 6. 验收标准

> 本节 AC 全部是**机制层面的过程性标准**——验证"判分器与 baseline 产物能落地、能跑通"，**不含任何 memory 召回质量阈值 / 分数高低判断**。

- **AC-1 判分器接入**：PerLTQA 评测能调用 AnchorRecallJudge，为每题输出 `[0, 1]` 分数（替换原 NoopJudge）。
- **AC-2 Report 输出**：评测结束控制台输出包含 macro 平均 + 0 分错题清单（问题 / anchor / 召回条目）；非 0 分题的召回展示不退化。
- **AC-3 Baseline 产物**：评测结束在 `memory_eval/baselines/` 下生成结构化 baseline 文件（schema_version=2），含运行条件齐备字段、每题完整召回 + gold answer + 稳定 question_id、macro 汇总；文件随代码进 git 归档，跨设备 / 跨人可见。
- **AC-4 Sanity case 全过**：3 个人造测试用例（全命中 / 全不命中 / 部分命中）单测通过，纳入 `./scripts/check`，不依赖真实 LLM。
- **AC-5 已有机制不退化**：011 落地的 LoCoMo 路径仍能跑通（NoopJudge 保留作兜底），`./scripts/check` 全绿。

---

## 7. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-11 | **R-4.3.3 / AC-3 修订**：baseline 由"不入 git"改为"入 git 归档"。原方案让 baseline 不可对比、与本需求"出一个可对比的数字"自相矛盾；持久化到仓库才能跨设备 / 跨时间真正可对比。 | design.md §2 / §6.1 / §6.3 / §9.3 同步修订；删除 `memory_eval/baselines/.gitignore`；新增 `memory_eval/baselines/README.md`；`memory_eval/README.md` 大改同步判分 / 输出 / schema。 |
| 2026-06-11 | **R-4.3.2 大幅扩展 + 新增 R-4.3.4 + AC-3 修订**：baseline 加入完整召回内容、gold answer、稳定 question_id、working tree dirty、数据集 hash、provider defaults (temperature)、note、起止时间 / 时长。原方案只存 `recalled_count` 让 0 分题"为什么没召回"无法复盘、改 memory 后"哪些召回变了"看不见，违背 baseline "可对比 + 可解释"的根本价值；新增 R-4.3.4 把"LLM 噪声"明确写入需求面，避免读者把单次数字当确定值。schema_version bump 到 2；旧 schema=1 baseline 保留作"减信息版"参照。 | design.md §6.2 schema 全量重写；§6.4 新增噪声策略段；§10 变更记录加一行；progress.md 加 hotfix-2 行；memory_eval/README.md 加噪声策略 + PerLTQA license attribution；baselines/README.md 字段表更新到 v2。 |

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-11
- **确认时间**：2026-06-11
- **承接**：[`011 记忆召回质量评测机制`](../011-memory-recall-eval/requirement.md) §4.3 判分扩展点
- **下一步**：撰写同目录的 `design.md`（技术方案）
