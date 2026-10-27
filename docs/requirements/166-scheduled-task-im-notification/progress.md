# 166 · 定时任务 IM 结果通知 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 需求、方案确认及分支占号。
- [x] 配置、迁移、快照、通知记录及管理入口。
- [x] 提取公共一次性 IM 投递流程，迁移原提醒调用。
- [x] 接入定时任务终态、结果提取、后台投递及生命周期。
- [x] 任务编辑开关和运行历史投递反馈。
- [x] 定向回归测试与项目统一检查。
- [x] 真实 Tauri 开关交互、保存恢复与投递历史展示验收。
- [x] 真实 IM / 界面验收及用户确认。

## AC 验证

- [x] AC-1 配置及管理入口：存储迁移、工具和 REST 测试；桌面操作与持久化记录交叉核对。
- [x] AC-2 三种触发与最终回复：触发登记测试、同 run 的 final phase / legacy / partial / tool 边界测试。
- [x] AC-3 异常状态与无额外模型/业务执行：失败、中断、拒绝及不通知终态测试；组件只读取业务事件并调用已有 IM 发送入口。
- [x] AC-4 绑定目标：多绑定、无绑定、dispatch 前切换会话测试，使用 fake provider，真实接收归 AC-8。
- [x] AC-5 独立投递结果与调度隔离：超时、拒绝、绑定变化及慢发送测试；桌面历史夹具显示执行与各目标结果。
- [x] AC-6 去重、重启和旧历史兼容：并发 claim、回执写入失败、重启 pending/unknown/failed、旧库迁移测试。
- [x] AC-7 复用、回归及统一检查：Todo 与任务通知共用 deliver_once → IMRuntime.send_proactive → provider；原提醒回归及 backend/frontend/rust 三阶段通过。
- [x] AC-8 真实验收和用户确认：用户确认手机 QQ“已收到，内容正确”，与本次 scheduled 运行、平台成功回执及桌面历史对应。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-10-27 | 需求与方案已确认，用户授权实现；当前分支 feature/166-scheduled-task-im-notification | 基线 6f7687bf；仅需求文档为本任务已有改动；未授权真实对外消息或桌面视觉验收 |
| 2026-10-27 | 已完成配置/快照/迁移、任务通知记录、公共 deliver_once、Todo 复用、后台投递、dispatch 绑定复核、UI 开关及逐目标状态 | 首轮原相关测试 61 passed；新增核心 16 passed；统一检查进行中 |
| 2026-10-27 | 用户授权由 Agent 启动真实桌面端做视觉验收 | 尚未授权向真实 QQ 发送测试消息；视觉验收使用隔离测试数据 |
| 2026-10-27 | 前端统一检查与 Rust 统一检查通过 | frontend: 193 files / 1558 tests + production build；Rust: 335 + 1 + 7 tests + release check；既有 CSS/chunk 与 dead-code warnings 保留 |
| 2026-10-27 | 后端统一检查通过；最后的 worker 停止与工具参数调整已定向复验 | backend: lint + mypy（844 source files）+ 4280 passed / 5 skipped；最后相关复验 42 passed；全量收集后新增的回执写入失败和无会话提交拒绝用例已包含在定向复验中 |
| 2026-10-27 | 完成用户授权的真实 Tauri 验收，并停止本任务运行态 | 新任务默认关闭、开启保存、重新编辑、关闭保存、刷新恢复、再次开启均通过；后台任务记录保持一致。历史使用隔离夹具，不代表真实 QQ 已送达。stop 后无 active lease、dev ports are free |
| 2026-10-27 | 用户授权真实 QQ 接收验收；真实定时运行及平台发送通过 | 22:36:03 自动 scheduled 触发，22:36:12 completed；真实 QQ delivered，有 provider message receipt，attempt_count=1。手机客户端接收待用户确认 |
| 2026-10-27 | 用户确认真实 QQ 接收“已收到，内容正确”；完成验收 | 对应 AF166-20261027-QQ 通知，补齐 AC-8；总体状态更新为 COMPLETED，尚未提交或合入 main |
| 2026-10-27 | 提交并合入主干、推送远端，发布公开 Guide r101 | 实现提交 9f2d07b1，主干 merge a2762e43；合并树与已验收实现一致。Guide eea27575 已推送，r101 immutable，兼容范围 0.4.1 至 0.5.0（不含） |
| 2026-10-27 | 完成本地测试安装包，未安装到 production | 用户授权工作目录改用 D:/agent-friend-packaging；build 20261027T144858Z-a2762e43bf89-d982d1f8，0.4.1 / local-test / ordinary / standard。冻结 doctor 全通过，原生符号已归档，SHA 与清单一致，临时 Guide 基线恢复后工作区 clean |

## 检查与验收证据

- 统一入口：`scripts/check/run.ps1 --stage backend`、`--stage frontend`、`--stage rust` 均 exit 0。后端仍有依赖库 coroutine / atexit 清理 warning，未导致用例或阶段失败。
- 定向入口：`scripts/test/run.ps1`，覆盖 `agent/tests/test_scheduled_notifications.py`、`agent_bridge/tests/test_scheduled_im_notifications.py`、`test_scheduled_task_routes.py`、`test_im_runtime_onboard.py` 以及原调度 / 提醒测试。最后三份 bridge 测试共 42 passed。`git diff --check` 通过。
- 桌面身份：当前 worktree、`feature/166-scheduled-task-im-notification`、Tauri 模式；lease PID 12196，app PID 12252，bridge PID 38160 于 22:17:25 启动，晚于最后业务源码修改；CDP 9222 选择实际 `chat.html` WebView。
- 证据层级：desktop DOM via CDP / desktop interaction / 实际 WebView 全视口截图；任务设置具备桌面 → REST → SQLite 的 product E2E 证据。未覆盖原生系统窗口装饰。
- 隔离数据及本地证据目录：`C:/Users/example-user/AppData/Local/Temp/agent-friend-166-visual-2a86772c35b74dd6b79392bc6806d52e/`。`editor-enabled.png` 为已开启编辑界面，`history.png` 为投递历史，`history-dom.txt` 为 scoped DOM，`verification.json` 为持久化与夹具标识。测试任务 `cd834d3d-77fd-4b36-9a09-e6245354c451` 已暂停。
- 历史夹具在一个事务内写入已完成运行和 settled 投递记录，未创建可执行请求；展示 delivered / failed / unknown / skipped 四种目标结果。没有真实 QQ 发送或模型调用。
- 打包影响：仅静态导入与业务逻辑，没有依赖、资源和冻结打包输入变化；未构建安装包。
- 真实 QQ 发送证据（用户授权的单次 per-run）：当前分支 Tauri 实例，app PID 37300、bridge PID 18488；使用现有 QQ 凭据和共享 provider lock，测试数据 / 绑定另存隔离目录，生产 `im_state.json` 不变。模型 `my cpa · gpt-6.1-sol` / `effort:low`，一轮真实模型请求，未调用工具，未额外生成通知摘要。
- 真实测试任务 `9bc7c60c-eea3-452d-b9a5-35bf4fa8ceba`，occurrence `620de35f-44fd-47b4-a618-3bd0cd628e55`，run `76f31774edbe43eca9a0041ef446e107`；trigger_kind=scheduled，运行 completed，通知 settled，唯一目标 delivered 且有回执，尝试次数 1。最终回复与通知正文均包含 `AF166-20261027-QQ：定时任务已执行完成，这条最终回复应由系统自动发送到绑定的 QQ。`。
- 真实测试本地证据：`C:/Users/example-user/AppData/Local/Temp/agent-friend-166-live-qq-4a47fb9491fe4fbabc0f8553c7db8d6a/` 下 `task.json`、`history.json`、`events-summary.json`、`completed-dom.txt`、`completed.png`。实际桌面显示“已完成 / QQ 私聊 …F657：已发送”，与 SQLite 及业务会话事件一致；不再是注入夹具。
- 一次性计划已耗尽，无后续执行；测试 runtime 已 stop，确认无 active lease、dev ports free；已通过 build-storage reconcile 解除停止后遗留的本任务构建占用记录，未删除缓存。未改生产代码。
- 接收端证据：本机没有已登录的 QQ 接收窗口，手机接收由用户人工确认；用户对上述测试标识明确回复“已收到，内容正确”。结合真实执行、平台回执和桌面历史，AC-8 已通过，总体 COMPLETED。
- 本地测试制品目录：`C:/Users/example-user/agent-friend-releases/_local-test/0.4.1/windows-x64/ordinary/20261027T144858Z-a2762e43bf89-d982d1f8/`。安装包 419484393 字节，SHA-256 `fc9680ed76640bdbd88eb8c29c0bcb1faf30deb74f285d42eb322ebf53665ad0`；`release.json` 绑定来源提交 a2762e43、Guide r101 的临时基线与 native symbols，标记不可分发。本次仅生成测试包，不声称真实安装或覆盖升级已验证。
