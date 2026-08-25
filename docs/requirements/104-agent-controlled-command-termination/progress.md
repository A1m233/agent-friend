# 104 · Agent 可控的受治理命令终止 - 进度

## 总体状态

IN_PROGRESS

## 任务列表

- [x] 实现共享终止 pending / confirmed 状态机与稳定错误语义
- [x] 新增并注册模型可见 `terminate_command` 工具及隐藏上下文注入
- [x] 补齐 Bridge、历史和前端命令活动投影
- [x] 固化真实事故的 agent_eval anchor、隔离 case 与 controls
- [x] 完成后端、前端、agent_eval 与既有取消入口回归
- [x] 完成项目门禁与 Windows frozen package-sensitive capability smoke
- [x] 整理 AC 证据并交由用户验收

## AC 验证

- [x] AC-1 模型可见终止能力
- [x] AC-2 所有权与授权边界
- [x] AC-3 权威终态与进程树清理
- [x] AC-4 同一 run 内继续执行
- [x] AC-5 重复与竞态安全
- [x] AC-6 失败与失联不冒充成功
- [x] AC-7 既有取消入口兼容
- [x] AC-8 Provider 契约与行为回归（隐式意图模型失败按用户决定作为已接受残余风险）
- [x] AC-9 跨平台与项目门禁

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-24 | 用户确认技术方案并明确授权写完后直接实现；创建 Phase 3 任务与 AC 验证清单。 | `requirement.md` 与 `design.md` 均为 `CONFIRMED`；尚未修改生产代码。 |
| 2026-08-24 | 完成共享终止状态机、独立 `terminate_command`、run 所有权约束、失败/失联语义、前端投影和 C27 行为契约。 | 后端全量门禁：2174 passed、4 skipped；前端全量门禁：107 files / 615 tests；agent_eval 定向回归：122 passed；无真实 LLM。 |
| 2026-08-24 | 完成源码命令运行时 smoke 与 Windows frozen `package-sensitive` candidate。 | build `20260824T150404Z-b12ddc35ca93-caaeabee`；doctor `ok: true`；`capability:governed_command_runtime` 验证 `agent_termination_tool: true`、`cancelled: true`、`child_tree_cleaned: true`。 |
| 2026-08-24 | 只读完成 Guide package-baseline。 | Guide `b26c637` / immutable `agent-friend-guide-r8` / snapshot `64267f…`；产品 `0.3.0` 兼容；当前 builtin 匹配。新能力后续需更新公开能力说明与排障边界，本次未修改或发布 Guide。 |
| 2026-08-24 | 补齐 Rust 与 agent_eval 官方入口门禁。 | Rust：216 passed、1 ignored；installer helper：6 passed；release check 通过。`scripts/test agent_eval`：132 passed。 |
| 2026-08-24 | 经用户逐次授权，使用事故模型启动 C27 五 case 首次真实验证。 | 模型 `deepseek/deepseek-v4-flash-vision-exp`；console 已观察 C27-01、C27-03 通过，C27-02、C27-04 失败，C27-05 在中断时已开始。按失败归因门禁中断 batch；当前 runner 只在整批结束后统一写报告，因此没有 transcript/report 可供可靠归因，AC-8 保持未完成。 |
| 2026-08-24 | 经用户确认完成真实 Tauri/WebView2 视觉验收。 | 当前 worktree 的 Tauri runtime 与 CDP 身份、新鲜度已核对；用 dev-only 状态注入展示 `cancelled + agent_requested`，展开卡片明确显示“Agent 已终止”，DOM 不含“运行中”或自然退出文案。证据层级为 desktop DOM via CDP，不冒充跨层 product E2E；WebView 截图 SHA-256 `35297f9b…`。运行态已停止并释放 lease。 |
| 2026-08-25 | 完成首次真实 eval 失败归因并修正 C27 评测保真度。 | 确认旧 case 用跨 user turn 历史事件冒充当前 run handle、替代 argv 未定义、fake exec 奖励编造、已退出 control 依赖单一字面 marker。C27-01～05 均改为当前 tool loop 先执行原命令并取得 handle，原命令/替代命令按精确 argv 匹配；`scripts/test agent_eval` 为 133 passed。产品工具与 runtime 未修改。 |
| 2026-08-25 | 经用户授权逐 case 运行修正后的事故模型 eval，并人工复核 C27-01。 | report `2026-08-24T16-33-25-b12ddc3-C27-01.json`：模型先正确执行 streaming 原命令，但随后连续三次用 `write_stdin(wait_for=exit)` 等待，第三次因 `tool_budget_exhausted` 未执行；没有调用 `terminate_command` 或替代命令。人工判定为本次模型选路失败，不是 judge 假阳性；C27-02～05 按失败门禁未运行，AC-8 保持未完成。 |
| 2026-08-25 | 完成 reasoning `off` / `max` 对照与中性 postmortem probe。 | `max` 将单例耗时从约 17 秒增至约 79 秒，但仍重复 `write_stdin(wait_for=exit)`；两档事后自述均把“可信终态”收窄成自然退出，并未把“必须切换”识别为放弃旧进程。结果只支持本样本模型选路解释，不代表隐藏推理或稳定能力上限。 |
| 2026-08-25 | 收紧 `write_stdin` / `terminate_command` 通用互斥路由并复验目标与 controls。 | 新说明明确重启/替换/切换路径属于终止场景、`cancelled` 是可信终态；C27-01 仍等待至预算耗尽，C27-02 将直接执行请求误判为假设而未调用工具。C27-03 安全选择 `wait_for=output`，原 fixture 机械拒绝该合法路径，已修正且不重跑；C27-04 已退出 control 通过；C27-05 精确终止目标句柄、保留无关句柄并完成替代命令。`scripts/test agent_eval` 为 136 passed。 |
| 2026-08-25 | 用户确认接受隐式终止意图模型选路失败为残余风险，并授权推进合入主干与远端。 | AC-8 调整为确定性契约、显式真实成功和安全 controls 的硬门槛；保留 C27-01/02 原始失败报告，不追加业务词、全局 gate 或特定模型 prompt 补丁。总体状态继续保持 `IN_PROGRESS`，待同步主干和最终门禁后转 `COMPLETED`。 |
