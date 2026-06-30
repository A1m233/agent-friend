# 039 · 记忆矛盾回应边界

## 状态

CONFIRMED

## 背景

`docs/explorations/system-prompt-lessons-from-fable-5/README.md` 已经把 Fable 5 system prompt 样本里值得借鉴的部分拆成若干可落地方向。其中最适合本期闭环的是 P0：当 Echo 被用户指出前后说法矛盾，尤其是“你刚才不是不记得吗 / 你怎么现在又认出我了”这类记忆矛盾追问时，不能暴露底层记忆、存储、数据库、日志、小本本、缓存等机制，也不能为了维护上一轮失误编出一套“为什么刚才不记得、现在又记得”的故事。

这个问题已有真实生产样本记录在 `docs/issues/018-agent-exposes-storage-metaphor-when-confronted/README.md`。当时 Echo 先因召回缺口说“没存下之前的聊天记录”，随后 hot-fix 后又认出用户是 Alex；用户质疑前后不一致时，Echo 又用“没存下来 / 没小本本记着”解释自己。这种话术虽然比“作为 AI 我没有记忆”更拟人化，但本质上仍暴露了存储机制，破坏“像真人不像工具”的体验底线。

现有 prompt 已经覆盖事实编造、web search、历史召回等边界；`agent_eval` 也已有 C1-C4 行为契约。但 issue 018 是另一种失败模式：不是“该不该召回”本身，也不是“空召回后是否编造事实”，而是“被揭穿前后矛盾后如何修正”。本需求把这个失败模式从 exploration / issue 记录落成可维护的 prompt 边界与可复跑的评测。

实现过程中，真实 replay 又暴露出一个更通用的同源坏样本：模型没有实际调用历史工具，却说自己“翻了一下之前的聊天记录”。所以本需求同步收敛通用工具使用契约：自然说“翻了翻 / 查了下”可以，但必须真的发生过对应证据动作；当前对话里看得到的信息不能被说成历史记录或外部查询。

## 目标

让 Echo 在被用户指出记忆相关前后矛盾时，稳定遵守以下行为：

- 不解释底层记忆、存储、数据库、日志、小本本、缓存、同步等机制。
- 不为了圆上一轮失误而编造“细节没存 / 名字存了”之类机制故事。
- 回到当前能确认的事实：能确认什么就自然说什么，不能确认的具体细节不补。
- 可以短促承认上一句不稳或接得不好，但不过度道歉、不自我贬低。
- 保持朋友式自然表达，而不是暴露“系统规则 / 工具 / memory 机制”。
- 对工具动作保持真实归因：没调用工具不能说查过 / 翻过，当前对话里看得到的信息不能伪装成历史记录。

本需求的完成形态不是“大 prompt 重构”，而是围绕 issue 018 形成一条小闭环：prompt section 明确边界、评测 fixture 锁住红线、必要文档说明后续观察口径。工具使用契约只处理跨工具红线，单个工具自己的触发、禁用、参数和结果边界仍留在 tool description。

## 范围

### 包含

**记忆矛盾回应 prompt 边界**

- 新增或调整项目级 system prompt section，用中文规则表达“被追问记忆矛盾时”的行为契约。
- 规则覆盖存储机制隐喻、元解释上一轮输出、圆谎、过度道歉 / 自我贬低等边界。
- 该规则对所有 persona 生效，persona 只能改变语气，不能改变这条项目级边界。
- section 内容保持短而明确，避免把 Fable prompt 的长文结构整体搬入 Echo。

**通用工具使用契约**

- 新增 `tool_usage_contract` prompt section，承载跨工具的调用陈述、证据来源、失败表达和外部动作确认边界。
- 将“未实际调用工具却声称查过 / 翻过”建模为通用工具红线，而不是 memory 专属禁词。
- `runtime_context` 收窄为当前时间、近期事实、证据来源与历史归因的运行时边界，不继续承载全部工具细则。
- 当前内置工具的 description 补齐“何时使用 / 何时不要使用 / 参数来源与证据边界 / 失败与结果表达”。
- 沉淀一条 `docs/lessons/` 经验，作为后续新增工具时的 authoring checklist。

**issue 018 验证闭环**

- 基于 issue 018 的真实触发链，补 replay 或等价可复跑验证入口，覆盖“历史里已有失败兜底，随后用户质疑前后矛盾”的场景。
- 验证重点是输出不再出现“没存 / 小本本 / 数据库 / 缓存 / 同步 / 记录没了”等存储机制表达，不再编造机制解释。
- 保留 issue 018 的观察边界：本需求可以完成修复与评测证据，但是否把 issue 018 标为 `resolved` 需看真实使用观察是否已经满足原 issue 关闭条件。

**agent_eval C5 行为契约**

- 在 `agent_eval/cases/` 新增 C5 `memory-inconsistency-response` 或等价契约，专门覆盖记忆矛盾追问场景。
- C5 首版采用 deterministic judge：通过工具调用、必要 anchor、机制暴露 / 无证据查记录声明 / 矛盾圆故事三类 guard 守住红线。
- C5 不引入 LLM judge；语气自然度只做人工 spot check，不作为首版自动判分门槛。
- 更新 `agent_eval` 文档和 CLI 说明中涉及 C1-C4 的枚举，让 C5 成为正式 case 集的一部分。

**测试与验证**

- 补非 LLM 单测，覆盖新增 case 文件被 loader 解析、selection 能选择 C5、judge 对三类语义 guard 命中正确 fail。
- 如实现调整 `SystemPromptComposer` 默认装配，补对应单测锁定默认 section key 与渲染内容。
- 手动真实 LLM eval 仍需按 `llm-api-confirm` 获得用户明确授权后再跑；默认 `./scripts/check/run.sh` 不触发真实 LLM 调用。

**文档更新**

- 在本需求 `design.md` 中说明 prompt section 落位、agent_eval C5 设计、issue 018 关闭边界。
- 必要时更新 issue 018 文档，记录本需求提供了修复与评测入口，但不在未完成真实观察前强行关闭。
- 必要时更新 `docs/explorations/system-prompt-lessons-from-fable-5/README.md` 的落地状态指针，保持 exploration 仍是过程笔记，不变成需求 truth source。

### 不包含

- **不做 Fable prompt 全量迁移**：不复制 Claude / Anthropic / Fable 产品身份、私有语法、citation 规则或长篇英文结构。
- **不做关系依恋与边界 section**：`relationship_attachment_and_boundaries` 另行需求处理，本期只覆盖记忆矛盾与批评回应的窄边界。
- **不做外部动作确认规则**：IM / connector / 日历 / 消费类动作授权规则等到外部动作变多时再做。
- **不做 product self knowledge 能力**：用户问“你能做什么”时的真实能力来源另行设计。
- **不引入 LLM judge**：本期自动评测只做 deterministic judge；不额外引入 judge 模型成本和不稳定性。
- **不承诺立即关闭 issue 018**：除非实现后已经满足 issue 018 原文要求的 replay / 关联样本 / 真实使用观察条件，否则只记录修复指向与观察状态。

## 关键信息

- 主要输入探索：`docs/explorations/system-prompt-lessons-from-fable-5/README.md`
- 主要闭环 issue：`docs/issues/018-agent-exposes-storage-metaphor-when-confronted/README.md`
- 相关历史 issue：
  - `docs/issues/015-agent-fabricates-named-entities/README.md`
  - `docs/issues/017-memory-pinned-gate-misses-identity/README.md`
- 现有 prompt 资源目录：`agent/src/agent/prompt_sections/`
- 现有 system prompt 装配：`agent/src/agent/system_prompt/`
- 现有行为契约评测：`agent_eval/`
- 本需求分支：`feature/039-memory-inconsistency-response`

## 验收标准

- [ ] 默认 system prompt 中包含记忆矛盾回应边界；该边界对所有 persona 生效。
- [ ] 默认 system prompt 中包含通用工具使用边界；没调用工具时不能声称查过 / 翻过。
- [ ] 用户指出“你刚才不是不记得吗 / 你前后说法不一样”时，Echo 不输出“没存 / 没记下 / 小本本 / 数据库 / 缓存 / 同步 / 记录没了”等存储机制表达。
- [ ] Echo 不编造“为什么之前不记得、现在又记得”的机制故事；输出回到当前可确认事实，不能确认的细节不补。
- [ ] Echo 可以简短承认上一句不稳或接得不好，但不长篇求原谅、不自我贬低。
- [ ] issue 018 有 replay 或等价验证入口，能复跑本期关注的失败链路。
- [ ] `agent_eval` 新增 C5 case 集，loader 能解析，CLI 能选择 C5 或 C5 单 case。
- [ ] C5 deterministic judge 能对机制暴露、无证据查记录声明、矛盾圆故事给出 fail，对基于证据自然修正的回复给出 pass。
- [ ] `Tool.description` 的项目约定不再是一句话摘要，而是要求写清使用时机、禁用场景、参数来源、结果边界和失败处理。
- [ ] 至少 `web_search` 与 `recall_past_chats` 的 description 已按该约定补齐。
- [ ] `docs/lessons/` 沉淀工具契约与 tool description 分层经验。
- [ ] 非 LLM 单测覆盖新增 loader / selection / judge / prompt 装配相关分支。
- [ ] `agent_eval/README.md` 或等价文档更新 C5 说明与运行示例。
- [ ] `./scripts/check/run.sh` 通过；默认 check 不触发真实 LLM / Tavily API。
- [ ] 若跑真实 LLM replay / eval，已按 `llm-api-confirm` 获得用户明确授权，并在 `progress.md` 记录模型、范围、结果和是否入库报告。
- [ ] issue 018 文档记录本需求修复指向；若未满足真实观察关闭条件，状态保持 open 并说明剩余观察项。

## 开放问题 / 未来演进

- **关系依恋与边界**：另行需求处理，需覆盖可依靠感、情感依恋、非操控、非排他、用户结束对话时自然收尾等场景。
- **工具 description checklist 后续规则化**：本期先沉淀到 lesson；如果后续新增工具频繁，可再迁入 `.cursor/rules/` 或贡献检查脚本。
- **多轮 C5 schema**：首版 C5 可以用 fixture session + 单轮 query 近似真实链路；若后续发现需要严格多轮交互，再扩展 agent_eval schema。
- **真实使用观察**：issue 018 的最终关闭仍应依赖真实 IM / 桌宠使用样本确认，不能只靠 prompt 文本存在。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-30 | 创建需求文档（CONFIRMED） | - |
| 2026-06-30 | 将真实 replay 暴露的“无证据工具动作声明”纳入本需求，新增通用工具使用契约与 tool description checklist 沉淀 | 扩展 prompt section、工具描述、lesson 与验收标准 |
