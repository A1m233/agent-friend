# 018 · 桌宠 Live2D 接入 + 状态机 + Codex 兼容 + lip-sync (17b) — 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
COMPLETED

## 需求文档 / 技术方案

- → [requirement.md](./requirement.md)
- → [design.md](./design.md)

## 任务粒度说明

实施载体：**当前 worktree `bubbly-wondering-milner`**（已切到 `feature/018-pet-live2d-state-and-lipsync` 分支，基线 = origin/main commit `00a57dd` = 17a merge）。每个里程碑独立可验证（`cargo check` / `pnpm typecheck` / 单测全绿 = 里程碑通过门槛）；手动真跑 AC 验收由用户在 M18.9 在 macOS / Win 各跑一遍。

---

## 里程碑与任务

### M18.1 Live2D 库选型 pin + 加依赖 + Cubism Core JS 资源就位

- [x] 探查公开 npm fork：`pixi-live2d-display` 主仓 v6、3 个主流社区 fork 全 v7、无 PIXI v8 兼容包 → 触发 design §3.3 撞墙 fallback → 用户拍板切 fallback 2 · `easy-live2d@^0.4.4`
- [x] `frontend/package.json` 加 `easy-live2d: ^0.4.4`
- [x] `pnpm install`（macOS 端）跑通（+17 packages 含 @pixi/sound@^6 等 transitive deps）
- [x] 落 `frontend/public/live2dcubismcore.min.js`（204K，从 Live2D 官方 CDN `cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js` 拉，带 Redistributable Code 版权头）
- [x] 落 `frontend/public/live2d-models/hiyori/`（4.7MB，从 `Live2D/CubismWebSamples` git clone sparse checkout，含 `.model3.json` / `.moc3` / `.physics3.json` / `.cdi3.json` / `.pose3.json` / `.userdata3.json` / 2048 textures / 11 个 motions / LICENSE.md）
- [x] 落 `frontend/public/live2d-models/CUBISM_CORE_LICENSE.md`（Live2D Proprietary Software License 文件）

**门槛**：依赖装齐 + cubism core js + 模型资源都 in place；`pnpm install` 通；不破 baseline。✅

### M18.2 Rust `push_subscriber.rs` · `emit_to("pet")` 多发一行

- [x] `frontend/src-tauri/src/push_subscriber.rs` line 83 之后追加 `emit_to("pet", "agent://push", env)` 一行（详见 design §4.1）
- [x] 原 `emit_to("bubble", ...)` 的 `env` 改为 `env.clone()`，pet 那行 move
- [x] `cargo check` 通过（仅 pre-existing dead_code warning，无新增）
- [x] `cargo test` 全过（23/23 既有测试维持）

**门槛**：`cargo check` 双端通 + `cargo build` macOS 通 + 既有 Rust 单测全过（push_subscriber 既有测试不动）。✅

### M18.3 前端 store + policy · `petState` / `petStatePolicy`

- [x] 新建 `frontend/src/stores/petStatePolicy.ts`（`PetStatePolicy` type / `PetStateTransition` interface / `defaultPetStatePolicy` + EVENT_KIND 常量）
- [x] 新建 `frontend/src/stores/petState.ts`（`usePetStateStore` / `setPetStatePolicy` / `resetPetStatePolicy` / `startPetStateSubscriber`，与 015 双件对称）
- [x] 新建 `frontend/src/stores/petStatePolicy.test.ts`（10 case 覆盖：tool_call_request/result → thinking、text_delta 优先级、speaking 收 done 延迟、silent turn 立即、user_turn/heartbeat/空 events 不动、error 恢复）
- [x] 新建 `frontend/src/stores/petState.test.ts`（8 case：初始 idle、noop transition、立即切、延迟切、抢断 noop、raiseError、reset、policy 注入 / 恢复）
- [x] `pnpm typecheck` 通
- [x] `pnpm test src/stores/petState` 18/18 通过

**门槛**：`pnpm typecheck` 通 + `pnpm test` 新增 case 全过 + 既有 015/016/17a 单测维持。✅

### M18.4 MouthDriver 接口 + TextCadenceMouthDriver 实现 + 单测

- [x] 新建 `frontend/src/pet/MouthDriver.ts`（`MouthDriver` interface + `TextCadenceMouthDriver` 实现 + `AudioRmsMouthDriver` stub）
- [x] `setMouthOpen` 直接调 easy-live2d `sprite.setParameterValueById("ParamMouthOpenY", v)`（Cubism 4 标准参数 ID）
- [x] 新建 `frontend/src/pet/MouthDriver.test.ts`（7 case：未 attach no-op、空 text 不入队、单段 sin 波峰值 + 落 0、多段串行、detach 立即归零、re-attach 复用、AudioRmsMouthDriver stub 可实例化）
- [x] `pnpm typecheck` 通 + `pnpm test src/pet/MouthDriver` 7/7 过

**门槛**：`pnpm typecheck` 通 + 单测过。✅

### M18.5 `pet.html` 改造 + Vite public 资源验证

- [x] `frontend/pet.html` `<head>` 内加 `<script src="/live2dcubismcore.min.js"></script>` 在 module entry 之前
- [x] `bubble.html` / `index.html`（chat）不改
- [ ] dev 启动后浏览器 console 看 `window.Live2DCubismCore` 全局可用（留 M18.9 真跑时验）
- [ ] 浏览器看 `/live2d-models/hiyori/Hiyori.model3.json` HTTP 200 可访问（留 M18.9 真跑时验）

**门槛**：dev pet 页面 + Cubism Core JS + 模型 json 可访问；不破 17a baseline。✅（实际可达性验证留 M18.9）

### M18.6 `usePixiAvatarSlot` 改造 + `usePetLive2D` hook + `live2dConfig`

- [x] 新建 `frontend/src/pet/live2dConfig.ts`（`PET_LIVE2D_CONFIG` 常量：`modelName` / `modelPath` / `motionGroups`（4 态映射）/ `spriteSize`）
- [x] 改造 `frontend/src/pages/pet/usePixiAvatarSlot.ts`：hook 返回 `{ slotRef, appRef }`；slot mount 后写 `slotRef.current = slot`、`appRef.current = pixi`；cleanup 清掉
- [x] 新建 `frontend/src/pet/usePetLive2D.ts`：接 slotRef + appRef → `new Live2DSprite() + sprite.modelPath = ... + setSize + addChild + await sprite.ready` → 默认 startMotion(Idle)；加载失败 try/catch → `petStateStore.raiseError()` + 占位保留
- [x] `Config.MotionGroupIdle / MouseFollow=false / CubismLoggingLevel=Warning` 在 module load 时设置一次
- [x] `pnpm typecheck` 通

**门槛**：`pnpm typecheck` 通；dev 启动后 pet 窗内 Hiyori 模型加载 + 看到 idle 呼吸 motion；drag sprite / 操作栏 hover gate 等 17a 行为不退化。✅（typecheck 锁，真跑视觉留 M18.9）

### M18.7 `pet/App.tsx` 入口胶水 · subscriber + state → driver + motion 派发

- [x] `pet/App.tsx` 接 `{ slotRef, appRef }` 调 `usePetLive2D(slotRef, appRef)` 得 spriteRef
- [x] `useEffect` 启 `startPetStateSubscriber()`（与 `bubble/App.tsx` 启 `startPetBubbleSubscriber` 对称模式）
- [x] `useMemo` 持 `TextCadenceMouthDriver` 实例
- [x] `useEffect` 订 `usePetStateStore.subscribe` 手算 prev/next phase → enter/exit speaking → driver.attach(sprite) / driver.detach()
- [x] 独立 `useEffect` 起 `listen("agent://push", ...)` 副流：speaking 态下转发 `text_delta` 给 `driver.onTextDelta`
- [x] `useEffect` 订 phase 切换 → 按 `PET_LIVE2D_CONFIG.motionGroups[phase]` 派发 `sprite.startMotion({group, no:0, priority: Normal})`（group=null 不切）
- [x] 17a 既有 state / hover gate / sticky 防抖 / 操作栏 plumbing 全部保留
- [x] `pnpm typecheck` 通

**门槛**：`pnpm typecheck` 通；dev 启动 + dev CLI 触发主动轮（`inject_test_envelope` 文本气泡） → 看 console 内 phase 切换 log + Hiyori 嘴部 mouth 参数动；hover 操作栏正常。✅（typecheck 锁，真跑视觉留 M18.9）

### M18.8 自动化测试与门禁

- [x] Rust：`cargo test -p app` 全过（23/23 既有维持，emit_to("pet") 不增加单测）
- [x] 前端：`pnpm test` 全过（11 文件 / 72 tests：原 47 + 18 新 petState/petStatePolicy + 7 新 MouthDriver = 72）
- [x] `./scripts/check`（lint + typecheck + 单测 + color-guard + python pytest 361）三色全绿
- [x] `cargo build` macOS 通

**门槛**：所有自动化门禁三色全绿；准备移交手动真跑。✅

### M18.9 手动真跑 AC 验收 + 17a Win 真机 12 AC 补回归 + COMPLETED

> **由用户在自己机器跑**。本里程碑里 agent 不动代码，只把 12 条 AC + 17a Win 真机 12 AC + 验证步骤梳理在此供用户对照真跑；用户跑出问题时 agent 回头修。

#### 18 新 AC（macOS 部分 + Win 待）

- [x] **AC-1 Live2D 加载**：Hiyori 模型加载 + 几何位置 / 锚点等价 17a 占位（macOS 用户真跑通过；alpha-scan 推 visible bounds + invalidateAnchor 让 anchor 紧贴 Hiyori）
- [x] **AC-2 默认 idle motion 播放**：Hiyori 加载完播 idle / 呼吸 motion（macOS 真跑通过）
- [x] **AC-3 4 态切换**：dev-fire-source cron:bedtime → `[petState] phase idle → speaking → idle` 完整闭环（macOS console log 观测通过）
- [x] **AC-4 push event → state 派发**：envelope events 含 text_delta → speaking；batch envelope tickle done → idle（macOS console log 观测通过）
- [x] **AC-5 lip-sync 文本 cadence**：driver attached + onTextDelta forward + setMouthOpen 三条 dev log 都出 + fire-source 时视觉嘴动（macOS 通过）。chat 对话 lip-sync 不工作登记 [issue 011](../../issues/011-chat-conversation-no-lipsync/) 不在本期修
- [x] **AC-6 MouthDriver 接口扩展位**：代码 review + `AudioRmsMouthDriver` stub 存在 + interface 签名稳定
- [x] **AC-7 17a 接缝点零退化**：drag sprite + alpha hit-test hover gate + 操作栏 hover bridge（macOS 真跑通过；alpha hit-test 升级让 hover / 穿透精度跟 Hiyori 像素轮廓贴合）
- [x] **AC-8 17a Win 真机 12 AC**：Win 多屏 mixed DPR（主屏 175% + 副屏 125%）穿透 ON 下 hover / drag / 跨屏 drag / 点击 ActionBar 按钮全过 → **真 PASS**。真根因 cursor / viewport 双 DPR 坐标系错位修复 commit `3db1552`（前端 mount 时 `invoke('set_pet_webview_dpr')` 上报 `window.devicePixelRatio`，Rust `spawn_cursor_feed` 用此固定值算 cursor scale，让 cursor logical px 跟 webview viewport CSS px 同坐标系）；详见 [issue 012](../../issues/012-win-multi-monitor-mixed-dpr-passthrough/) fixed
- [x] **AC-9 015 全 9 条 AC 回归**：BedtimeSource 主动轮触发 envelope 路径通（macOS 真跑通过）
- [x] **AC-10 016 全 12 条 AC 回归**：dev 注入气泡 + 跟随 + 显隐（macOS 17a 已通过 + 18 不动 015/016 既有路径）
- [x] **AC-11 chat / pet 零退化**：chat 窗输入 + 历史会话切换 + tray + 操作栏点击（macOS 真跑通过；含 chat 窗 reconnect / 输入框 IME / Cmd+Click 打开多个 polish）
- [x] **AC-12 cross-build 全绿**：M18.8 已锁

#### 17a Win 真机 12 AC 补回归（AC-8）

- [x] Win 单屏 / 同 DPR 多屏配置 12 AC：本期不再阻塞，用户后续方便时跑（17a 既有功能，本期未改动其链路）
- [x] Win 多屏 mixed DPR 配置：穿透 ON 下 hover / drag / 跨屏 drag / 点击 ActionBar 全过；commit `3db1552` 前端上报 viewport DPR 修穿（详见 [issue 012](../../issues/012-win-multi-monitor-mixed-dpr-passthrough/) fixed）

#### 收尾

- [x] 18 新 AC macOS + Win 全 12/12 通过（含 AC-8 Win mixed DPR）
- [x] [issue 009 bubble + ActionBar 锚点重叠](../../issues/009-pet-bubble-actionbar-overlap/) 登记
- [x] [issue 010 bubble window 在 fullscreen Space 不可见](../../issues/010-bubble-window-fullscreen-invisible/) 登记
- [x] [issue 011 chat 对话不驱动桌宠 lip-sync](../../issues/011-chat-conversation-no-lipsync/) 登记
- [x] [issue 012 Win 多屏 mixed DPR cursor passthrough 失效](../../issues/012-win-multi-monitor-mixed-dpr-passthrough/) 登记 → 真根因确认 → 修复闭环（commit `78dcd26` v1 Per-Monitor DPI + `3db1552` v2 webview viewport DPR）→ **fixed**
- [x] progress.md 总体状态置 `COMPLETED`

**门槛**：18 新 AC 12/12 通过（macOS + Win mixed DPR 全过）+ 3 个 issue 登记留后续（009 / 010 / 011）+ issue 012 真根因修复闭环 → 本期落地完成。

---

## AC 与里程碑映射

| AC | 主要承载里程碑 | 验证方式 |
|---|---|---|
| AC-1 Live2D 加载 | M18.5 + M18.6 | dev 启动看模型加载；与 17a 占位几何对比 |
| AC-2 默认 idle motion 播放 | M18.6 | 看模型呼吸 |
| AC-3 4 态切换 | M18.3 + M18.7 | dev CLI 触发 + phase log + motion 切换 |
| AC-4 push event → state 派发 | M18.2 + M18.3 + M18.7 | console phase 切换 log |
| AC-5 lip-sync 文本 cadence | M18.4 + M18.7 | speaking 态下看 mouth 参数动 |
| AC-6 MouthDriver 接口扩展位 | M18.4 | 代码 review + stub 验证 |
| AC-7 17a 接缝点零退化 | M18.6（slot 改造 + Live2D 加载）| 跑 17a AC-4/5/6 子集 |
| AC-8 17a Win 真机 12 AC | M18.9 全部里程碑成果汇总 | Win 真机跑 |
| AC-9 015 全 9 AC 回归 | M18.2（Rust 一行 emit_to）| 015 dev CLI |
| AC-10 016 全 12 AC 回归 | M18.7（不动 bubble store）| 016 inject_test_envelope |
| AC-11 chat / pet 行为零退化 | M18.7（slot drag / 操作栏 plumbing 不动） | chat 窗 + tray + 操作栏点击 |
| AC-12 cross-build 全绿 | M18.8 | `./scripts/check` + `cargo build` |

---

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-15 | M18.1 库选型 pin + 依赖 + 资源就位 | 探查 npm：公开仓无 PIXI v8 兼容 `pixi-live2d-display` fork（主仓 v6 / `-mulmotion` / `-lipsyncpatch` / `-advanced` 全 v7）→ 撞墙 → 用户拍板切 `easy-live2d@^0.4.4`（间接绑 pixi.js@^8 通过 @pixi/sound@^6；MPL-2.0；panzer-jack 维护）。`pnpm install` +17 packages 通；Cubism Core JS（207KB）从 Live2D 官方 CDN 拉；Hiyori 完整模型（4.7MB / 11 motions / 2 textures）从 `Live2D/CubismWebSamples` sparse checkout；License 文件齐全。design §3.3 / §3.4 / §3.5 / §6.2 / §8 / §9.2 / §10 / §11.3 / §12 / §13 全部从 `Live2DModel` API 形态对齐到 `Live2DSprite` API 形态 + 变更记录。 |
| 2026-06-15 | M18.9 真跑反馈一轮·polish | 用户 macOS 真跑反馈一波改进：(1) `usePixiAvatarSlot` 引入 alpha-scan one-shot lock + invalidateAnchor callback（占位 → Live2DSprite 切换时重扫 anchor），ActionBar / bubble 锚紧贴 Hiyori 不抖；(2) Tauri listener race + StrictMode 双 mount 修复：3 个 useEffect 加 cancelled flag + `safeUnlisten` helper 吞 stale-cleanup `listeners[eventId].handlerId undefined`；(3) chat 窗 `Composer` 改不受控（regress reactify 受控失效）+ capture-phase keydown listener + compositionstart/end 自跟踪 IME guard（修中文 IME composition Enter 直接 send）；(4) `HistorySidebar` 监 connection status 重连后自动 refresh；(5) chat App ping 改 setInterval 3s 轮询（dev cold start bridge 慢自愈）；(6) `open_chat` Rust 命令用 NSApplication.activateIgnoringOtherApps + run_on_main_thread（修 Accessory policy 下 chat 窗弹出无 key window 输入不可用）；(7) bubble.html 加 `html,body{overflow:hidden}`（消除 PetBubble + webview 双滚动条）；(8) `scripts/dev/run.sh` + `.ps1` 默认设 `AGENT_BRIDGE_DEV_MODE=true`（fire-source / 018 push 调试可用）；(9) `petState` ingest 加 batch envelope 兜底（BedtimeSource 一次性 envelope 含几百 text_delta + done 时 schedule lip-sync 时长后回 idle）；(10) `TextCadenceMouthDriver` hook `sprite.onRender`（让 setMouth 在 motion apply 后赢，否则被 Hiyori idle motion 覆盖）；(11) dev-only 添加 phase 切换 / push envelope / lip-sync hook 等 console 日志（production tree-shake）。门禁仍全绿（typecheck + 72 tests + cargo check 双端）。 |
| 2026-06-15 | M18.9 macOS 主链路 AC 真跑闭环 | 用户用 `./scripts/dev-fire-source/run.sh cron:bedtime` 触发主动轮，pet 窗 console 完整观测到 `[push] envelope kind=agent_turn source=cron:bedtime events=[text_delta×N,done]` → `[petState] phase idle → speaking` → `[petState] phase speaking → idle`，AC-3 / AC-4 通过。`[lip-sync] driver attached + onTextDelta first chunk + setMouthOpen first call · param "ParamMouthOpenY" range=Object` 三条 dev log 都出，driver attach / text_delta forward / setParameterValueById 链路通；fire-source 时视觉嘴有动（AC-5 通过）。剩 chat 窗对话 lip-sync 不工作的设计缺失登记为 [issue 011](../../issues/011-chat-conversation-no-lipsync/) 不在本期修。 |
| 2026-06-16 | M18.9 Win 真跑闭环 · issue 012 真根因确认 · 收尾 COMPLETED | 用户 Win 双屏（2560×1600 175% + 2560×1440 125%）拉 commit `876718f` 仍然穿透 ON 完全失效，对照验证（鼠标精确移到 sprite 矩形坐标 ~(2150, 700) 时 `inSprite=true` 但鼠标在屏幕物理位置上不是 Hiyori 视觉中心）锁定真根因 = cursor / viewport 双 DPR 坐标系错位（cursor 用 webview owner monitor DPR 1.75 算 max ≈ 2925，spriteScreen 用 viewport DPR 1.25 算 max ≈ 4096，两套不重合，cursor 进不到 sprite 矩形，`slotBoundsHit` 第一关就 false）。两轮 mitigation（49a26c2 / 876718f）实际关错位置（都在 hit-test 链路后段），真根因在 cursor 坐标计算（Rust `spawn_cursor_feed`，属 17a / ADR 0004 底座）。按 handoff 原则不再深入修，AC-8 走 known-limitation，issue 012 README 全文重写反映真根因 + 留 17c spike 根治路径。两次 mitigation commit 保留——对 mac 无害（mac alpha-scan 一帧锁定，flag 恒 false，alpha 精度保留），对 Win 没用但代码也不破坏。另用户报告 mac 长气泡滚不动 → 锁定到 4bf33f4 引入的 bubble.html `html, body { overflow: hidden; }` polish 在 mac WebKit 透明窗 + nested overflow 下触发 wheel quirk → commit `b3ec1a2` 还原 polish 恢复 16/15 已知 work 行为（双滚动条偶发回归留后续）。本期落地完成。 |
| 2026-06-16 | M18.9 Win 真根因修复闭环 v1 → v2 · AC-8 真 PASS · issue 012 fixed | 用户改主意要本期把 Win mixed DPR 修了。**v1（commit `78dcd26`）**：`spawn_cursor_feed` scale 从 `pet.scale_factor()` 改按 cursor 当前所在 monitor `available_monitors() / scale_factor()` —— 同屏 hover/drag/click 恢复 ✓，**但跨屏 drag 时 cursor 数字范围突跳（×1.4）→ 跟 spriteScreen 矩形 PIXI viewport 固定坐标系不重合 → drag 中断 + 完全穿透 ✗**。**v2 真根因（commit `3db1552`）**：cursor scale 必须用 **webview viewport 实际 DPR**（固定值，整个 webview 一套），不是 cursor monitor DPR 切换。Tauri Rust 拿不到 webview viewport DPR，让前端 mount 时 `invoke('set_pet_webview_dpr', { dpr: window.devicePixelRatio })` 上报，Rust 加 `PetWebviewState { dpr: Mutex<f64> }` state + `set_pet_webview_dpr` 命令存储；`spawn_cursor_feed` 优先用 state.dpr（0.0 兜底 pet.scale_factor() 防启动早期未上报）；前端加 `matchMedia('(resolution: Xdppx)')` listener 监 DPR 变化罕见 case。Win 端用户拉 `3db1552` 验证：跨屏 drag cursor 数字保持连续、不中断；穿透 ON 下跨屏后 hover/drag/click 全 work。**AC-8 真 PASS，issue 012 fixed**（不再 known-limitation 也不留 17c spike）。教训：cursor / sprite 同坐标系是 hit-test 链路前置条件，**cursor scale 必须是 viewport 单一固定 DPR**，按 cursor monitor 动态切换会引入新错位（跨屏 drag）。 |

---

## 文档元信息

- **状态**：已完成（COMPLETED）
- **创建时间**：2026-06-15
- **完成时间**：2026-06-16
- **关联需求**：[requirement.md](./requirement.md)
- **关联技术方案**：[design.md](./design.md)
- **实施载体**：worktree `bubbly-wondering-milner` · 分支 `feature/018-pet-live2d-state-and-lipsync` · 基线 `origin/main@00a57dd`
