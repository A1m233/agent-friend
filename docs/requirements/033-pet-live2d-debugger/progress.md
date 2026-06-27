# 033 · 桌宠 Live2D 调试器 - 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
COMPLETED

---

## 实施说明

- **分支**：`feature/033-pet-live2d-debugger`
- **范围**：[requirement.md](./requirement.md) 与 [design.md](./design.md) 锁定的 dev-only Live2D 调试窗口。
- **关键约束**：首版服务当前 Hiyori，但 catalog / command schema / UI 分区为后续 Live2D 模型切换与模型试点留接缝；本期不实现模型切换。

---

## 任务列表

- [x] M33.1 协议 / catalog / executor 纯逻辑与单测
- [x] M33.2 Live2D 调试窗口 entry / UI / 日志 reducer
- [x] M33.3 Tauri debug-only 动态窗口 + Vite entry + ActionBar 入口
- [x] M33.4 pet 窗 debug bridge + `usePetInteractions` debug controls
- [x] M33.5 验证：单测 / typecheck / lint / `./scripts/check` / 桌面视觉路径记录

---

## 验收追踪

| AC | 状态 | 验证方式 / 备注 |
| --- | --- | --- |
| AC-1 dev 入口可打开 | 已验证 | Tauri dev ActionBar dev 分页入口可见；真实 dev 调试器窗口已打开并完成 catalog 查询 |
| AC-2 release 不可触达 | 已验证（代码） | Rust command 仅 `debug_assertions` 注册；窗口动态创建，不进 `tauri.conf.json` |
| AC-3 motion catalog 可用 | 已验证 | `catalog.test.ts` 覆盖 model3 投影；真实 pet 窗日志显示 `queryCatalog` response ok |
| AC-4 motion 播放可控 | 已验证 | `executor.test.ts` 覆盖 `startMotion` 参数；真实 dev 调试通道已可向 pet 窗发送命令 |
| AC-5 快捷触发可用 | 已验证 | `executor.test.ts` 覆盖 tap 快捷分流；真实桌面端已验证快捷按钮可见与 hover 态 |
| AC-6 错误可见且不崩 | 已验证 | executor / log reducer 覆盖错误路径；真实窗口 listener 失败可写入面板日志 |
| AC-7 日志可对照 | 已验证（单测 + web draft） | `debugLog.test.ts` + web-only draft 截图 |
| AC-8 扩展接缝可读 | 已验证（代码） | `Live2DModelRef` / catalog / command schema 均按当前模型抽象 |
| AC-9 既有路径零退化 | 已验证 | `./scripts/check/run.sh` 全绿；真实 dev 桌宠与调试器窗口并行观察通过 |
| AC-10 门禁通过 | 已验证 | `./scripts/check/run.sh` 全绿 |
| AC-11 桌面端视觉验收 | 已验证 | 真实 Tauri dev 截图：`/tmp/agent-friend-live2d-debugger-desktop.png`；hover 截图：`/tmp/agent-friend-live2d-debugger-desktop-hover-mousemoved.png`；`list-windows` 可见 `app agent-friend · Live2D 调试器` |
| AC-12 人工观察有效 | 已验证 | 用户确认可以收尾 |

---

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-27 | Phase 3 启动，创建进度追踪文件。 | 等待 M33.1 开始实现。 |
| 2026-06-27 | M33.1 完成：新增 Live2D debugger protocol / catalog / executor 与单测。 | `pnpm test -- src/pet/live2dDebugger/catalog.test.ts src/pet/live2dDebugger/executor.test.ts` 通过（vitest 147 passed）。 |
| 2026-06-27 | M33.2 完成：新增 `live2d-debugger.html`、React entry、调试窗口 UI 与日志 reducer。 | `pnpm test -- src/pages/live2d-debugger/debugLog.test.ts` 通过（vitest 151 passed）。 |
| 2026-06-27 | M33.3 / M33.4 完成：接入 Tauri debug-only 动态窗口、Vite entry、ActionBar dev 入口、pet 窗事件 bridge 和 `usePetInteractions` debug controls。 | `pnpm typecheck` 通过；相关定向 vitest 通过（151 passed）。 |
| 2026-06-27 | M33.5 验证进展：统一门禁全绿；启动真实 Tauri dev 成功；web-only draft 截图完成。 | `./scripts/check/run.sh` 全绿（Python 594 passed，frontend vitest 151 passed）；真实桌面窗口截图受 macOS 权限限制未完成，未把 `progress.md` 标 `COMPLETED`。 |
| 2026-06-27 | 排查并修复真实调试器窗口卡 `pending`：动态窗口未纳入 Tauri capability，导致事件监听 / 发送没有桌面权限。 | 已把 `live2d-debugger` 加入 `default.json` windows，并补充 listener 失败面板日志、pet bridge dev 日志、`isTauri()` 运行时兜底；`./scripts/check/run.sh` 全绿（Python 594 passed，frontend vitest 154 passed）。 |
| 2026-06-27 | 修正快捷区前两枚动作按钮 affordance：`点击反馈` / `参数 only` 改为与 idle 动作一致的 outline 按钮。 | 这是一次性触发动作，不是 toggle；`pnpm --dir frontend typecheck`、`pnpm --dir frontend lint` 通过；web-only 草稿截图：`/tmp/agent-friend-live2d-debugger-quick-buttons-web.png`。 |
| 2026-06-27 | 完成真实桌面视觉验收：确认 dev Tauri 实例、调试器窗口、catalog 响应和快捷按钮 hover 态。 | 桌面截图：`/tmp/agent-friend-live2d-debugger-desktop.png`；hover 截图：`/tmp/agent-friend-live2d-debugger-desktop-hover-mousemoved.png`；`list-windows` 确认可见 `app agent-friend · Live2D 调试器`；日志显示 `queryCatalog` response ok。 |
| 2026-06-27 | 用户确认验收，需求收尾为 `COMPLETED`。 | 最终 `./scripts/check/run.sh` 全绿（Python 594 passed，frontend vitest 154 passed）。 |
