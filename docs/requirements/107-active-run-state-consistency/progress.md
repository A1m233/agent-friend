# 107 · 活动运行状态与界面投影一致性 - 进度

## 总体状态

COMPLETED

> 2026-08-28 的首轮交付已完成；2026-09-05 因主要活动仍可被多个表面同时投影而重新进入方案设计，补充方案现已确认并进入实现。下方既有完成项与验收记录作为首轮历史保留。

## 本轮任务列表

- [x] 建立聊天页唯一 `PrimaryActivityPresentation` resolver 与 store adapter
- [x] 把授权、运行中命令、压缩、普通工具、模型与 generic 迁移到同一选择结果
- [x] 提升社交登录请求快照并移除卡片局部平行轮询
- [x] 让 MessageList、ApprovalDock 与活动子组件只消费选择结果
- [x] 补齐组合矩阵、联合渲染、fake timer 与社交登录 store 回归
- [x] 运行聚焦检查与项目 `scripts/check`
- [x] 执行真实 Tauri 桌面验收并记录运行态证据
- [x] 邀请用户验收并确认本轮交付完成

## 首轮任务列表（2026-08-28，已完成）

- [x] 新增 canonical run presentation selector 与请求围栏 helper
- [x] 移除会话级重复 `streaming` / active map，并迁移 store action 与 session sync
- [x] 建立 ChatMessage run identity、terminal outcome 与历史投影边界
- [x] 落地 active message action bar 和唯一主要活动反馈
- [x] 收敛后台未读、失败语义与外部 run 控制范围
- [x] 收敛 command / filesystem approval / ToolCard 的 REST-push 与 terminal precedence
- [x] 补齐状态矩阵、反序、重连、组件和 item 回归测试
- [x] 运行局部检查与项目 `scripts/check`
- [x] 执行真实 Tauri 桌面验收并记录运行态证据
- [x] 邀请用户验收并确认交付完成

## 本轮 AC 验证

- [x] AC-3/AC-4：精确活动与 generic 互斥，跨表面只存在一个主要活动
- [x] AC-11/AC-12：工具、命令与授权保留审计信息但不重复争抢实时状态
- [x] AC-17：活动组合矩阵与联合渲染计数为 0 或 1
- [x] AC-18：既有需求与 Persona 编辑器消息时间线不回归
- [x] AC-19：真实 Tauri 覆盖压缩慢响应、工具、登录、Dock 与回退（dev-only 内存 fixture；非模型/Bridge E2E）
- [x] AC-20：项目完整门禁通过

## 首轮 AC 验证（2026-08-28，已完成）

- [x] AC-1~AC-5：整轮权威状态、action bar、通用活动、优先级、queued
- [x] AC-6~AC-10：切回、初始化、重连、terminal 收口、乱序隔离
- [x] AC-11~AC-16：工具/命令、授权、控制范围、后台结果、无正文、多会话
- [x] AC-17：确定性状态矩阵
- [x] AC-18：既有需求回归检查
- [x] AC-19：真实 Tauri 桌面验收
- [x] AC-20：项目门禁

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-28 | requirement、证据索引与技术方案确认；进入实现阶段 | 用户已明确授权写完设计后直接实现 |
| 2026-08-28 | 统一 run presentation、消息身份/终态、action bar、活动优先级、外部入口控制、后台结果与工具/授权/命令对账 | frontend 全量 114 文件 / 669 tests 通过；生产 build 通过 |
| 2026-08-28 | 完成项目自动门禁 | frontend lint/typecheck/test/build；backend lint、mypy 588 files、pytest 2284 passed / 4 skipped；Rust Tauri Core test + release check 全部通过 |
| 2026-08-28 | 真实 Tauri / WebView2 验收发现取消中仍显示“正在准备回答”，补为“正在取消当前任务，任务尚未结束…”并复验 | 修复后 frontend 全量 114 文件 / 670 tests、lint/typecheck/build 通过 |
| 2026-08-28 | 完成 active、terminal、queued、stopping、外部入口、命令授权和运行中命令的真实 WebView DOM / 截图验收 | runtime：当前 worktree、`feature/107-active-run-state-consistency`、Tauri、CDP 9222；UI 状态使用 dev-only 内存注入且已恢复，跨层语义由确定性测试覆盖；主要截图位于 `%LOCALAPPDATA%\Temp\agent-friend-107-*-cdp.png` |
| 2026-08-28 | 收尾桌面运行态 | 已执行 `scripts/dev/run.ps1 stop`，lease 释放且开发端口空闲 |
| 2026-08-28 | 用户确认验收并要求合入 `main`、推送远端 | 总体状态更新为 `COMPLETED`，进入受治理的 Guide 维护与 Git 合入流程 |
| 2026-09-05 | 补充需求与技术方案确认；用户明确授权开始实现 | 进入单一主要活动投影 owner、社交登录共享事实与组合回归实现 |
| 2026-09-05 | 完成聊天页单一主要活动 resolver、根组件唯一 owner、消息/Dock 纯消费迁移与社交登录共享快照 | 聚焦类型检查、ESLint 与 7 文件 / 47 tests 通过；前端全量 118 文件 / 709 tests 与 production build 通过 |
| 2026-09-05 | 完成项目分阶段完整门禁 | backend lint、mypy 605 files、pytest 2421 passed / 4 skipped；frontend lint、颜色/UI provenance/design token、709 tests、production build；Rust 218+6 tests 与 release check 全部通过 |
| 2026-09-05 | 尝试进入真实 Tauri 验收，被 desktop instance governance 安全拦截 | 当前安装版 production PID 28172 占用单例域；脚本未结束用户实例，AC-19 等待用户退出安装版后继续 |
| 2026-09-05 | 用户退出安装版后继续桌面验收；发现并修复根 adapter 的不稳定嵌套 selector 引发的白屏 | `usePrimaryActivity.test.ts` 挂载真实 Zustand adapter，修复前复现 Maximum update depth exceeded，修复后稳定；不再只验证纯 resolver 与叶子组件 |
| 2026-09-05 | 组合验收发现授权接管后登录卡仍使用实时文案，补齐被动呈现与展开工具卡的 pulse 隔离 | 同时补齐登录请求生命周期围栏、切走再切回的旧 GET/action 隔离、尚未返回 request 的社交工具投影、queued/stopping 对旧 model 状态的覆盖 |
| 2026-09-05 | 修复后复跑前端门禁并补齐单项/两两组合矩阵 | `scripts/check/run.ps1 --stage frontend` 全绿（当时 718 tests）；随后仅新增 28 个矩阵测试，`scripts/frontend/lint.ps1` 与 `scripts/frontend/test.ps1` 再次通过，最终 119 文件 / 746 tests；backend/Rust 无新增改动，沿用本轮已通过证据 |
| 2026-09-05 | 热更新后遇到 Context 模块身份不一致，停止旧开发实例并用冷启动实例完成最终复验 | worktree 为 fd7c/agent-friend，branch 为 feature/107-active-run-state-consistency，Tauri lease PID 49024，desktop PID 11624，processSessionId 11624-1788550281979，CDP 9222；所有产品修改均早于该实例启动 |
| 2026-09-05 | 最终真实 WebView DOM / 截图矩阵通过，未发生 pageerror | 覆盖压缩持续 11 秒、command/approval 接管、展开工具详情无额外 pulse、普通工具、社交工具启动、登录 continuation 与 approval 组合、generic、queued、stopping、外部 run、terminal、A→B→A；每步主要活动计数为 0 或 1，终态恢复 action bar。截图：`C:/Users/example-user/AppData/Local/Temp/agent-friend-107-{compaction,approval,socialApproval,stopping,terminal}-verified.png` |
| 2026-09-05 | 验收证据边界 | 使用当前源码的真实 Tauri WebView，并临时注入内存 fixture；结束恢复 store/API 并刷新页面，无真实 LLM 调用、无会话历史写入。这是 desktop DOM/screenshot 与工具详情展开交互证据，不宣称完整模型/Bridge E2E。用户最终验收仍待确认，不提交或合入 |
| 2026-09-05 | 收尾验收运行态 | `scripts/dev/run.ps1 stop` 已停止本次实例；`status` 确认无 active lease、owner process exited、开发端口全部空闲；`git diff --check` 通过 |
| 2026-09-05 | 用户确认本轮验收并明确要求合入主干、推送远端 | 本轮状态更新为 COMPLETED，进入 Guide 复核、需求提交与保留 merge commit 的主干合入流程；不重新构建安装包 |
| 2026-09-05 | 合入前 Guide 复核：无需更新公开知识 | 公开 Guide HEAD 287a4ca，基线 775db56 为产品 HEAD 1c2decd 的祖先；已核对基线以来 diff 与本轮 overlay。using-agent-friend/troubleshooting 已说明运行阶段、压缩、授权优先、终态及恢复语义，本轮为同一契约的去重与收口修复，不新增操作、权限或数据规则；公开 checkout clean 且验证通过，不发布新 release、不改 builtin |
