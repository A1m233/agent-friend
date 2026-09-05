# 060 · 已读会话在终态重收或迟到读取后重新显示蓝点

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-09-05
- **关联需求**：[115-assistant-phase-execution-disclosure](../../requirements/115-assistant-phase-execution-disclosure/requirement.md)（验收时发现，不属于本期修复范围）

## 现象

用户已点开看过的会话，随后在前端热更新/重载附近又出现“有新回复”蓝点，但对应任务没有重新运行。

本次只读核对的 Windows Tauri dev 属于 115 worktree；目标会话仅有一条已完成的运行快照，`finished_at=2026-09-05T13:18:57.326172Z`、`version=3`，当前 `unreadRunOutcomeBySession` 却为 `completed`。WebView navigation type 为 `reload`。115 的桌面验收探针也执行过整页 reload，不能只把触发者归因于 Vite HMR。

两种可触发序列：

1. 完成的可见回复已读 → 切到其它会话 → 前端状态重新初始化、后台仍保留该运行的近期终态 → 校准重收旧终态 → 蓝点重新出现。
2. 后台完成通知触发异步历史读取 → 用户点开看过该会话并离开 → 较早的历史读取返回 → 蓝点重新出现；此序列无需 HMR。

## 根因初判

- [`conversation.ts`](../../../frontend/src/stores/conversation.ts) 的 `markSessionViewed` 只删除内存中的未读标记，没有记录已读运行/版本依据。
- 同文件 `calibrateRuns` 拉取 `runsApi.list(true)`；`applyRunSnapshot` 接收终态后调用 `markCompletedRunUnread`。重新初始化后旧快照不再被内存版本去重，已完成且有可见内容的非当前会话会重新置为未读。
- `markCompletedRunUnread` 在异步读取返回后只检查当前选中会话、快照终态/版本与可见内容，不检查等待期间是否已读。打开后又离开的会话因此仍会被迟到结果重新点亮。
- 上述逻辑在基线 `c863ac793717f3b2335993a17471b5531d1326e4` 已存在。115 对 `conversation.ts` 的改动仅补充终态消息计时字段，没有修改已读逻辑。

证据基础为 code-supported + runtime-supported；隔离检查从当前源码提取 `hasVisibleRunMessage`、`markCompletedRunUnread`、`markSessionViewed`，使用模拟 store/API/可见性判断，复现两种序列，输出分别为 `beforeReload=null`、`afterReload=completed`、`openedAndLeftBeforePendingReadReturns=completed`。检查未改真实会话、未调用 LLM；未新增正式回归测试或归档该一次性检查脚本。

置信度：已读状态缺陷及两条代码机制 confirmed；用户截图究竟由哪一次 HMR、验收 reload 或迟到读取触发，仍缺少操作时序证据，不能唯一归因。

## 影响与判断

蓝点会把旧回复误报为新回复，影响阅读与切换判断；未发现该问题导致消息丢失、重新调用模型或重复执行工具。重新点开可暂时清除，但不能根治。

这是正式产品代码中的状态缺陷，不是仅存在于开发模式的渲染装饰。也不表示每次关闭或重启都会复现：当前关闭窗口通常只是隐藏，后台近期终态另有保留窗口。

用户明确决定本次不修，只把 issue 文档随 115 后续验收合入。它不作为本期 phase/折叠功能已修复的声明，也不扩大 115 的生产代码范围。

## 可选修复方向

- 为“已看过哪些完成结果”提供明确依据，并让终态校准与迟到异步读取统一尊重该依据；具体存储归属与生命周期在修复时设计，不写入模型 transcript。
- 复用现有会话产品事实与稳定运行身份，避免仅按“不是当前会话且已完成”判断新回复。
- 补覆盖已读后重收、读取期间打开再离开、真正新运行仍提示、失败提示以及隐藏/无可见内容运行的确定性回归。
