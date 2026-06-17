# 016 · 三端无统一文件日志，coding agent 事后排查无据可查

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-06-17
- **关联需求**：无

## 现象

agent / agent_bridge / 桌面端 Tauri / 桌面端 frontend 四个执行域各跑各的日志，且都**没有落到文件**：

- **agent_bridge**（`agent_bridge/src/agent_bridge/app.py:93` `_configure_logging`）：仅 `logging.basicConfig(level=..., format=...)`，**只到 stderr**；无 `FileHandler` / `RotatingFileHandler`
- **agent runtime**：各模块用 `logging.getLogger(__name__)`，共享上面这套 hook，同样无文件出口
- **桌面端 Tauri Rust**（`frontend/src-tauri/src/lib.rs:436-443`）：`tauri-plugin-log` 只在 `cfg!(debug_assertions)` 下挂，**prod build 完全无 logger**；dev build 走 plugin 默认 targets（`Stdout` + `Webview` + `LogDir`），写盘但只在 dev
- **桌面端 frontend JS**：全仓 9 处 `console.log/warn/error`，纯 console，未接 `@tauri-apps/plugin-log` 的 JS binding，console 调用不写盘

结果：任何跨进程问题（push channel 断流 / 主动事件触发链 / cursor feed 时序 / 跨屏 DPR 等）出 bug 后**只能靠记忆 + 复现**——dev 期 stderr 一关就丢、prod 干脆没有，coding agent 接手时无任何可读的事后证据。

## 根因初判

非 bug，是基础设施未建，孵化期"最小够用"的副作用：

- `_configure_logging` 只设 format / level 这种最薄一层，没人补 FileHandler
- Tauri 侧 plugin-log 的 setup 块裹在 `cfg!(debug_assertions)` 里——可能是早期"打包发行延后"决策（`0002` §4）的连带后果，prod 没人测就漏配
- frontend JS 没桥接到 Rust 的 plugin-log，可能 010 桌面端初版就没纳入

## 影响与判断

**影响范围**：全栈

- coding agent 排查问题时只能反复跑复现 → 对 race condition / 偶发问题基本无能为力
- 用户日常碰到 bug 时**没法附日志反馈**，只能描述现象
- 已知 issue 中 011 / 012 / 013 这类跨进程 / 跨屏 / 时序问题，基本都靠插临时 log 推断；有统一文件日志后这类 issue cycle time 能数量级下降
- 即将推进的 [`docs/explorations/pet-liveliness-and-proactive-events`](../../explorations/pet-liveliness-and-proactive-events/README.md) 主动事件链路（跨 agent → bridge → frontend → Live2D）涉及策略层节流 / 静默时段 / 多 source 优先级合并，**事后日志几乎是唯一调试手段**
- 用户已有 `backend-log-query` skill，说明"查后端日志"的动作模式已经在了，缺的就是日志本身

**为什么暂不修**：孵化期主线还在跑功能，本身没卡住功能交付，登记后择机推进。

## 可选修复方向

三端独立可做，任一先做都能解决"出了事没东西看"：

**方向 A · agent_bridge 加 RotatingFileHandler**

- `_configure_logging` 同时挂 stream + rotating file handler
- 文件路径走 `docs/decisions/0002` §3.19 用户数据目录约定的类比：macOS `~/Library/Logs/agent-friend/agent_bridge.log`，Win `%APPDATA%/agent-friend/logs/`
- 用 `platformdirs`（已是 §3.19 选用的路径解析库）拿日志目录
- rotation 默认每 10MB / 保留 5 份即可

**方向 B · Tauri Rust plugin-log 移出 `debug_assertions` 包裹**

- `tauri_plugin_log::Builder::default().level(...).build()` 的挂载移出 `cfg!(debug_assertions)`
- 显式 `.targets([LogDir, Stdout])`，prod 也写
- 默认 LogDir 跟方向 A 对齐到同一目录，便于打包

**方向 C · frontend JS 接 `@tauri-apps/plugin-log`**

- frontend 入口装一个 console patch：`console.error/warn` 同时 forward 到 `@tauri-apps/plugin-log` 的 error/warn，让 Rust 写盘
- 这样三端日志汇到同一目录，coding agent 一处查所有

A / B / C 相互独立，可拆三次小改落地。最大价值是**三件全做完后形成统一日志根目录**，配合现有 `backend-log-query` skill，coding agent 排查 cycle time 能数量级下降。
