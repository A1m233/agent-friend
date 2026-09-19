# 137 · 证据索引

## 用途与当前判断

仅记录当前事故与会话连续性相关的证据；不保存完整私有会话、业务账号或凭据。修复已落地，并取得确定性回归、CPA 合成会话和真实 Tauri 验收证据；全量后端 3374 passed / 5 skipped、前端 1310 tests 与构建、Rust 检查均通过，用户已确认验收并授权合入、推送及 local-test 构建。合成验收输出单独保存在 evidence/。

- 思考强度变化使 input usage 的兼容性摘要失配，保留的不透明 reasoning 因缺少可用计量而按每段一个完整窗口预留。该机制与首次两次失败相符。
- 编辑重发入口缺少错误持久化回调，实际只记录了失败终态，未记录对应 `run_error`。
- 切回原思考强度后的正常发送仍重建了模型历史并触发压缩；该次压缩成功。重建的精确内存状态转移仍需完整入口确定性验证。
- 模型正确识别了最新请求，但上下文末尾出现两条旧任务的无明确归属中断提示；harness 注入缺陷已确认，其对模型停在旧状态调查上的影响 strongly-supported，未做真实模型反事实复跑。

## 身份与查询边界

- 调查时间：2026-09-19，Asia/Shanghai；实际事故窗口 20:20–20:40。
- 产品：0.3.0，packaged，production profile；build `20260919T095711Z-1ebee945baf8-d96d917f`。
- 历史 provenance：source commit `1ebee945baf86778a7b779740d3635701c6d5324`，dirty=true，source tree SHA-256 `b83cf2ec6717788c35e41f0f723e5adbc7ec279e6b3936cfc768d6b994f95b7d`。不得表述为 clean HEAD 上的运行。
- 已逐文件校验安装包与调查源码相同：`agent/conversation.py`、`agent/context/usage_anchor.py`、`agent/context/summarizing.py`、`llm_providers/reasoning.py`、`llm_providers/connections/directory.py`、`agent_bridge/routes/meta.py`、`agent_bridge/protocols/ag_ui/encoders.py`、`agent/runtime/__init__.py`。这不是整个安装包与仓库一致的证明。
- 查询原始位置：通过产品 `agent.paths.log_dir()` / `sessions_dir()` 对应的本机日志及 session JSONL；检索 run ID、按事件类型提取配置、usage、rollout 和终态。仅查询本次用户指定会话，未扩展扫描其它私人会话。
- 会话脱敏关联：日志中的 `session_ref=bb74ebf01628b8cdd7800abc`。R1=`38b0029d-b6d5-4c7d-8b23-6fb61793260c`，R2=`faa9bc33-e93d-4dd5-8d0f-419bb343ee1a`，R3=`d1160a55-45a0-4ddd-b494-c5097e7b30d4`。

## 证据账本

| ID | 问题、来源与锚点 | 观察、解释与局限 | 基础 / 置信度 / 状态 |
| --- | --- | --- | --- |
| E-001 | R1：20:20:16 配置从 low 改为 medium；20:20:54 窗口 272000；20:21:38 `summary_result_over_budget` | 摘要调用约 44 秒后暂停；正式回答调用和业务工具均为 0。usage 兼容性把 reasoning/defaults 纳入判定，失配后丢弃旧计量；未知 reasoning 每段预留一个窗口 | runtime-supported + code-supported / confirmed / active |
| E-002 | R2：窗口 400000 生效；20:31:17 同类压缩失败；meta.py 的 edit-resend submit 与普通 ag_ui submit 对照 | 摘要约 47 秒；有失败 terminal，无本轮 run_error，普通发送有错误回调。具体桌面画面未验收 | runtime-supported + code-supported / confirmed / active |
| E-003 | 诊断中的离线局部对照；历史 60 条，原工具 31 项，最后真实 input=84288 | low + 400k 得到 85582，medium + 400k 得到 5341223。只改变配置的局部计数机制成立；没有经过发送前 reconcile/repair，不能推出真实发送不压缩 | controlled-comparison / confirmed（仅局部机制） / retained |
| E-004 | R3：20:35:54 切回 low；rollout 从 60 条变为 1192 再到 1193；20:36:48 context build | 压缩前估算 697956、后 187750，阈值 320000；旧 checkpoint 为 `checkpoint_boundaries_not_contiguous`；随后正式调用和工具执行成功。推翻了“切回 low 即可避免压缩”的扩张结论，未推翻 E-003 的局部数值 | runtime-supported / confirmed / active；invalidates E-003 的完整路径推断 |
| E-005 | R3 首次被接受的模型历史：212 条；最新业务输入索引 200，两个 turn_aborted 索引 201–202，当前继续输入索引 210 | 两条提示对应 9 月 14 日和 9 月 19 日凌晨的旧 run，正文均以“上一轮”指代，run ID 仅在未发给模型的 metadata。当前最新请求被保留，模型也明确识别目标；问题是执行状态串轮，不是当前请求被摘要删除 | runtime-supported + code-supported / confirmed（错误注入） / active |
| E-006 | R3 首次回答、后续工具和最终回复；当前日期工具与历史日志时间 | 模型先调查中断状态，后把当日下午日志联系成中断前业务，最终未执行当前业务。旧日志属于当前任务的证据缺失。因果影响 strongly-supported，未做删除错误提示后的真实模型对照 | runtime-supported / strongly-supported / active |
| E-007 | Codex 官方源码 `78245b47af2a7aafcabe025828ceecca69db4df1`，2026-09-19 实际读取 | 计量复用最近 usage 并加新增 items；中断在对应 task 的 turn context 中记录 marker 并先落盘；不等同于证明闭源 Desktop 内部实现 | code-supported / confirmed（开源行为） / active |
| E-008 | Codex App Server 官方 Events 文档，2026-09-19 实际读取 | failed turn 携带 error；item 完成事实与 turn 生命周期明确。作为 AF 错误持久化、恢复和展示契约的参考 | official-documentation / confirmed（文档契约） / active |
| E-009 | 基线源码 `_append_tool_call_request_event` 与 `_restore_context_usage` 对照 | 无文字的工具轮把 context_usage 保存到 tool_call_request，但恢复只扫描 assistant_message；最新可信计量因此可能遗漏。尚未把该机制绑定为本次事故的触发原因 | code-supported / confirmed（读写契约遗漏） / active |
| E-010 | 2026-09-19 隔离临时 Session + 仓库 `_FakeLLM`、`_make_conversation`、`SessionRolloutRecorder`；无网络调用 | 20 轮合成历史先成功压缩并提交，再完成一条用户任务。business/model=44/11；编辑重发正确选中 9 条前缀，但在 context build 强制失败后 model 仍为 11、active identity 不匹配；warm 与 cold reconcile 均把模型历史重建成 43 条，reason=business_history_changed。证明失败编辑的分支与模型历史未同步；为机制复现，不是私有事故精确重放 | reproduction-supported + code-supported / confirmed / active |
| E-011 | 同一 Codex SHA 的 `session/rollout_reconstruction.rs:50,103` 与 `compact_remote_v2.rs:360` | surviving replacement-history checkpoint 被作为完整历史基线，恢复 suffix 必须来自同一 surviving segment；压缩安装替换历史。与 AF 编辑失败后回退全量业务历史的风险形成对照 | code-supported / confirmed（开源行为） / active |
| E-012 | Bridge 所有 submit 调用点的静态盘点 | ag_ui 普通发送、OpenAI（含语音）、IM、手动继续各自配置 error_callback；编辑重发、定时任务、提醒和社交授权跟进未配置。后三者另有业务状态 observer，不能仅凭缺 callback 宣称完全没有错误反馈；需分别验证 session 历史的失败原因 | code-supported / confirmed（调用点差异），用户体验影响待验证 / active |
| E-013 | `test_context_recovery_consistency`、`test_context_summarizing`、`test_turn_context_lifecycle` | v4 身份排除输出控制、工具轮 usage 恢复、v3 校验迁移、未知成本只预留一次；不可压缩 tail 在摘要前拒绝；新旧失败编辑 warm/cold 均保留摘要与新请求，provider 拒绝/取消不提交候选摘要 | deterministic-regression / passed / active |
| E-014 | `test_runtime_edit_failure_is_durable_without_route_callback`、`test_run_errors` | 使用真实 Bridge factory 和 runtime 执行编辑重发失败，只有一条带文案的 run_error，重载仍可读；默认策略覆盖 user-visible system turn，排除 memory_only；按 run 去重 | deterministic-integration / passed / active |
| E-015 | `scope_interruption` 回归、runtime cancellation 与 rollout 测试 | 旧 marker 归位、明确身份/时间、未知/未执行/已知结果区分、重复恢复幂等；不再保留失活运行的孤立 marker；恢复不调用业务工具 | deterministic-regression / passed / active |
| E-016 | [CPA 报告](evidence/cpa-smoke.json)，`context-recovery-smoke/run.*` | 合成会话 3 轮：low 输入估计 8691；medium 复用真实 7787 + 增量 38 = 7825；失败编辑后 cold 复用 7787 + 45 = 7832，回复 REPLACEMENT137，压缩次数 0。窗口 400000；仅证明这些样本，不是长会话稳定性统计 | real-provider-smoke / passed / active |
| E-017 | [编辑失败刷新后记录](evidence/desktop-edit-error-reloaded.json)、[截图](evidence/desktop-edit-error-reloaded.png)、[恢复记录](evidence/desktop-success.json)、[截图](evidence/desktop-success.png) | 当前 worktree 的真实 Tauri，隔离 DATA/LOG。普通发送与编辑超大合成输入均在模型前失败；UI 有明确原因，编辑失败刷新后只投影该 run 错误；重启 Bridge 后通过真实 UI 改短输入，CPA 回复 DESKTOP137，持久化 completed。截图已实际检查 | desktop DOM/screenshot + product E2E / passed / active |
| E-018 | 隔离桌面日志的 model_call_observation | 桌面实际调用为 conversation 1、notification 1、memory_extraction 1，各 HTTP attempt=1，均 CPA luna。前置估算仅计对话而漏计两项产品配套调用，已向用户更正；加 E-016 共 6 次，不把配套调用隐去 | runtime-supported / confirmed / active |

## 官方参考锚点

- [usage 与新增项计量](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/core/src/context_manager/history.rs#L769)
- [上下文预算判断](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/core/src/session/context_window.rs)
- [任务中断 marker 的记录和 flush](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/core/src/tasks/mod.rs#L948)
- [App Server Events](https://developers.openai.com/codex/app-server#events)
- [恢复时的 surviving checkpoint 与 suffix](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/core/src/session/rollout_reconstruction.rs#L50)
- [压缩成功后安装替换历史](https://github.com/openai/codex/blob/78245b47af2a7aafcabe025828ceecca69db4df1/codex-rs/core/src/compact_remote_v2.rs#L360)

## 处理结论与剩余边界

- E-001/E-003：已分离输入兼容性与输出设置；真实路由、输入表示和未知设置继续失效。未知 reasoning 显式报告 unknown_components，保守 admission 不取消。
- E-004/E-010：已增加用户输入接受边界及有效 rollout 前缀恢复；私有事故不声称已完整重放，原始用户会话没有写入。
- E-002/E-008：默认错误持久化在 Bridge factory 注入 Runtime；既有渠道回调继续按原来源/暂态会话策略覆盖，按 run 幂等防止重复。相邻路径范围见 progress。
- E-005/E-007：模型视图修正历史 marker；未更改原始工具事实、权限状态或执行调度。
- E-006：E-016 证明合成失败编辑后可承接新任务；旧中断反事实对照、MAA 业务任务完成率和大样本模型可靠性仍未验证，不能由当前 smoke 外推。

## Artifact 与隐私

原始日志、session、安装包仅在本机原位置读取，未复制进仓库，未修改真实会话。日志会轮转，session 会继续增长；此索引不声称原文件整体 hash 恒定或完整 transcript 已归档。公开回归应使用合成、脱敏 fixture，并保留上述历史 provenance，不能用后补 fixture 冒充事故完整重放。

E-010 的一次性探针通过 Python stdin 执行，复用仓库 fake/helper，在系统临时目录创建独立 sessions/personas/data；临时数据未作为长期 artifact 归档。保留上述输入结构、精确调用顺序和结果作为后续正式回归的依据，不把临时文件可用性当作验收条件。

## 对账记录

| 日期 | 结果 |
| --- | --- |
| 2026-09-19 | 需求范围已确认，分支从上述 main SHA 创建并远程占号；尚无设计确认、产品修复或修复后验证。局部计数建议被完整运行证据推翻的部分已显式保留。 |
| 2026-09-19 | 完成失败编辑的合成完整入口复现，补充工具轮 usage 读取遗漏与多入口错误 owner 盘点；仍处于方案确认前，只修改需求文档和描述性证据。 |
| 2026-09-19 | 用户授权实施及 CPA/桌面验证后，完成 E-013–E-018。验收输入版本见 [source-inputs.json](evidence/source-inputs.json)，为基线上的未提交工作区；不冒充已发布版本。 |
