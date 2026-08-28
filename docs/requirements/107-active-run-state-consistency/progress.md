# 107 · 活动运行状态与界面投影一致性 - 进度

## 总体状态

COMPLETED

## 任务列表

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

## AC 验证

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
