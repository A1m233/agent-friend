# 030 · Agent 行为契约评测正式化

## 状态

CONFIRMED

## 背景

`experiments/agent-contract-eval-spike/` 已验证「handcraft 行为契约 + fixture 固化 case + deterministic judge」这条路线可行。spike 不只跑通了 C1/C2/C3/C4 四类契约，还在真实 agent 链路中抓到了 issue 25 对应的空召回后编造问题，并把经验回流到：

- `docs/lessons/001-agent-conversation-history-evidence-boundary/README.md`
- `docs/explorations/agent-evaluation/README.md`
- `experiments/agent-contract-eval-spike/SPIKE-NOTES.md`

当前问题是：这套能力仍停留在 `experiments/` 下，定位是 spike，不具备正式项目工具应有的包结构、脚本入口、报告归档、baseline 对比与单测保障。后续每次改 agent prompt、工具描述、会话历史召回、web search 触发策略或编造防御时，都缺少一个像 `memory_eval` 那样可复用、可留证据的评测工具。

本需求把 spike 中已经成型的行为契约评测升级为正式项目工具。目标不是继续探索方法本身，而是把它工程化、文档化、可测试化，并让重要评测结果能够随项目历史留档。

## 目标

建立正式 `agent_eval/` 工具包，用于评测 agent 编排层的行为契约，包括工具触发、跨会话 grounding、身份/事实编造抵抗等已验证场景。

可衡量结果：

- `agent_eval/` 成为正式 workspace 包，包名 `agent-eval`，Python module `agent_eval`。
- spike 中已成型的 schema / loader / runner / judge / report 能力迁入正式包，并脱离 `experiments/` 路径运行。
- C1/C2/C3/C4 现有 83 条 fixture case 迁入正式 case 集，loader 能稳定解析。
- 工具能生成正式 JSON 报告 / baseline，记录足够的运行条件和逐 case 结果，重要评测结果可入 git 归档。
- `scripts/agent-eval/run.sh` 与 `run.ps1` 提供双端入口，开发者不需要记忆 `uv run python -m ...`。
- 真实 LLM eval 保持手动运行，不接入 `./scripts/check/run.sh`；但 loader、judge、report/baseline schema、CLI 参数选择等非 LLM 部分进入常规单测。
- 正式工具 README、`scripts/README.md`、agent evaluation exploration 文档都说明 spike 与正式工具的边界和当前状态。

## 范围

### 包含

**正式包与 workspace 接入**

- 新增顶层 `agent_eval/`，作为与 `memory_eval/` 同级的正式评测工具包。
- 在 workspace 配置中接入 `agent-eval`，让项目环境能 editable install 并运行 `python -m agent_eval`。
- 包职责定位为 `agent` 编排层的外部观察者：消费 agent 公开运行入口、工具 trace 与 session store，不反向污染 agent 核心包。

**case 与契约迁移**

- 将 spike 下 `cases/` 迁到正式包内（暂定 `agent_eval/cases/`，最终路径由 design 固化）。
- 保留 C1/C2/C3/C4 现有 case 语义与 id，迁移后总数仍为 83 条。
- loader 对 fixture session、case-level judge、must call、must mention、must not mention 等现有字段保持兼容。
- case 集信息需要能被报告 / baseline 记录，包括 contract 选择、case 选择、case 数量，以及能识别 case 集版本或内容变化的摘要信息。

**runner 与 judge 正式化**

- 迁入 spike 已验证的 runner：每个 case 隔离 session store，可注入 fixture session，可收集 tool call trace、final text、stop reason 与 runtime error。
- 迁入 deterministic judge：覆盖工具必须调用、身份保持、fixture-grounded anchor、fabrication forbidden terms 等现有判分方式。
- judge 失败结果必须能指导排查：至少包含 case id、contract id、pass/fail、失败原因、相关 tool calls 与 final text 摘要。
- runner / judge 的扩展边界在 design 中说明，便于未来接入多轮 case、Tavily 快照 mock、C5 工具召回鲁棒性等新契约。

**报告与 baseline**

- 生成机器可读 JSON 结果，包含：
  - 运行条件：起止时间 / 时长、git commit、working tree dirty 状态、model、provider defaults、运行参数、case 集信息。
  - 汇总维度：总 case 数、按 contract 的 pass/fail/pass rate、runtime error 数。
  - 单 case 维度：query、facet、source、判分结果、失败原因、tool call trace、final text、stop reason、error。
- 建立 `agent_eval/baselines/` 或等价目录，说明哪些评测结果适合入 git 归档、如何命名、如何解读 dirty baseline、如何和历史结果对比。
- 报告 / baseline 机制参考 `memory_eval/baselines`，但不要求复刻其 memory 特有字段。
- spike 的既有 `out/*.json` 作为历史实验产物保留，不直接混入正式 baseline。

**CLI 与脚本**

- `python -m agent_eval` 提供正式 CLI，支持至少：
  - 查看帮助。
  - 选择全部契约或指定 contract。
  - 选择单个 case。
  - 限制每个 contract 的 case 数，便于小规模验证。
  - 覆盖 model。
  - 为本次 baseline 写入 note 或等价人工注解。
- 新增 `scripts/agent-eval/run.sh` 与 `run.ps1`，语义一致、参数透传、文案明确提示会触发真实 LLM 调用。
- `scripts/README.md` 登记该脚本，并明确它不属于默认 `check` 门禁。

**测试**

- 补 pytest 单测覆盖非 LLM 部分：
  - loader 能解析正式 case 目录，数量与关键字段正确。
  - judge 覆盖 pass/fail、anchor miss、forbidden hit、runtime error 等分支。
  - report/baseline JSON schema 的关键字段稳定。
  - CLI 参数选择逻辑正确，尤其是 `--contract`、`--case`、limit 组合行为。
- 单测不触发真实 LLM API，不要求 `.env` 存在。
- 非 LLM 单测进入 `./scripts/check/run.sh` 的常规 pytest 范围。

**文档**

- 新增 `agent_eval/README.md`，说明工具定位、与 `memory_eval` 的关系、如何运行、何时需要用户授权、如何阅读 baseline。
- 更新 `scripts/README.md`。
- 更新 `docs/explorations/agent-evaluation/README.md`，把 `experiments/agent-contract-eval-spike/` 标为历史 spike / archive 来源，把正式工具指向 `agent_eval/`。
- spike 目录先保留为 archive，不在本需求 Phase 1 直接删除；是否移除 workspace 中的 spike 包、是否瘦身 `out/`，由 design 阶段评估并说明。

### 不包含

- **不在本期修 agent 行为本身**：本需求只把评测工具正式化，不修 C4 失败、召回 query 设计、prompt 留白等被评测发现的问题。
- **不新增真实 LLM 门禁**：`./scripts/check/run.sh` 不跑真实 agent eval，不产生 token 消耗。
- **不绕过授权跑真实 eval**：任何实际触发 DeepSeek / Tavily / 其他 LLM provider 的评测运行，仍需按 `llm-api-confirm` 获得用户明确授权。
- **不把 C5 / 多轮 schema / Tavily mock 作为首版必交付**：可以在 design 中预留扩展点，但首版验收聚焦正式化现有 C1-C4。
- **不做 agent-eval skill**：spike notes 中提到的显式 `/agent-eval` skill 不进入本期首版范围；等正式 CLI 和 baseline 稳定后单独评估。
- **不触碰 feature 29 相关区域**：不修改前端设置、Tauri 设置中心、showcase snapshot 相关实现，除非 design 阶段发现必要且重新确认。
- **不删除 spike 历史**：`experiments/agent-contract-eval-spike/` 作为方法验证证据先保留。

## 关键信息

- spike 目录：`experiments/agent-contract-eval-spike/`
- spike 方法总结：`experiments/agent-contract-eval-spike/SPIKE-NOTES.md`
- 当前正式化输入 case：C1 23 条、C2 20 条、C3 20 条、C4 20 条，共 83 条
- 相关探索：`docs/explorations/agent-evaluation/README.md`
- 相关经验：`docs/lessons/001-agent-conversation-history-evidence-boundary/README.md`
- 对照工具：`memory_eval/` 与 `memory_eval/baselines/`
- 相关 issue 修复基线：
  - `08410a1 fix(025): 修复会话历史空召回后编造`
  - `66e8635 docs(issues): 回填 issue 025 修复指针`
- 本需求分支：`feature/030-agent-eval-formalization`

## 验收标准

- [ ] `agent_eval/` 作为正式 workspace 包存在，`python -m agent_eval --help` 能在项目环境中运行并展示 CLI 帮助。
- [ ] C1/C2/C3/C4 现有 83 条 case 已迁入正式包，loader 单测证明 contract 数量、case 数量、fixture session 与 judge 字段解析正确。
- [ ] 正式 runner 能在手动授权后跑指定 contract / case，并生成包含 tool call trace、final text、stop reason、verdict 的结果文件。
- [ ] deterministic judge 单测覆盖工具调用、identity、anchor、forbidden terms、runtime error 等关键分支。
- [ ] JSON report / baseline 包含运行条件、case 集信息、汇总结果和逐 case 结果；schema 关键字段有单测锁定。
- [ ] baseline 目录有 README，说明命名、字段、dirty 状态、provider defaults、case 集信息与入 git 归档策略。
- [ ] CLI 支持帮助、指定 contract、指定 case、限制 case 数、覆盖 model、写入 note；参数选择逻辑有单测覆盖。
- [ ] 新增 `scripts/agent-eval/run.sh` 与 `run.ps1`，双端语义一致、参数透传，并在 `scripts/README.md` 登记。
- [ ] `./scripts/check/run.sh` 包含 agent_eval 的非 LLM 单测；默认 check 不触发真实 LLM / Tavily API。
- [ ] `agent_eval/README.md` 说明工具定位、授权要求、运行方式、报告与 baseline 使用方式。
- [ ] `docs/explorations/agent-evaluation/README.md` 更新为“spike 已归档，正式工具在 agent_eval/”的状态描述。
- [ ] spike 目录是否继续保留、是否从 workspace 成员中移除、历史 `out/` 如何处理，在 `design.md` 中有明确方案。

## 开放问题 / 未来演进

- **C5 工具召回鲁棒性契约**：基于 spike L6，后续可专门测多词 query 与 `recall_past_chats` substring filter 的错位。
- **N=3 抖动测试**：同一 case 多次运行统计 verdict 稳定性，适合作为后续 baseline 增强。
- **多轮 case schema**：覆盖用户 push-back 后 agent 是否补调工具等单轮 case 测不到的失败模式。
- **Tavily / web_search 快照 mock**：让时效信息契约更可控地测“是否调用 / 如何使用结果”，避免真实搜索结果漂移影响判分。
- **agent-eval skill**：在 CLI 和 baseline 稳定后，再考虑提供显式 skill，把最新失败 case 摘要带回当前对话。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-25 | 创建需求文档（CONFIRMED） | - |
