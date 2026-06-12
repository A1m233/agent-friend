# 015 · 桌宠气泡与前端对话 Owner — 进度

## 总体状态
<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
IN_PROGRESS

> 8 个里程碑，依赖链：M15.1 → M15.2 → M15.4 → M15.5 → M15.6 → M15.8（M15.3 / M15.7 独立可插入）。M15.8 含 dev demo orchestrator + 自动门禁 + 手工真跑端到端验收。

---

## 里程碑与任务

> 任务粒度按"涉及文件 + 涉及单测"组织，便于实施时逐项打勾、断点续传。每项动到的文件以 [`design.md`](./design.md) §2 涉及文件清单为准。

### M15.1 PushEnvelope TS 类型 + Cargo 依赖准备

> 前置基础：让 Rust / TS 双端拿到协议契约，并把新增 native 依赖先 link 通过。

- [x] `frontend/src/types/push.ts`：新文件，定义 `PushEnvelope` TS interface（与 `agent_bridge/push/protocol.py` schema 对齐：`kind` / `session_id` / `seq` / `source_kind` / `events`）；events 字段用既有 `SessionEvent` 类型（`types/meta.ts`）
- [x] `frontend/src/types/index.ts`：导出 `PushEnvelope`
- [x] `frontend/src-tauri/Cargo.toml`：加 `reqwest = { version = "0.12", default-features = false, features = ["stream", "rustls-tls"] }` + `futures-util`；macOS-only target 段加 `objc2` + `objc2-app-kit`（避免污染 Win/Linux 构建；用 rustls-tls 避免 native OpenSSL）
- [x] `pnpm run typecheck`（即 `tsc -b --noEmit`）通过
- [x] `cargo build` 通过（首次拉 reqwest / objc2 / hyper-rustls，1m35s 全绿）

### M15.2 Rust push_subscriber 实现 + 解析单测

> 完成后能在 dev 模式下抓到 `agent_bridge` push channel 的事件、log 出来。

- [x] `frontend/src-tauri/src/push_subscriber.rs`：新文件
  - [x] `PushEnvelope` struct + `#[derive(Deserialize, Serialize, Clone, Debug)]`
  - [x] `find_frame_sep` 纯函数（SSE \n\n / \r\n\r\n 分帧）
  - [x] `parse_envelope_frame` 纯函数（取 data: 行拼 JSON 解码）
  - [x] `run_loop` async：reqwest `bytes_stream()` → 累积 buf → 按帧切 → 解码 → heartbeat 丢 → `emit_to("pet", "agent://push", env)`
  - [x] `spawn_push_subscriber(app, bridge_base_url)`：`tauri::async_runtime::spawn` task 包装
- [x] `frontend/src-tauri/src/lib.rs`：
  - [x] 顶部加 `mod push_subscriber;`
  - [x] 新增 `bridge_base_url()` helper（env `AGENT_FRIEND_BRIDGE_URL` 覆盖、默认 `http://127.0.0.1:18800`）
  - [x] `setup` 闭包内调 `push_subscriber::spawn_push_subscriber(app.handle(), bridge_base_url())`
- [x] `frontend/src-tauri/src/push_subscriber.rs` 内嵌 `#[cfg(test)] mod tests`（**12 个测试全绿**）：
  - [x] `find_frame_sep` 4 case（\n\n / \r\n\r\n / 仅一种 / 不完整无分隔）
  - [x] `parse_envelope_frame` 7 case（标准 frame + heartbeat null source_kind + 多行 data: 拼接 + event: 行被自然丢弃 + 空 frame / event-only / comment-only 三种 None / 非 JSON / events 含 UTF-8 透传）
  - [x] `PushEnvelope` JSON round-trip（serde 兼容 events 任意 dict）
- [x] `cargo test` 全绿（12 passed）
- [ ] 手工真跑 spot-check 留给 M15.8 端到端（dry-run 通过 dev demo orchestrator 一起做）

### M15.3 跨 Space + 全屏浮动（macOS）

> 独立可插入。让 pet 窗"用户看得见"语义成立——切 Space、全屏 app 之上都能浮动。

- [ ] `frontend/src-tauri/tauri.conf.json`：pet 窗加 `"visibleOnAllWorkspaces": true`
- [ ] `frontend/src-tauri/src/lib.rs`：
  - [ ] 新增 `apply_pet_window_level(app: &AppHandle) -> tauri::Result<()>`
  - [ ] `#[cfg(target_os = "macos")]` 分支：`objc2::msg_send![ns_window, setLevel: 1000_i64]`（NSScreenSaverWindowLevel，压过全屏 layer）
  - [ ] `#[cfg(not(target_os = "macos"))]` 分支：空实现
  - [ ] `setup` 闭包内调 `apply_pet_window_level(app.handle())?;`
- [ ] 手工真跑 spot-check（macOS）：
  - [ ] 切 Space 桌宠跟随
  - [ ] 启动全屏视频播放器（如 QuickTime）桌宠仍浮在视频之上 → 对应 **AC-7**

### M15.4 Policy 实现 + 单测

> 不依赖 M15.2 / M15.3 的运行环境，可纯单测驱动跑通。

- [ ] `frontend/src/stores/petBubblePolicy.ts`：新文件
  - [ ] `BubbleItem` type（`id` / `text` / `sourceKind`）
  - [ ] `PushPolicy` type alias（`(env: PushEnvelope) => BubbleItem | null`）
  - [ ] `ASSISTANT_TEXT_TYPES` 常量集合（含 `"assistant_message"`，留 set 是为了未来扩展 TextDone 等）
  - [ ] `defaultPolicy` 实现（按 design §5.2 规则）
- [ ] `frontend/src/stores/petBubblePolicy.test.ts`：新文件
  - [ ] `defaultPolicy: agent_turn + assistant_message → BubbleItem`（含 source_kind 透传）—— **AC-4**
  - [ ] `defaultPolicy: agent_turn + only memory_observation → null`（silent turn 丢弃）—— **AC-5**
  - [ ] `defaultPolicy: user_turn → null`（兜底丢弃）
  - [ ] `defaultPolicy: heartbeat → null`
  - [ ] `defaultPolicy: 多段 assistant_message → 用 \n\n 拼接`（防御 turn 内多 message）
- [ ] `pnpm test petBubblePolicy` 全绿

### M15.5 Store 实现 + 单测

> 完成后能用 `usePetBubbleStore.ingest(envelope)` 驱动状态机走完完整生命周期。

- [ ] `frontend/src/stores/petBubble.ts`：新文件
  - [ ] `BubblePhase` type
  - [ ] `usePetBubbleStore` zustand store（`phase` / `current` / `ingest` / `expand` / `dismiss`）
  - [ ] 内部 `dismissTimer` + `AUTO_DISMISS_MS = 10_000`
  - [ ] `setPolicy(p)` / `resetPolicy()` 测试钩子
  - [ ] `startPetBubbleSubscriber()` async（listen `agent://push` → `ingest`）
- [ ] `frontend/src/stores/petBubble.test.ts`：新文件（用 `vi.useFakeTimers()` 控时）
  - [ ] `ingest → showing`（phase / current 字段断言）
  - [ ] `ingest → showing → +10s → idle`（fakeTimers advance）
  - [ ] `setPolicy(替换 policy) → ingest user_turn 也能进 bubble`（AC-3 policy 可替换扩展点证明）—— **AC-3**
  - [ ] `ingest 新 envelope（seq+1）替换 current`（同时清旧 timer）
  - [ ] `ingest 同 id 不替换`（兜底）
  - [ ] `expand → expanded`（清 timer，自动 dismiss 不触发）
  - [ ] `dismiss → idle`
  - [ ] `expanded 状态 +10s 不会自动 idle`
- [ ] `pnpm test petBubble` 全绿

### M15.6 PetBubble UI 组件 + 集成到 pet/App.tsx

> 完成后 pet 窗能真实渲染气泡 UI；起 frontend dev 能看到 mock envelope 的气泡。

- [ ] `frontend/src/components/pet/PetBubble.tsx`：新文件（按 design §6.1 实现）
  - [ ] 订阅 `usePetBubbleStore`
  - [ ] `useEffect` + `getBoundingClientRect` 翻转判定（贴顶时改下方显示）
  - [ ] 截断逻辑（`TRUNCATE_LEN = 120`）+ 点击 expand
  - [ ] expanded 状态显示完整文本 + 关闭按钮
  - [ ] `data-hit` 标记 + tailwind animate-in fade-in
- [ ] `frontend/src/components/pet/PetBubble.test.tsx`：新文件（用 `@testing-library/react`）
  - [ ] `phase=idle` 不渲染（DOM null）
  - [ ] `phase=showing` 渲染文本
  - [ ] 长文本截断 + "点击展开" 提示
  - [ ] 点击截断状态触发 `expand`
  - [ ] `expanded` 渲染全文 + 关闭按钮
- [ ] `frontend/src/pages/pet/App.tsx`：
  - [ ] 外层 div 加 `relative` 让 absolute 定位有 anchor
  - [ ] 插入 `<PetBubble />` 在 pet-stage 同级
  - [ ] `useEffect` 调 `startPetBubbleSubscriber()` + unlisten 清理
- [ ] `pnpm test PetBubble` 全绿
- [ ] 手工 dev spike：在 pet 窗 console 调用 `__TAURI__.event.emit("agent://push", mockEnv)`（或测试钩子）能看到气泡冒出

### M15.7 sessionProjection 防御（注释 + 兜底测试）

> 独立可插入。被动跳过已天然成立，本期只做"显式声明 + 兜底测试"。

- [ ] `frontend/src/stores/sessionProjection.ts`：顶注释补一段说明 `system_trigger` / `memory_observation` 被动跳过的语义（design §7.2 文案）
- [ ] `frontend/src/stores/sessionProjection.test.ts`：新增 1 条 case
  - [ ] `it("ignores system_trigger / memory_observation events (015 R-4.5.1)")`：含两种 type 事件的 session 投影出的 ChatMessage 序列与不含时 `toEqual` —— **AC-6**
- [ ] `pnpm test sessionProjection` 全绿

### M15.8 Dev CLI demo + 自动门禁 + 手工真跑

> 端到端验收：bedtime / idle reflection / 既有 chat 对话 / 跨 Space / 全屏浮动五件事全跑通。

- [ ] `scripts/dev-pet-bubble-demo/run.sh`：双端 wrapper（参考 014 `scripts/dev-fire-source/run.sh` 模板）
- [ ] `scripts/dev-pet-bubble-demo/run.ps1`：Windows 等价
- [ ] `scripts/README.md`：登记新脚本（"015 端到端 demo / 触发 bedtime + idle_reflection / 需 bridge dev_mode=true"）
- [ ] **自动门禁**：`./scripts/check` 全绿（lint + typecheck + cargo test + vitest）—— **AC-8**
- [ ] **手工真跑端到端**（按用户授权 `llm-api-confirm` 走真 LLM）：
  - [ ] 启 bridge（`scripts/bridge/run.sh`，dev_mode=true）
  - [ ] 启 frontend（`pnpm tauri dev` 或对应脚本）
  - [ ] 在 chat 窗发一条 user 消息 → MessageList 正常流式渲染 + pet 气泡**不跟随** → 对应 **AC-1 / AC-8**
  - [ ] 跑 `./scripts/dev-pet-bubble-demo/run.sh`
    - [ ] 触发 bedtime → pet 气泡冒出 + chat 窗 MessageList **无该消息** + 翻 session JSONL 能找到 `system_trigger` 事件 → 对应 **AC-4**
    - [ ] 触发 idle_reflection → pet 气泡**不冒** + chat 窗无反应 + bridge log 能看到 `memory.observe` 被调用 → 对应 **AC-5**
  - [ ] 切 Space 桌宠跟随 + 启动全屏视频桌宠仍可见 → 对应 **AC-7**
  - [ ] 切到一个历史会话再切回（chat 窗）→ MessageList 正常投影（含 user 触发轮、不含主动轮）→ 兜底 **AC-6**
- [ ] 更新 `progress.md` 总体状态 → `COMPLETED`，补完结日志

---

## 实施日志

| 日期 | 完成 | 备注 |
|------|------|------|
| 2026-06-13 | M15.1 完成 | types/push.ts + Cargo.toml 依赖；rustls-tls 避免 OpenSSL；macOS-only objc2 + objc2-app-kit；typecheck + cargo build 全绿 |
| 2026-06-13 | M15.2 完成 | push_subscriber.rs 落（SSE 分帧 / envelope 解码 / reqwest bytes_stream / emit_to 广播）+ lib.rs 接入 setup spawn；12 个单测全绿；spot-check 端到端 dry-run 移交 M15.8 |

---

## 验收锚点对照

| AC | 覆盖里程碑 | 测试形式 |
|----|---------|---------|
| AC-1 Owner 抽象可订阅可分发 | M15.5 + M15.8 | Store 单测 + 手工真跑 |
| AC-2 双订阅去重（思路 A 下零去重） | M15.2 + M15.4 | Rust 订阅 `kinds=agent_turn` + Policy 测试 |
| AC-3 Policy 可替换 | M15.5 | `setPolicy` 替换证明 |
| AC-4 主动轮分流端到端 | M15.4 + M15.8 | Policy 单测 + 手工真跑 |
| AC-5 Silent turn 前端丢弃 | M15.4 + M15.8 | Policy 单测 + 手工真跑 |
| AC-6 sessionProjection 兼容 | M15.7 + M15.8 | 兜底单测 + 手工真跑 |
| AC-7 跨 Space / 全屏浮动 | M15.3 | 手工真跑（macOS） |
| AC-8 既有路径零退化 | M15.8 | `./scripts/check` 全绿 + chat 对话手跑 |
| AC-9 Dev CLI 端到端 | M15.8 | demo orchestrator 全流程 |
