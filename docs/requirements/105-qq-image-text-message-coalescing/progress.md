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

## AC 验证

- [x] AC-1：支持附件等待 3 秒，窗口内不触发 Agent；超时后只处理一次
- [x] AC-2：同绑定同私聊的首条纯文字在窗口内与附件合并并只处理一次
- [x] AC-3：合并事件在会话历史中只形成一条附件加文字用户消息
- [x] AC-4：普通纯文字消息保持立即处理
- [x] AC-5：不同 Bot、私聊或 session 严格隔离
- [x] AC-6：第二附件不累计、不续期，前后两条独立结算
- [x] AC-7：超时后到达的文字独立处理
- [x] AC-8：GIF、语音和不支持类型保持现有行为
- [x] AC-9：去重、失败、停止和绑定变更无双调用、串包或 pending 泄漏

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-26 | 用户确认 requirement、design 并授权开始实现 | 分支 `feature/105-qq-image-text-message-coalescing`；不执行真实 QQ/LLM 调用 |
| 2026-08-26 | 完成合并状态机、runtime 前置编排、pending reservation、生命周期清理与生产装配 | 首轮后端 lint、typecheck 和 41 个定向测试通过；继续补充边界回归后复验 |
| 2026-08-26 | 完成 QQ/C2C、附件、语音、GIF、runtime、onboard、assembly 与 migration 相关回归 | 172 passed；全部使用 fake/mock，无外部调用 |
| 2026-08-26 | 完成项目全量门禁 | backend：lint、581 文件 typecheck、2250 passed / 4 skipped；frontend：lint/guards/typecheck、626 passed、production build；Rust：cargo fmt、216 + 6 passed / 1 ignored、release check。仅有既有第三方退出清理、CSS/chunk 和 Rust dead-code warning |
| 2026-08-26 | 用户确认验收并要求合入 `main`、推送远端 | 总体状态更新为 `COMPLETED`，进入受治理的 Guide 复核与 Git 合入流程 |
