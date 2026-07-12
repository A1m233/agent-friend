# 045 · AgentRuntime 统一执行入口与调度生命周期 · 进度

## 总体状态

COMPLETED

## 任务列表

- [x] M45.1：新增 Run model、CancellationToken 与 per-session Scheduler；覆盖串行、并发、优先级、去重、过期和容量测试。
- [x] M45.2：统一 Runtime execute/terminal/PostTurn；Conversation 增加 cooperative cancellation、run_id 关联与 listener 背压修复。
- [x] M45.3：AG-UI 普通发送与 edit-resend 迁移到 RunHandle，移除 user-turn 镜像和生产 session 双锁。
- [x] M45.4：OpenAI streaming/non-streaming、voice 与 IM 迁移到 RunHandle，保留协议和低延迟参数。
- [x] M45.5：补齐 shutdown、入口集成与回归测试，逐项核对 AC，运行 `scripts/check`。
- [x] 用户完成最终验收。

## 实现日志

| 日期 | 完成内容 | 备注 |
|---|---|---|
| 2026-07-12 | Phase 3 启动；基线已同步至 `ed93257`，开始 M45.1。 | 不调用真实 LLM；先建立可断言的调度地基。 |
| 2026-07-12 | 完成 M45.1：RunRequest/RunHandle、CancellationToken、per-session mailbox、有界 worker、priority/dedupe/expiry/capacity。 | Scheduler 与取消测试通过。 |
| 2026-07-12 | 完成 M45.2：Runtime submit/唯一终态/PostTurn outcome、Conversation 安全取消点与 `run_id` meta、listener overflow 断开。 | Runtime/Conversation/Listener 回归通过。 |
| 2026-07-12 | 完成 M45.3：AG-UI route/encoder 支持 RunHandle，生产路径不再镜像 user turn 或持有 bridge session 双锁。 | AG-UI→Runtime→push 生命周期测试通过。 |
| 2026-07-12 | 完成 M45.4：OpenAI streaming/non-streaming、voice 参数与 IM 聚合路径接入 RunHandle；request-local error callback 保留 run_error 落盘。 | 43 项聚焦测试 + strict typecheck 通过。 |
| 2026-07-12 | 完成 M45.5：补 shutdown non-clean、consumer abandoned、listener overflow、入口回归与 AC 核对；收尾审查修复 mailbox 满时 interactive admission 顺序。 | `scripts/check` 全绿：767 项后端测试；前端 53 files / 295 tests；lint、mypy、tsc 与 guards 全过。 |
| 2026-07-12 | 用户确认验收通过，需求状态改为 COMPLETED。 | 准备提交并合入 main。 |

## AC 验证记录

| AC | 结果 | 验证 |
|---|---|---|
| AC-1 入口统一 | ✅ 自动化 + 代码路径 | AG-UI 生产生命周期测试；OpenAI streaming/non-streaming 与 IM 回归；生产装配均注入 `agent_runtime`。 |
| AC-2 同 Session 串行 | ✅ 自动化 | barrier scheduler 测试断言同 session 最大 active=1。 |
| AC-3 跨 Session 并发 | ✅ 自动化 | 两 session barrier 同时 active。 |
| AC-4 用户优先 | ✅ 自动化 | interactive 取消 queued/active background，安全边界让出 session。 |
| AC-5 过期与合并 | ✅ 自动化 | expired、dedupe latest 与 queue outcome 测试。 |
| AC-6 排队取消 | ✅ 自动化 | token/scheduler queued terminal 无 Conversation 副作用。 |
| AC-7 在途取消 | ✅ 自动化 | Conversation partial cancel + encoder consumer abandoned 传播。 |
| AC-8 异常收尾 | ✅ 自动化 | executor failure 仅一 terminal，PostTurn 收到 failed；listener terminal 清理。 |
| AC-9 慢订阅者隔离 | ✅ 自动化 | queue overflow unregister，不创建等待 `queue.put` Future。 |
| AC-10 Voice 流式与延迟 | ✅ 离线机制 | OpenAI stream/voice 参数与既有 latency trace 回归通过；本轮未调用真实 LLM/voice。 |
| AC-11 安全关闭 | ✅ 自动化 | 不可中断 worker 超时返回 non-clean，worker 返回后 clean；stop 后取消 token。 |
| AC-12 全量门禁 | ✅ | `scripts/check` 全绿；等待用户最终验收确认。 |
