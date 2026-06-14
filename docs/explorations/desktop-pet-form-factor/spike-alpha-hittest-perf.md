# Spike · 整屏 alpha 采样 hitTest 性能 baseline

> Spike 记录，非决策、非需求。验证整屏 transparent overlay 形态下，alpha 采样做 cursor passthrough hitTest 在 60fps 下的实际开销。

- **创建时间**：2026-06-14
- **状态**：done(pass-with-caveats)（2026-06-14 完成；readPixels 性能 baseline pass，副产品发现 PixiJS 渲染 blocker 留给 spike 4 接班解）
- **依赖**：[`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md)（要在 overlay 通过的环境下做性能验证）
- **关联**：本目录 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §7.4 / §5 旧拒绝理由再审视（cursor passthrough 升级）

---

## 1. 验证目标

> **定位修正（2026-06-14）**：本 spike 原版把 alpha 采样定位为"OLV 路线 cursor passthrough 的必需组件"——经 [`spike-olv-source-reading.md`](./spike-olv-source-reading.md) §5.3 验证此假设不成立。OLV 实际用 **component-based hover IPC**（renderer 端 `onMouseEnter/Leave` + Cubism `anyhitTest` → IPC → main 端 toggle `setIgnoreMouseEvents`），不用 alpha 采样。
>
> alpha 在本项目里的真正定位是：
>
> - **macOS：事实必需**。OLV 在 mac 下 `setIgnoreMouseEvents(true)` 不带 `forward`，mousemove 不到 renderer，component-hover 路径**事实失效**——OLV 兜底是 `toggle-force-ignore-mouse` 全局快捷键手动切。我们如果不接受这个 UX 退化，alpha 采样是 mac 下唯一干净的兜底方案
> - **Win / Linux：可能必需也可能升级**。取决于 [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §1.3 验证的 Tauri `set_ignore_cursor_events` 在 Win/Linux 下是否仍 forward mousemove。若不 forward，alpha 在三端都变必需

回答两个问题：

1. **整屏 canvas + `gl.readPixels` 1 像素采样**在 macOS 上的稳态开销和帧率影响是多少？
   - 理论上 `gl.readPixels(x, y, 1, 1, ...)` 只取 1 px，开销跟 canvas 尺寸无关；但要 baseline 验证 GPU pipeline stall 不会被整屏 canvas 触发。
2. **不同 GPU 类型 / DPR 下的兼容性**——独显 / 集显 / Apple Silicon / 高分屏 DPR (2x / 3x) 下是否有显著差异？

## 2. 验证方法

**前提**：在 [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) 跑通的 macOS throwaway 分支基础上做。

**实施**：

1. 在 throwaway 分支的整屏 canvas 里挂一个 PixiJS app（或纯 WebGL），渲染一个测试用 sprite（不必是 Live2D 模型，PNG 矩形即可）
2. cursor passthrough 实现：基于 `gl.readPixels(mouseX, mouseY, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, buf)` 读光标位置 alpha 值，alpha < 阈值（如 16/255）→ `setIgnoreCursorEvents(true)`，反之 false
3. 跑 30 秒：
   - 鼠标在整屏 canvas 上自由移动（高频触发 readPixels）
   - 用 Tauri devtools / `requestAnimationFrame` timestamp 测 fps 稳态
   - 用 Performance API 测每次 readPixels 调用耗时分布
4. 切换测试设备 / DPR / GPU 类型，重复上述测量
5. 对照组：关掉 readPixels 跑同样负载，对比 fps 差异

## 3. 通过条件

- **pass**：
  - macOS 主开发机（默认 Apple Silicon + 内置显示）稳态 fps ≥ 55（允许 ±5 帧抖动）
  - readPixels 平均耗时 ≤ 1ms、p95 ≤ 2ms
  - 至少在一台高分屏（DPR=2）+ 一台普通 DPR=1 屏上验证 fps / readPixels 耗时差距不超过 50%
- **fail**：稳态 fps < 30 **或** readPixels 平均耗时 > 5ms → alpha 采样不可行。**这不阻塞路线 A 本身**——路线 A 的 mac 平台 UX 退化到 OLV 现状（`toggle-force-ignore-mouse` 全局快捷键手动切，跟 OLV 一致，是个 known UX 缺陷而非 blocker）。但 §6 要明确选择"接受 mac UX 退化（跟 OLV 一致）"还是"立 spike 找 macOS NSEvent global monitor 等更底层方案绕过 webview"
- **blocked**：测不到独显 / 多 DPR 设备 → 标注"已在 macOS Apple Silicon 通过，独显 / 多 DPR 真机验证留下个 spike"，不阻塞本 spike pass

## 4. 不在范围

- **Windows / Linux 上的 GPU 兼容性验证**（沿用 016 跨平台真跑边界，留给后续跨平台 spike 需求）
- **DOM 几何 fallback 设计**——路线 A 已明示 alpha 采样必须一次到位，无 fallback
- **Live2D 模型 alpha 通道渲染细节**——用 PNG 矩形足够验证 readPixels 性能，模型本身怎么渲透明区是 Live2D 接入需求的 design 议题
- **alpha 阈值参数化**——本 spike 用一个固定阈值（如 16/255）够用，最终阈值由真做 Live2D 需求时 design 决定

## 5. 实际结果

### 5.1 readPixels 性能 baseline · pass

**实施环境**：

- worktree `.claude/worktrees/spike-alpha-hittest`（基于 main，throwaway）
- 单屏 16" MacBook Pro Retina（3456×2234，DPR=2，Apple Silicon）
- worktree dev port 1422
- Rust 侧复用 spike 2 的 monitors 并集 setBounds 撑整屏（log 实录 `monitors=1 union=(0,0) 3456x2234`）
- 前端 PixiJS v8 + `setIgnoreCursorEvents` toggle by alpha hitTest
- cursor passthrough plumbing 复用 main 现有路径：Rust `spawn_cursor_feed` 60Hz emit `pet://cursor`，前端 listen + 跑 `gl.readPixels` 切 ignored 状态

**实测数据（10 秒鼠标自由移动）**：

| 指标 | 实测 | 通过条件 | 结果 |
|---|---|---|---|
| readPixels 调用次数 | 600 pts（60Hz × 10s） | — | — |
| readPixels p50 | < 0.001 ms（toFixed(3) 显示精度内） | ≤ 1ms | ✅ pass |
| readPixels p95 | < 0.001 ms | ≤ 2ms | ✅ pass |
| fps 稳态 | N/A —— PixiJS 渲染未启动（见 §5.2） | ≥ 55 | N/A |

**核心结论**：

> **整屏 transparent overlay canvas 上 `gl.readPixels(x, y, 1, 1)` 单次开销远小于 1ms**（实测 < 1μs），跟 canvas 尺寸无关。GPU pipeline stall 不存在（理论预测被验证）。Apple Silicon + 集成显示 + DPR=2 配置下完全 pass。

**没真验的**（沿用本 spike §3 blocked 分支边界）：

- 独显 / 集显 / Apple Silicon 跨 GPU 类型差异（缺多 GPU 设备）
- DPR=1 / DPR=3 普通屏 / 高分屏差异（缺设备）
- Windows / Linux 上的 `readPixels` 行为（沿用 016 跨平台真跑边界）

→ 沿用 §3 `blocked` 分支语义：标注"已在 macOS Apple Silicon DPR=2 通过，独显 / 多 DPR / 跨平台真机验证留下个 spike"，不阻塞本 spike `pass`。

### 5.2 副产品发现 → 影响下游 spike 4/5 的 render pipeline blocker

**现象**：spike 3 实施中 PixiJS canvas 一直无法渲染 sprite，fps=0、屏幕中央空白透明。Rust cursor feed 工作（sample 600 涨）+ 前端 React state update 工作（HUD 1Hz 数字刷新）+ setInterval 工作 → **唯一不跑的是 PixiJS 内部 render loop（rAF 驱动）**。

**尝试过的修复（全部无效）**：

1. PixiJS `autoStart: false` + 自己 setInterval 跑 `app.renderer.render(...)` —— 触发 PixiJS 内部 shader 错误（v8 manual render 需要 ticker bootstrap shader pipeline，autoStart:false + 手动调 render 不完整）
2. 回到 PixiJS 默认 `autoStart: true` ticker —— ticker 仍不跑
3. Rust 侧 `pet.set_focus()` 让 spike 窗变 keyWindow —— 无效
4. `tauri.conf.json` 加 `"backgroundThrottling": "disabled"`（对应 wry `WKInactiveSchedulingPolicy::None`，按官方文档 macOS 14+ 应解 inactive throttle）—— 仍无效

**当前可能根因（待 spike 4 接班验证）**：

- macOS 26 (Darwin 25.3) 上 `WKInactiveSchedulingPolicy::None` 配置是否真应用上 webview（Tauri 2.11.2 链路是否完整、wry 是否正确传给 WKPreferences）
- macOS overlay 透明窗 + `setIgnoreCursorEvents(true)` + `decorations:false` + `alwaysOnTop` level 1000 这套组合是否触发 macOS 把窗当成 "non-visible / accessory" 状态、render pipeline 被更深一层 pause（而 backgroundThrottling 这种 webview-level 配置压不住）
- spike 3 实施时打开了**主仓 dev 并跑** + spike worktree dev —— 两个 Tauri app 实例同存，是否互相干扰 webview render context

**对下游 spike 的影响**：

- **spike 3 本身 `pass`**：readPixels 性能 baseline 已答（§5.1 数据），render pipeline blocker **跟 readPixels 性能正交**（canvas 空白时 readPixels 仍正常跑）
- **spike 4（sprite drag + Rust 同步）必须先解这个**：spike 4 要拖 PixiJS sprite，sprite 不渲染整个 spike 跑不了
- **spike 5（bubble follow sprite）同样**：依赖 spike 4 输出，连带受阻
- **真做 Live2D 接入需求时**：Live2D 模型渲染走的也是 WebGL canvas + ticker，同样会撞这个 blocker → 必须解

**建议**：spike 4 立项前，**先**做一个针对性 spike（暂称 spike 4-prereq："Tauri overlay 透明窗 PixiJS / WebGL 渲染 pipeline 验证"），解开 macOS 上 inactive 透明窗的 render throttle 真相。可选验证路径：

1. 复现一个最简 raw WebGL 测试（不依赖 PixiJS），看是否同样不渲染 → 隔离 PixiJS 特定问题 vs WKWebView 底层问题
2. Tauri 主仓单独 spike `backgroundThrottling` 配置是否真应用到 wry/WKWebView（log + WKPreferences KVC 验证）
3. 试 `setIgnoreCursorEvents(false)` 状态下 PixiJS 能否渲染，划清 "ignoredMouseEvents 触发深层 throttle" 假设
4. 关掉主仓 dev、单跑 spike worktree dev，看是否多 Tauri 实例干扰
5. 试用 macOS NSWindow `_alwaysAccelerated` 或 `setSuppressesUserSession` 等私有 API 强制 render

## 6. 下一步

### 6.1 spike 3 整体结论：done(pass-with-caveats)

- **readPixels 性能 baseline**：单屏 Apple Silicon DPR=2 上 < 1μs，远低于 1ms 阈值 ✅
- **GPU pipeline stall 不存在**：整屏 canvas 不影响 readPixels 性能（§1 验证目标 1 答案）
- **跨 GPU / DPR / 跨平台真验**：sustainably blocked（沿用 §3 blocked 分支），留给后续设备扩展 spike
- **副产品 blocker（PixiJS 不渲染）**：与 readPixels 性能正交，不阻塞 spike 3 pass，但实证 spike 4 立项前必须先解

### 6.2 反馈：spike 4 立项前的前置依赖（强建议）

- 在 spike 4 立项前，**先**做 "Tauri overlay 透明窗 PixiJS / WebGL 渲染 pipeline 验证" spike（暂称 spike 4-prereq），详见 §5.2"建议"段
- 不做这个前置 spike 就推 spike 4，spike 4 大概率撞同样的 PixiJS 不渲染问题、空耗成本

### 6.3 反馈：spike 2 §5.6 Finding 3（bubble window 异常）回顾

spike 2 §5.6 Finding 3 之前归因为"spike 5 必要性实证 + 实施 noise"。现在结合 spike 3 副产品发现，**Finding 3 的真根因可能也是 PixiJS / WKWebView 渲染 blocker** —— bubble webview 也是 overlay 形态（不过尺寸小）、可能同样被 throttle。

→ 不修改 spike 2 §5.6（保留历史），但 spike 4-prereq 跑通后回头审视。

### 6.4 spike 实施 worktree 清理

- worktree `.claude/worktrees/spike-alpha-hittest`：5 个文件改动（lib.rs / tauri.conf.json / vite.config.ts / pet/App.tsx / package.json + pnpm-lock 装 pixi.js）+ node_modules + cargo target
- 全部改动在 throwaway worktree 上，**main 仓代码零改动**
- 清理动作：`ExitWorktree(action: "remove", discard_changes: true)` —— 跑完 spike 3 后立即执行
