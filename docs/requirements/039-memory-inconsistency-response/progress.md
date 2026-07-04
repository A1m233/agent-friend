# 039 · 记忆矛盾回应边界 - 进度

## 总体状态

COMPLETED

## 任务列表

### M39.1 Prompt section 与默认装配

- [x] T1. 新增 `memory_consistency.md` prompt section
- [x] T2. 接入 `SystemPromptComposer` 默认装配与资源加载
- [x] T3. 更新 prompt section README 与 composer 单测
- [x] T12. 新增 `tool_usage_contract.md`，并重排默认 prompt section 职责

### M39.2 agent_eval C5

- [x] T4. 新增 C5 case fixture
- [x] T5. 更新 agent_eval README / CLI help / loader / selection / baseline 测试
- [x] T6. 补 judge 单测覆盖 C5 语义 guard

### M39.3 issue 018 replay 与文档

- [x] T7. 新增 issue 018 replay 脚本
- [x] T8. 更新 issue 018 与 Fable exploration 的修复指针
- [x] T13. 更新 039 文档，将“无证据工具动作声明”纳入本需求范围

### M39.4 工具描述与经验沉淀

- [x] T14. 将 `Tool.description` 约定从一句话改为详细工具使用说明
- [x] T15. 补齐 `web_search` 与 `recall_past_chats` 的禁用场景、参数来源和结果边界
- [x] T16. 新增 `docs/lessons/002-tool-usage-contract-and-description-boundary/README.md`

### M39.5 验证

- [x] T9. 跑非 LLM 单测
- [x] T10. 跑 `./scripts/check/run.sh`
- [x] T17. 跑新增工具契约后的非 LLM 单测与全量 check
- [x] T11. 经用户授权后跑真实 LLM replay / C5 smoke
- [x] T18. 扩充 C5 到 20 条并完成授权全量复测

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-30 | 创建 `progress.md`，进入 Phase 3 | 已确认 `requirement.md` / `design.md` |
| 2026-06-30 | 完成 M39.1 prompt section 与默认装配 | 先新增 `memory_consistency` 槽位；后续用户 review 后补 `tool_usage_contract`，当前默认顺序为 `project_identity → persona → persona_switch_strategy → tool_usage_contract → memory_consistency → channel → runtime_context` |
| 2026-06-30 | 完成 M39.2 agent_eval C5 | 新增 `c5-memory-inconsistency-response.yaml` 5 条 case；同步 README / CLI help / loader / selection / baseline / judge 单测 |
| 2026-06-30 | 完成 M39.3 issue 018 replay 与文档指针 | 新增 `docs/issues/018-.../replay.py`；issue 018 保持 open 并记录 039 修复指向；Fable exploration 标记 P0 已由 039 承接 |
| 2026-06-30 | 完成非 LLM 验证 | `./scripts/test/run.sh agent agent_eval` 通过（289 passed）；`./scripts/check/run.sh` 通过（ruff + mypy 244 files + pytest 660 passed；frontend lint/test 因本地 node/frontend 依赖未就绪由脚本跳过） |
| 2026-06-30 | 修复当前验证入口的凭证来源 | issue 018 replay 与 `agent_eval` 改为默认使用 `runtime_config` 凭证中心；旧脚本系统性迁移另记 `docs/issues/032-legacy-llm-scripts-bypass-credential-center/README.md` |
| 2026-06-30 | 授权跑真实 LLM 样本并暴露后续边界 | replay 先暴露“没有之前的记忆”断关系话术，后又暴露“未调用 recall 却声称翻了聊天记录”；C5 limit1 smoke 曾通过并生成报告，但报告早于最新边界收窄 |
| 2026-06-30 | 收窄“翻聊天记录”处理口径 | 不再把自然的“翻聊天记录 / 查会话记录”作为普通禁词；prompt 改为禁止无证据声称查过，replay 改为在未调用 `recall_past_chats` 时才判定 unsupported lookup claim |
| 2026-06-30 | 完成最新非 LLM 验证 | `./scripts/test/run.sh agent agent_eval` 通过（289 passed）；`./scripts/check/run.sh` 通过（ruff + format + mypy 244 files + pytest 660 passed；frontend lint/test 因本地工具链/依赖未就绪由脚本跳过）；`git diff --check` 通过 |
| 2026-06-30 | 授权复跑真实 LLM 最小样本 | issue 018 replay 调用了 `recall_past_chats`，但最终输出“根本没存住 / 蒙的 / 蒙对了”，命中 forbidden 失败；C5 limit1 smoke 当时通过，但报告早于后续补丁，未保留为当前证据 |
| 2026-06-30 | 补 replay 暴露的新坏样本 | prompt 明确禁止把矛盾解释成“没存住 / 蒙的 / 蒙对了 / 猜的 / 碰巧叫对”；C5 与 replay forbidden terms 同步补齐；`./scripts/test/run.sh agent agent_eval` 通过（289 passed）；`./scripts/check/run.sh` 通过（ruff + format + mypy 244 files + pytest 660 passed；frontend lint/test 跳过） |
| 2026-06-30 | 授权复跑 replay，发现反驳式脱身样本 | issue 018 replay 未调用 `recall_past_chats`，输出“你说你叫 Alex 那就 Alex 呗 / 小本本”，命中 forbidden 失败；因 replay 已失败，未继续消耗第二次 C5 smoke 调用 |
| 2026-06-30 | 补反驳式脱身边界 | prompt 明确禁止“你刚刚说你叫 X / 没认出来 / 那就 X 呗”式脱身；C5 identity case 与 replay forbidden terms 同步补齐；`./scripts/test/run.sh agent agent_eval` 通过（289 passed）；`./scripts/check/run.sh` 通过（ruff + format + mypy 244 files + pytest 660 passed；frontend lint/test 跳过） |
| 2026-06-30 | 授权复跑 replay，发现“没纠正 / 猜中”样本 | issue 018 replay 未调用 `recall_past_chats`，输出“你没纠正我 / 猜中一次不代表我记得”，命中 forbidden 失败；因 replay 已失败，未继续消耗第二次 C5 smoke 调用 |
| 2026-06-30 | 补证据优先边界 | prompt 明确要求判断过往事实时先回历史片段或记录证据，不要凭用户暗示、用户没纠正或当前上下文顺着推断；C5 identity case 与 replay forbidden terms 同步补 `没纠正`；`./scripts/test/run.sh agent agent_eval` 通过（289 passed）；`./scripts/check/run.sh` 通过（ruff + format + mypy 244 files + pytest 660 passed；frontend lint/test 跳过） |
| 2026-06-30 | 根据外部最佳实践停止打地鼠式禁词 | 对照 OpenAI Model Spec / Memory FAQ、Microsoft HAX、Memory Sandbox 等资料后，将实现口径调整为 evidence-grounded correction：prompt 缩短为证据归因原则；C5 judge 改为机制暴露、无证据查记录声明、矛盾圆故事三类语义 guard；replay summary 同步输出三类命中 |
| 2026-06-30 | 授权复跑 replay，发现当前上下文被误表述为查记录 | issue 018 replay 未调用 `recall_past_chats`，但输出“去翻了一下之前和你的聊天记录”，被 `unsupported_lookup_claims` 捕获；因 replay 已失败，未继续消耗第二次 C5 smoke 调用 |
| 2026-06-30 | 补当前上下文与历史记录的归因边界 | prompt 明确区分当前对话上下文和历史记录：当前对话里看得到的信息不能说成翻/查记录，只有确实看到历史片段时才可自然提“看了下之前聊到的” |
| 2026-06-30 | 用户 review 后调整为通用工具契约方案 | 新增 `tool_usage_contract`：自然说“翻 / 查”可以，但必须真的发生；不再把该问题局限在 memory prompt；工具细则由 tool description 承担 |
| 2026-06-30 | 完成工具分层实现与文档沉淀 | `runtime_context` 收窄为运行时证据边界；`Tool.description` 改为详细说明；补齐 `web_search` / `recall_past_chats` description；新增 lesson 002 |
| 2026-06-30 | 完成工具契约重构后的非 LLM 验证 | targeted pytest 通过（50 passed）；`./scripts/test/run.sh agent agent_eval` 通过（290 passed）；`./scripts/check/run.sh` 通过（ruff + format + mypy 244 files + pytest 661 passed；frontend lint/test 因本地依赖未就绪由脚本跳过）；`git diff --check` 通过 |
| 2026-06-30 | 授权跑工具契约重构后的真实 LLM 验证 | issue 018 replay 最终通过（无机制泄漏、无无证据 lookup claim、无矛盾圆故事）；C5 limit1 smoke 最终通过（1/1，报告 `agent_eval/reports/2026-06-30T09-41-40-88a5735-C5-limit1.json`）；过程中发现 replay guard 漏掉“看了下之前聊到的 / 没翻到”等否定式 lookup claim，已补 guard 与 prompt；另一次 C5 smoke 因 `没认出来` 过宽 guard 误杀，已收窄 |
| 2026-06-30 | 完成授权验证后的非 LLM 门禁 | `./scripts/test/run.sh agent agent_eval` 通过（290 passed）；`./scripts/check/run.sh` 通过（ruff + format + mypy 244 files + pytest 661 passed；frontend lint/test 因本地依赖未就绪由脚本跳过） |
| 2026-07-01 | 扩充 C5 并补记忆矛盾 preflight | C5 从 5 条扩到 20 条；补 `recall_past_chats` 多 term OR 查询；在用户质疑记忆前后矛盾、机制诱导、当前轮归因诱导或只问证据时，编排层先做一次窄口历史核对并把结果注入本轮上下文；补 judge / replay guard 与对应单测 |
| 2026-07-01 | 授权跑扩充后 C5 全量复测并收敛实现 | C5 20 条真实 LLM 评测从 11/20 提升到 13/20；后续将 trigger 整理为语义分组、压缩 prompt 的 case-list 味道，最终复测保持 13/20（报告 `agent_eval/reports/2026-07-01T06-51-46-ca0e9cb-C5.json`）；剩余失败集中在生成阶段复述机制词或编归因故事，本轮不再继续堆 prompt/trigger，若后续处理应另立 evidence-bound response repair 方向 |
| 2026-07-01 | 完成扩充与 preflight 后的非 LLM 门禁 | targeted pytest 通过（42 passed）；`./scripts/check/run.sh` 通过（ruff + format + mypy 244 files + pytest 664 passed；frontend lint/test 因本地工具链/依赖未就绪由脚本跳过） |
