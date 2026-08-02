---
name: add-agent-eval-case-group
description: 把 agent-friend 的真实 bad case、当前会话、截图/日志/trace 或 agent-friend diagnostics 包闭环固化为 agent_eval 行为契约 case group。Use when 用户要求“把这个 bad case 写进/固化进 agent eval”“生成回归 case/group”“从真实会话或诊断包沉淀评测”，或希望用可重复 eval 防止某类 Agent 行为回归；本 Skill 负责复合根因拆分、contract 归属、fixture/YAML/必要的小型 harness 与 deterministic judge 支持、测试，以及按项目 agent_eval 治理完成新增 case 的首次真实验证，但不负责通用运行调度，不修产品行为、不关闭 issue、不自动提交。
---

# 固化 Agent Eval Case Group

## 目标

把一次真实事故转成可维护、可运行、可解释的 `agent_eval` 行为契约。不要只在对话中给出分析；完成后应有仓库内 case、fixture、必要测试和验证证据。

## 先读参考

开始任何分析或写入前，完整读取：

- `references/incident-to-case-group.md`：证据重建、复合根因与 case group 设计。
- `references/agent-eval-authoring.md`：本仓库 `agent_eval` 的现行结构、写入和验证流程。

同时读取当前仓库中的 `agent_eval/README.md`、相关 contract YAML、schema、loader、runner、judge 与测试。以当前代码为准，不依赖 Skill 中可能过时的 contract 数量或字段印象。

## 职责边界

本 Skill 是一次“事故到回归资产”的端到端 owner：

1. 还原 bad case 的权威输入、上下文和错误输出。
2. 调用 `diagnose-bug` 建立证据链并拆分多根因；若输入是 `agent-friend-diagnostics-*.zip`，同时调用 `inspect-agent-friend-diagnostics` 负责包校验、运行时身份、格式与隐私边界。
3. 把每个可独立回归的行为错误映射到现有 contract；只有没有合适行为 owner 时才新增 contract。
4. 写入 `agent_eval` 的 case、fixture、必要的小型 schema/loader/runner/judge 支持和测试。
5. 证明原始错误答案会失败、至少一个合理答案会通过，并执行确定性测试。
6. 获得真实 LLM 调用授权后，按项目 `agent-eval-governance` Rule 运行新增 case/contract
   的首次验证。本 Skill 只确定哪些新增或受影响 case 需要验证及其原因，不负责后续复验、
   重复采样或 resume 调度。

本 Skill 不负责：

- 修改 system prompt、工具描述、provider、会话编排或其他产品行为。
- 把 eval 失败顺手修掉。
- 关闭 issue、自动 commit/push，或实现与当前案例无关的通用 eval 平台。
- 代替重复采样能力；需要 `--repeat N` 等通用能力时引用现有 issue，或另行登记，不在本次 case 固化中夹带实现。

## 工作流

### 1. 冻结事故证据

确定并记录：来源、原始输入、相关会话区间、附件、模型、surface、runtime 时间、工具请求/结果、错误输出及可验证真相。优先读取权威原件，不从转述反推细节。

只抽取因果相关上下文；冷启动或长会话背景如果会改变行为，应保留并单独标注。不得为了适配当前 YAML 而静默删除历史工具事件、附件或先前 assistant 自我设定等关键条件。

附件遵循“保真优先、权限感知”：用户明确授权保存原件时使用原件；否则只持久化完成回归所需的最小内容，不写入凭据、无关私密信息或第三方诊断包中的额外数据。无法安全持久化时暂停并说明需要的授权或替代 fixture。

### 2. 做多标签根因分析

不要给复合 bad case 强行指定单一根因。对每个错误分别列出：

- 观察到的失败行为。
- 直接证据与反证。
- 触发条件、促成因素、系统性弱点、eval 缺口。
- 因果置信度与能区分判断的反事实/隔离实验。
- 候选 contract owner。

同一回答中的视觉越界、来源归因、因果故事、用户意图投射、工具路由或人格自评可以属于不同根因与不同 contract。频率只说明稳定性，不单独证明因果。

### 3. 设计 case group

把“事故”和“契约”分开：事故是 provenance/evidence 单元，contract 是稳定行为单元。一个事故可以通过相同 `source` 向多个 contract 贡献 case。

优先形成小型组合：

- **Anchor replay**：最大程度保持原始因果链，证明真实事故可被复现。
- **Isolation cases**：每条只施加一个关键诱因，定位不同错误机制。
- **Controls**：确认正确识别、合理推断、必要工具调用或自然对话没有被误伤。

不要用一个塞满禁词的脆弱 judge 代替分层 case。只有当某个断言能稳定区分错误与可接受行为时才写进 judge。

### 4. 写入 agent_eval

先复用现有 contract；新增 contract 前检查相邻契约，说明为什么现有 owner 无法承载。按 `references/agent-eval-authoring.md` 更新 YAML、fixture、README 计数和测试。

若当前 harness 无法忠实表达因果相关输入，可实现最小、通用、可测试的支持。扩展必须局限在 `agent_eval`，兼容已有 case，并由当前 anchor replay 直接驱动。若需要大型通用事件回放、模型 judge、生产数据导入器或平台级迁移，先停止写入并向用户说明边界与单独需求。

### 5. 验证判分边界

对每个新增或改变的 judge 维度，至少验证：

- 原始 bad answer 失败，且失败理由命中目标行为。
- 一个内容不同但语义合理的 answer 通过。
- 相邻但不应失败的 control 不被误杀。

若只能靠某个事故专属词串判负，优先重做 case 隔离或 judge 语义；不要把“看起来抓住了这一次”误当成稳定契约。

### 6. 交给项目 Rule 运行与复核

先列出本次新增或改变的 case ID、受影响 contract，以及为什么需要首次真实验证；然后完全
遵守项目 `agent-eval-governance` Rule 执行确定性测试、真实调用、续跑状态管理和报告复核。
不要在本 Skill 内维护第二套运行清单、授权、重复采样或 pass/fail 判读规则。

复核时额外确认本次事故固化特有的内容：输入是否忠实、附件是否加载、历史是否完整，
以及 anchor / isolation / control 是否仍在验证各自的目标行为。

## 完成标准

只有同时满足以下条件才宣告闭环：

- provenance 可追溯，原始证据与派生 fixture 的关系清楚。
- 复合错误已拆分，case 与 contract owner 一一说得通。
- anchor、必要 isolation/control 已写入，或明确说明为何不需要。
- 原始 bad answer fail / 合理 answer pass 已验证。
- `agent_eval` 确定性测试通过；若获授权，真实 eval 结果和 transcript 已复核。
- 没有修改产品行为、关闭 issue 或自动提交。

最终回报新增/修改的 contract 与 case、证据保真策略、验证结果、真实运行配置，以及仍未解决的产品问题和 eval 能力缺口。
