# 135 · 实施进度

## 总体状态

IN_PROGRESS

2026-09-19 用户明确授权开始实现。分支 `feature/135-context-degradation-task-continuity`。

## 任务列表

- [x] 压缩失败历史保全与重试生命周期
- [x] 原工具调用有界发现与读取
- [x] 工具注册、身份注入及摘要契约
- [x] 确定性测试及项目门禁
- [x] 按治理准备行为评测用例与明确执行边界
- [ ] 原自定义模型评测入口及真实调用授权/验证
- [x] 用户确认当前实现合入（真实模型验证缺口保留）

## AC 验证

代码实现与确定性门禁已完成；真实模型、游戏及桌面视觉验收未运行。用户在知悉真实模型验证缺口后明确要求合入主干并推送；本次交付接受确定性验证结果，真实行为证据继续保留为未完成项。

| AC | 当前证据 | 边界 |
|---|---|---|
| 01、02 | `test_context_summarizing.py`：失败保留历史、不采样主模型、用户输入不重复、后续重试 | 模型是否自然承接待真实验证 |
| 03、08 | 工具续轮失败后已完成结果保留且不重放；已提交 rollout 不被裁剪，重新加载后可继续 | 已纳入 321 项定向门禁通过结果 |
| 04、05 | `test_tool_call_history.py`：搜索、参数分页、结果引用读取、扫描上限、取消、失效游标 | 只证明确定性恢复机制 |
| 06、07 | 当前会话身份宿主注入、有效分支过滤；事件级关联和未知/冲突状态；只读工具没有执行入口 | 不把历史权限声明当作新授权；现有 scope 生命周期由既有测试覆盖 |
| 09 | 新增 C18-06 / C24-10 派生隔离场景及 judge 正反例 | 未执行真实 LLM；不是完整生产事件回放，也不验证摘要生成质量 |
| 10 | backend：3330 passed / 5 skipped；frontend：1310 passed + build；Rust test + release check 均通过 | 用户已明确要求合入；真实模型验证未完成 |

## 行为验证清单（待授权）

- 目标：C18-06 原参数发现；C24-10 无结果时保留未知状态且不重放。
- 相邻 control：C24-09 已有窗口不重启；C25-01 旧 scope 与授权状态。未将这些 case 内容写入产品指引。
- 新增目标用例来自 E-001 的派生隔离，原事故模型为自定义连接中的 `gpt-5.6-luna`。重新只读核对 model_config_change，事故前最后配置为该 custom model reference + `effort:low`，不能换成 OpenRouter 同名模型后声称原配置复验。私有连接 UUID 不归档到公开仓库。
- 已确认验证入口缺口：当前 agent_eval CLI `_provider_spec` 只装配内置 provider，没有读取桌面自定义 ModelDirectory；因此不能直接把上述 custom reference 传给现有 CLI 当作有效复验。正式运行前需补齐/选定原连接可用的评测入口，或由用户明确选择并标注替代路由观察。相邻 control 的本轮模型也需确认，不能静默套用默认。
- 预计 4 个 case、约 8–20 次模型请求；业务工具只用隔离历史及 fake 回执，不运行游戏。实际 CLI/模型/选择、leakage audit 与 report 位置在获得授权后冻结。
- E-003 的原模型输入不可精确还原：用宿主“压缩失败即暂停、不会在裁剪上下文上继续采样”机制回归覆盖，不将派生 case 称为 exact replay。
- 不用确定性通过率宣称摘要质量或任务连续性已稳定解决。

## 打包与入口边界

本次为 `standard`：无新增运行依赖、动态导入、资源读取协议或原生库；新模块为静态 Python 导入，复用既有 JSONL 查询与 JSON/schema 路径，spec 的 `collect_all(agent)` 覆盖模块收集。未新增冻结环境特有的高风险路径，因此不构建 runtime 或 installer。没有前端代码改动，不执行桌面视觉验收，也不作视觉正确性声明。

## 实现日志

| 日期 | 内容 | 验证或备注 |
|---|---|---|
| 2026-09-19 | 开始实施；继续对照 evidence-index.md 的 E-004 至 E-007 | 不将 fake 验证当作真实模型行为验证 |
| 2026-09-19 | 阻断有损 FIFO 候选；失败活动 discarded；新 run / 无 run ID 调用可重新尝试；模型参数不兼容给出配置修复提示 | 保持原错误 code 与历史提交 owner |
| 2026-09-19 | 加入 inspect_tool_calls 的当前会话搜索/读取、事件关联、游标与取消；摘要模板版本升为 2 | 无业务执行副作用、无新权限能力 |
| 2026-09-19 | 定向回归首次 210 passed / 1 failed（fixture inventory 总数尚未更新）后修正总数；后端 lint/typecheck 已通过，完整门禁继续执行 | 后续新增集成测试仍需核实结果 |
| 2026-09-19 | 最终定向 backend 门禁：321 passed，lint/typecheck 通过；frontend：157 files / 1310 tests + production build 通过；Rust test + release check 通过 | 原全量 backend 3319 passed / 5 skipped / 1 inventory 断言失败已修正，正在重跑最终全量 |
| 2026-09-19 | 对照 Codex MidTurn 失败结束尝试、PostTurn 保留已完成结果；AF 通过阻断有损候选及保留原工具结果完成对应宿主边界 | 未新增 post-turn compaction；未将 Codex Desktop 内部实现作为依据 |
| 2026-09-19 | 扫描产品摘要及新工具代码，无 C18-06/C24-10、fixture-heron/fixture-lake、事故 source 标记；人工核对新增指引为通用连续性与只读证据契约 | 只证明未发现此类泄漏；真实 transcript 仍待授权验证 |
| 2026-09-19 | 最终完整 backend 门禁：3330 passed / 5 skipped / 3 warnings，退出 0；全仓 lint/typecheck 通过；git diff --check 通过 | 有第三方异步清理警告，不影响测试退出码；未把 skip 计作通过 |

## 最终验证命令

- `scripts/check/run.ps1 --stage backend -q --tb=short`：通过，3330 passed、5 skipped。
- `scripts/check/run.ps1 --stage frontend`：通过，157 test files、1310 tests，production build 完成。
- `scripts/check/run.ps1 --stage rust`：通过，cargo fmt / test / release check。
- 额外定向 backend 门禁：321 passed，覆盖 context、tool history、tool loop、factory、Bridge 错误与 AG-UI、agent_eval。
- 不对未执行的真实模型行为、桌面视觉、冻结 runtime 或真实游戏操作作通过声明。

## 合入授权

2026-09-19 用户明确要求“合入主干推送远端”，授权提交、合入与推送当前实现。真实模型评测入口和实际调用仍未执行，不将合入视为这部分验证通过。
