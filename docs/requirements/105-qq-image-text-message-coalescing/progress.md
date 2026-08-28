# 105 · QQ 附件与文字消息合并输入 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 新增 QQ 附件配文合并策略、事件分类与单次合成状态机
- [x] 在 IMRuntime 的 resolver/Router 前接入 3 秒 pending、timeout 与顺序结算
- [x] 为 IMBindingCoordinator 增加 pending turn reservation
- [x] 补齐 stop、unbind、provider replace 和 stale binding 生命周期清理
- [x] 完成 production assembly 装配与结构化日志
- [x] 新增合并策略、runtime 集成和控制协调器自动化测试
- [x] 运行受影响定向测试
- [x] 运行 `scripts/check` 全量门禁
- [x] 修正平台事件与逻辑用户输入边界，支持一次 QQ 多图发送聚合
- [x] 将批次收集移出长 Agent turn 锁，消除数十秒 pending 膨胀
- [x] 补齐多图、独立发送、长回复排队与生命周期回归
- [x] 按真实时序收紧后重跑受影响定向测试与 `scripts/check` 全量门禁
- [x] 在真实 QQ 客户端完成一次选五图、五次单发和一次选两图对照，确认平台原始事件、分界特征与最终回合行为
- [x] 将硬上限改为内部可选能力、QQ 生产默认关闭，并重跑最终门禁

## AC 验证

- [x] AC-1：单条可读取附件等待 3 秒，窗口内不触发 Agent；超时后只处理一次
- [x] AC-2：一次 QQ 多图发送拆成多个附件事件时，只形成一条多附件用户消息、一次 Agent 处理和一组回复
- [x] AC-3：同绑定同私聊的首条纯文字在窗口内与完整附件批次合并并只处理一次
- [x] AC-4：合并事件在会话历史中只形成一条附件加文字用户消息，既有持久化语义不变
- [x] AC-5：普通纯文字消息保持立即处理
- [x] AC-6：不同 Bot、私聊或 session 严格隔离
- [x] AC-7：真实独立发送跨越静默窗口时分别结算
- [x] AC-8：默认无硬上限时持续到达的附件保持同批；显式配置硬上限时仍能强制结算
- [x] AC-9：已有长 Agent run 不阻塞下一批入站聚合或放大批次计时
- [x] AC-10：GIF、语音和不支持类型保持现有行为
- [x] AC-11：去重、失败、停止和绑定变更无双调用、串包或 pending 泄漏

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-26 | 用户确认 requirement、design 并授权开始实现 | 分支 `feature/105-qq-image-text-message-coalescing`；不执行真实 QQ/LLM 调用 |
| 2026-08-26 | 完成合并状态机、runtime 前置编排、pending reservation、生命周期清理与生产装配 | 首轮后端 lint、typecheck 和 41 个定向测试通过；继续补充边界回归后复验 |
| 2026-08-26 | 完成 QQ/C2C、附件、语音、GIF、runtime、onboard、assembly 与 migration 相关回归 | 172 passed；全部使用 fake/mock，无外部调用 |
| 2026-08-26 | 完成项目全量门禁 | backend：lint、581 文件 typecheck、2250 passed / 4 skipped；frontend：lint/guards/typecheck、626 passed、production build；Rust：cargo fmt、216 + 6 passed / 1 ignored、release check。仅有既有第三方退出清理、CSS/chunk 和 Rust dead-code warning |
| 2026-08-26 | 用户确认验收并要求合入 `main`、推送远端 | 总体状态更新为 `COMPLETED`，进入受治理的 Guide 复核与 Git 合入流程 |
| 2026-08-28 | 真实“长会话”QQ 事故确认一次五图发送被拆成五个 Agent 回合；用户澄清“不连续聚合”只指多次独立发送 | 分支 `fix/qq-logical-multimage-batching`；进入逻辑输入批次修复，不调用真实 LLM |
| 2026-08-28 | 完成逻辑输入批次、3 秒静默窗口、5 秒硬上限与长 Agent turn 锁拆分 | 首版受影响扩展回归 124 passed；随后进入真实 QQ 特征取证，不把未验证的 batch 候选字段视为完成证据 |
| 2026-08-28 | 完成首版项目全量门禁 | backend：lint、588 文件 typecheck、2282 passed / 4 skipped；frontend：lint/guards/typecheck、645 passed、production build；Rust：cargo fmt、216 + 6 passed / 1 ignored、release check。仅有既有第三方退出清理、CSS/chunk 和 Rust dead-code warning；当时尚未执行真实 QQ/LLM smoke |
| 2026-08-28 | 经用户授权完成真实 QQ 同发/单发对照 | 原事故一次选五图最大相邻间隔 1.518 秒；复验一次选五图最大间隔 0.505 秒；一次选两图跨度 0.522 秒；五次单发最小间隔 3.346 秒。真实行为分别为 1、1、1、5 个 Agent 回合 |
| 2026-08-28 | 排除平台 `timestamp`、消息 ID 和 `message_scene.ext` 作为批次号，删除未证实显式 batch 字段路径 | `message_scene.ext` 为逐消息 `msg_idx` 引用索引；最终批次依据只保留本机到达静默，真实时序固化为确定性回归 |
| 2026-08-28 | 完成收紧后的最终回归与全量门禁 | 受影响扩展回归 125 passed；backend：lint、588 文件 typecheck、2283 passed / 4 skipped；frontend：lint/guards/typecheck、645 passed、production build；Rust：cargo fmt、216 + 6 passed / 1 ignored、release check。总体状态更新为 `COMPLETED` |
| 2026-08-28 | 用户确认硬上限改为内部可选，QQ 生产默认关闭，并要求合入 `main`、推送远端 | 重新进入实现与验证；完成后按受治理的 Guide 复核和 Git 流程交付 |
| 2026-08-28 | 完成可选硬上限与最终门禁 | 定向扩展回归 126 passed；backend：lint、588 文件 typecheck、2284 passed / 4 skipped；frontend：lint/guards/typecheck、645 passed、production build；Rust：cargo fmt、216 + 6 passed / 1 ignored、release check。总体状态恢复为 `COMPLETED` |
