# 062 · 桌面多会话并发运行 · 实现进度

## 总体状态

COMPLETED

## 当前基线

- 分支：`feature/062-desktop-multi-session-concurrent-runs`
- 基线：`7dcf0e7c2d7e5215db1159d13153f44d023520ed`
- 并行需求：`feature/063-context-compaction-reliability`（独立 worktree；不修改其 context/checkpoint 领域）

## 任务列表

### M1 · Runtime 控制面

- [x] 定义 `RunSnapshot`、consumer abandon policy、interactive busy policy 与稳定取消结果
- [x] 实现 queued/running/terminal 生命周期版本和 listener 发布
- [x] 实现 active/recent terminal 查询、TTL/容量清理与精确取消
- [x] 实现 desktop AG-UI consumer detach，保留其他入口 cancel 语义
- [x] 补齐 Runtime 状态机、并发、取消、detach 和容量测试

### M2 · Bridge、push 与 Tauri

- [x] 新增 run list/get/cancel REST API 和稳定错误映射
- [x] 新增 `run_changed` push envelope 与订阅过滤
- [x] AG-UI route 接入 desktop policy、busy admission 和延迟附件消费
- [x] Session detail 返回 durable revision，并补 mutation guard
- [x] Tauri 订阅并向 chat WebView 转发 `session://run-changed`
- [x] 补齐 Bridge/push/Rust 集成测试

### M3 · 前端多会话运行

- [x] 新增 run API、类型和 snapshot reducer
- [x] conversation store 改为 session-indexed view/run state
- [x] transport registry 按 run 管理，导航不再 abort
- [x] 实现精确停止、后台流更新和 terminal 最终校准
- [x] session sync 支持 run/session 双通道与重连 pull 校准
- [x] 补齐跨 session、乱序、断线和缓存清理测试

### M4 · 统一草稿与侧栏

- [x] 新增 home/session draft workspace、可扩展 asset 和 submission 模型
- [x] Composer 文本、附件上传、兼容性和语音 trace 按 draft workspace 归属
- [x] 实现 home claim、admission restore 与临时附件 release
- [x] 侧栏展示 queued/running/completed unread/failed
- [x] 前端归档、模型、编辑重发等操作按 session busy 防护，服务端统一保护 destructive mutation
- [x] 补齐草稿隔离、异步写回、submission 和侧栏测试

### M5 · 验证与收口

- [x] 运行受影响模块自动化测试
- [x] 运行 `scripts/check` 全量门禁
- [x] 在用户授权接管 dev runtime 后完成真实 Tauri / WebView2 CDP 验收
- [x] 逐项回填 AC-1～AC-16 证据
- [x] 完成实现后静态审查并修正断线 busy、external run detail 合并和 Scheduler 索引清理问题
- [x] 用户明确确认验收后，将总体状态改为 `COMPLETED`

## 实现日志

| 日期 | 完成内容 | 备注 |
|---|---|---|
| 2026-07-25 | 进入 Phase 3；确认 062/063 使用独立分支与 worktree；建立实现任务清单 | 真实桌面验收将在 M5 按端口、数据目录和构建目录隔离条件决定执行方式 |
| 2026-07-25 | 完成 Runtime/Bridge/Tauri 控制面、session-indexed 前端、统一草稿与侧栏状态；定向 Python 55 项、Frontend 40 项、Rust 139 项通过 | Rust 另有 1 项既有设备 smoke ignored；尚未跑全量 `scripts/check` 与真实桌面验收 |
| 2026-07-25 | 静态审查补齐 external active run 与 detail 合并、断线时保留本地 busy、重连缺失 run 校准、Scheduler terminal 索引清理 | 063 当前只改 context/conversation/llm provider 域，与 062 当前文件列表无重叠 |
| 2026-07-25 | `scripts/check` 全绿：Python 1018 passed/2 skipped，Frontend 431 passed，Rust 139 passed/1 ignored，生产构建与 release check 通过 | 既有 bundle size 与 Rust dead-code/unused-import warning 不阻断门禁 |
| 2026-07-25 | 用户确认另一会话不需要桌面验收并授权停止其运行态；核对并停止 `798e` Tauri 进程树后，通过 `scripts/dev/run.ps1 takeover --tauri` 启动本分支 | Vite 1420、CDP 9222、Bridge 18800、Voice 18900 均由 `84f7` worktree 提供；desktop-visual doctor、真实对话 WebView DOM 与截图可用 |
| 2026-07-25 | 完成 AC-15 真实桌面验收，共触发 5 个短文本 run | A2/B2 同时为 running；A3 精确停止得到 cancelled/explicit_request 时 B3 仍 running，随后 B3 completed；A/B/Home 文本草稿隔离，A 的 TXT/PNG 临时附件为 success 且切走/切回恢复 |
| 2026-07-25 | 桌面验收发现 Home 首条提交后可能被 TDesign 同轮 change/input 回放重新写回；在 Composer 增加 submitted-value echo guard | 修复后 `scripts/check` 再次全绿：Python 1018 passed/2 skipped，Frontend 431 passed，Rust 139 passed/1 ignored，生产构建与 release check 通过 |
| 2026-07-25 | 用户确认验收通过，需求进入合入收口 | AC-1～AC-16、项目门禁、真实桌面验收与用户验收四门齐备，总体状态更新为 `COMPLETED` |

## 验收证据

| AC | 状态 | 证据 |
|---|---|---|
| AC-1～AC-4 | 自动化通过 | `test_runtime_scheduler.py` 的跨 session 并发/同 session busy；`conversation.test.ts` 的 A/B workspace、迟到收尾与 active detail 合并 |
| AC-5～AC-6 | 自动化通过 | Runtime 精确 cancel/result/snapshot 生命周期；Bridge run list/get/cancel；Frontend exact stop、queued/running reducer |
| AC-7～AC-8 | 自动化通过 | `run_changed` Push/Tauri 解析与 sessionSync；后台 unread indicator；DETACH 无背压；transport 断线保持 busy 与 reconnect pull 校准 |
| AC-9～AC-10 | 自动化通过 | `drafts.test.ts` 覆盖 session 隔离、异步目标写回、home claim、submission reject merge；Composer 按 workspace 管理文本/附件/validation/voice trace |
| AC-11～AC-12 | 自动化通过 | external active run 保持 desktop busy；control scope 阻止跨 channel stop；Runtime mutation reservation 与 Bridge archive/delete/persona/channel busy 测试 |
| AC-13～AC-14 | 自动化通过 | recent terminal TTL/512、Scheduler terminal 索引清理、Frontend run 512/view LRU 20；revision/requestSeq、push/pull/导航乱序测试 |
| AC-15 | 真实桌面通过 | 当前分支 Tauri + WebView2 CDP：A2 `49a7186d-...` 与 B2 `8725e55f-...` 同时 running；A3 `6b77c5b7-...` 精确停止为 cancelled/explicit_request 时 B3 `e9e20457-...` 仍 running，之后独立 completed 并显示 `B3-DONE`；A/B/Home 文本草稿分别恢复，A 的临时附件列表为 success、B/Home 为 empty、切回 A 后仍恢复。截图：`.codex-test-tmp/062-desktop/concurrent-a-b.png`、`background-complete.png`、`attachment-restored.png` |
| AC-16 | 通过 | `scripts/check` 全绿：Python 1018 passed/2 skipped，Frontend 431 passed，Rust 139 passed/1 ignored，构建通过 |
