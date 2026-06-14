# Spike · Tauri Windows 整屏 transparent overlay + WebGL/PixiJS 文档调研

> Spike 记录，**纯文档调研型**，非端到端真机验证。承接 [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) §4.4 接受的 trade-off + §5.2 Win 暂定路径 + §6 反转触发条件 3，把"Win 整屏 overlay + WebGL/PixiJS 是否可工程化"这条 macOS 端已绿、Win 端纯空白的硬约束先用 paper 调研填到"工程师拿到这份 spike 能直接接力跑端到端真机 spike"的程度。
>
> 对照 [`spike-tauri-nspanel-pet-window.md`](./spike-tauri-nspanel-pet-window.md) 是 macOS 端的**端到端真机 spike**，本 spike **不真机、不端到端、不写代码**——本 §0 元信息显式标"spike 类型 = 文档调研"以避免读者把 §3 通过条件按端到端 spike 语义读。

- **创建时间**:2026-06-14
- **spike 类型**:**文档调研**（vs `spike-tauri-nspanel-pet-window.md` 是端到端真机；原因 = 无 Win 开发机硬约束,沿 [需求 016 R-4.7.4](../../requirements/016-pet-bubble-independent-window/requirement.md) + [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §2 边界）
- **状态**:**done(pass-with-caveats)**（2026-06-14 完成；6 子问题全有"已知答案"或"显式转交真机"标注 + 没读到 fundamental block；caveat = [wry#1691](https://github.com/tauri-apps/wry/issues/1691) + 上游 [WebView2Feedback#5536](https://github.com/MicrosoftEdge/WebView2Feedback/issues/5536) mouse-movement GDI 泄漏对桌宠 cursor 持续穿透场景几乎是最坏受害,且上游 Microsoft 尚无公开回复 / workaround,但仅在 Win 11 + 较新 WebView2 复现）
- **依赖**:[`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md)（§5.1 已读穿三端 `set_ignore_cursor_events` 实现,本 spike 扩展到整屏 + transparent + toggle 高频组合）、[`spike-tauri-nspanel-pet-window.md`](./spike-tauri-nspanel-pet-window.md)（macOS 端到端已 pass-with-caveats,本 spike 给 Win 端到端 spike 铺路）、[`industry-standard-tauri-nspanel.md`](./industry-standard-tauri-nspanel.md) §7（已记 Win 边界 + 已知反向证据）
- **关联**:[ADR 0004](../../decisions/0004-pet-overlay-route/README.md) §4.4 / §5.2 / §6 反转条件 3、[`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §7.7（本 spike 在 §7 表追加一行指向本 doc）
- **下游**:Win 端到端真机 spike（待真做 Live2D 接入需求 / 拿到 Win 开发机后承接,沿 macOS spike §7.6 同样"先 spike 后落地"规范）

---

## 1. 验证目标

文档调研型 spike 的 §1 = "调研子问题清单"。每个子问题用 paper 路径调到"可答"或"显式转交真机"。不强求"在文档层就给出端到端结论"——文档层给不出端到端确定性,但能把"哪些是公开已知 / 哪些必须真机验"明确分类。

### 1.1 整屏 setBounds + monitors union 在 Win 上是否稳定 / 有特殊限制

子问题:Tauri Win 端把 `set_size` + `set_position` 撑到 monitors union 整屏,底层 tao + Win32 API 层面有没有特殊限制、cap、或 caveat?

调研路径:tao Win `window.rs` 源码 / Tauri 文档 / 业界 Win 整屏实证。

### 1.2 setIgnoreCursorEvents Win 路径行为(toggle 高频 + 整屏 layered window)

子问题:Tauri `set_ignore_cursor_events` 在 Win 上的实际 Win32 实现是什么?60Hz Rust polling 频繁 toggle 在整屏 layered + transparent 组合下行为如何?是否触发 GDI / DWM 副作用?

调研路径:tao Win `window_state.rs` 源码 / [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §5.1 已读穿一半 / 本 spike 扩展。

### 1.3 PixiJS rAF 在 Win Chromium WebView2 整屏 + transparent 下是否 60fps

子问题:macOS NSPanel + nonactivating 给 30fps cap 是 macOS WKWebView 特性,Win Chromium WebView2 没有 NSPanel/key-window 概念,理论上无 30fps cap。但整屏 + transparent + DWM blur-behind 组合下 PixiJS rAF 能否稳态 60fps?

调研路径:wry Win 实现 / Tauri 官方文档 / WebView2 Chromium 内核共识 / OLV Electron Win 实证间接证据。

### 1.4 [tauri#9373](https://github.com/tauri-apps/tauri/issues/9373) Win 10 canvas 拉满窗 choppy 复现性 + workaround

子问题:#9373 报告 Win 10 上 canvas `width="100%" height="100%"` + rAF 动画卡顿,固定 canvas 尺寸恢复流畅。这条反向证据当前状态、复现条件、是否有 workaround?跟整屏 transparent overlay 形态有多大关系?

调研路径:issue body + 评论 + 状态 + 关联 PR + reactions 总数。

### 1.5 [wry#1691](https://github.com/tauri-apps/wry/issues/1691) + [WebView2Feedback#5536](https://github.com/MicrosoftEdge/WebView2Feedback/issues/5536) GDI 泄漏

子问题:#1691 报告 WebView2 mouse movement 触发 GDI 泄漏,close 后移交上游 Microsoft。当前 root cause / 复现环境 / WRY-level workaround 全谱 / Microsoft 回复状态?对桌宠 cursor 持续穿透场景的影响?

调研路径:wry#1691 body + 评论 + close reason + 上游 WebView2Feedback#5536 全谱。

### 1.6 业界 Win 整屏 overlay 先例(**待答 · 用户 declare 阶段 pushback 的点**)

子问题:**业界(Electron 同栈 + Tauri 同栈 + 其他)做 Win 整屏 transparent overlay 有没有公开实证?** declare 阶段我假设"业界 Tauri Win 桌宠都是固定窗",但用户 pushback 指出"这得调研后才知道"——本子问题作为待答而非既定事实查清。

调研路径:OLV Win / Copiwaifu / BongoCat / manasight 博文 / judehek/tauri-plugin-overlay 多源对照。

---

## 2. 调研方法

**前提**:沿 [需求 016 R-4.7.4](../../requirements/016-pet-bubble-independent-window/requirement.md) + [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §2 边界,**本 spike 不真机、不写代码**。所有结论从公开文档 / 源码 / issue / 博文导出,**不预设业界结论**。

### 2.1 阅读清单

| 资料 | 类型 | 用途 |
|---|---|---|
| [tao `src/platform_impl/windows/window.rs`](https://github.com/tauri-apps/tao/blob/dev/src/platform_impl/windows/window.rs) | 源码 | §1.1 setBounds 实现 / transparent DWM blur-behind / fullscreen 边界 |
| [tao `src/platform_impl/windows/window_state.rs`](https://github.com/tauri-apps/tao/blob/dev/src/platform_impl/windows/window_state.rs) | 源码 | §1.2 `IGNORE_CURSOR_EVENT` flag → Win32 ex-style 映射 |
| [tauri#9373](https://github.com/tauri-apps/tauri/issues/9373) | issue | §1.4 反向证据 |
| [wry#1691](https://github.com/tauri-apps/wry/issues/1691) | issue | §1.5 反向证据 |
| [WebView2Feedback#5536](https://github.com/MicrosoftEdge/WebView2Feedback/issues/5536) | upstream issue | §1.5 Microsoft 侧状态 |
| Context7 `/websites/v2_tauri_app` | 文档 | §1.1 / §1.2 / §1.3 Tauri 2 Win 端 transparent / ignore cursor events / fullscreen 官方说明 |
| [manasight 2026 实战博文](https://blog.manasight.gg/why-i-chose-tauri-v2-for-a-desktop-overlay/) | 博文 | §1.3 Win 11 14MB / <1% CPU 数据 / §1.6 Tauri 业界 Win overlay 实证 |
| [BongoCat `src-tauri/tauri.conf.json` (master)](https://raw.githubusercontent.com/ayangweb/BongoCat/master/src-tauri/tauri.conf.json) | 实证 cfg | §1.6 Tauri 21.4k stars 桌宠 Win cfg |
| [Copiwaifu `src-tauri/tauri.conf.json`](https://raw.githubusercontent.com/Panzer-Jack/Copiwaifu/main/src-tauri/tauri.conf.json) | 实证 cfg | §1.6 Tauri 同栈最对标 |
| [OLV `src/main/window-manager.ts`](https://raw.githubusercontent.com/Open-LLM-VTuber/Open-LLM-VTuber-Web/main/src/main/window-manager.ts) | 实证源码 | §1.6 Electron 整屏 overlay Win 实证 |
| [judehek/tauri-plugin-overlay README](https://github.com/judehek/tauri-plugin-overlay) | 工具 | §1.6 DLL injection 路径 / ADR 0004 §6 反转条件 3 选项 a 兜底参考 |

### 2.2 调研工具

- WebFetch:抓 issue body / 博文 / cfg(评论页 SSR 不全的坑通过 GitHub raw URL 绕开)
- `curl + GitHub API`:抓 issue 评论 / metadata / close reason(WebFetch 不可访问的部分)
- Context7 `mcp__context7__query-docs`:Tauri 2 官方文档查 transparent / setIgnoreCursorEvents / setAlwaysOnTop on Windows
- 不用 WebSearch(本环境 400 报错)

### 2.3 调研边界

- **不真机端到端**(无 Win 开发机硬约束)
- **不验 Linux**(ADR 0004 §5.3 Linux 不在产品支持范围)
- **不写代码**(沿 [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §2 文档部分边界)
- **不验 OLV 是否可直接 Tauri 复制**:Electron 跟 Tauri WebView2 wrap 不同(wry 跟 Electron 内嵌 Chromium app shell 不同),OLV Win 整屏实证仅作间接证据,**不蕴含 Tauri Win 整屏 overlay 可工程化**

---

## 3. 通过条件

文档调研型 spike 的 pass 标准 vs 端到端 spike 不同:**不验"路线在 Win 端能否真跑",验"调研覆盖度足够让真机工程师接力 + 没读到不可绕反向证据"**。

| 状态 | 触发条件 | 含义 |
|---|---|---|
| **done(pass)** | §1 6 子问题每个都有"已知答案"或"显式转交真机"标注 + 没读到任何 critical caveat | 路线 A Win 端文档层面没有 fundamental block,真机 spike 可直接接力 |
| **done(pass-with-caveats)** | pass 标准 + 读到 1+ critical caveat 但不构成 fundamental block(如反向证据存在但有 workaround / 仅特定环境复现 / 业界已知 trade-off) | 真机 spike 接力时必须把 caveat 当核心验证项重点验 |
| **fail** | 读到不可绕的反向证据,文档层就能证明"路线 A Win 端 fundamentally 不可工程化" | 触发 [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) §6 反转条件 3 |
| **blocked** | 关键文档 / 源码 / issue 不可访问 / API rate limit 不可绕 / 多源对照矛盾不能形成结论 | 不算 fail,后续重试或换调研路径 |

---

## 4. 不在范围

- **不真机端到端 / 不写代码**:见 §2.3
- **不验 Linux**:ADR 0004 §5.3
- **不验 macOS / 端到端 Live2D**:已分别由 [`spike-tauri-nspanel-pet-window.md`](./spike-tauri-nspanel-pet-window.md) + 真做 Live2D 接入需求 design 阶段承接
- **不在文档层就给端到端确定性结论**:文档调研不能替代真机,但能"分类哪些是公开已知 / 哪些必须真机验"
- **不验 OLV / Copiwaifu / BongoCat 等业界项目自身**:仅作为本 spike 的实证 / 反证材料,不深读业界项目其他细节
- **不验 manasight 14MB / <1% CPU 的具体场景**:作者博文刻意不公开 implementation 细节,本 spike 直接接受"不可知"作为 finding
- **不深读 wry Win WebView2 transparent 实现细节**:WebFetch 拿 wry `webview2/mod.rs` 触发 cyber-safeguard(被拦截),改走 Tauri 官方文档 + tao 源码 + Tauri community 实证间接推导
- **不调研 Edge / WebView2 历史版本兼容性**:留给真机 spike(本 spike 仅记录 #1691 在 Win 10 + WebView2 145 不复现 / Win 11 build 26200 + WebView2 146.0.3856.59 复现)

---

## 5. 实际结果

### 5.1 §1.1 整屏 setBounds 在 Win 上稳定性 · **可工程化(留真机验证)**

**tao Win window.rs 实证**(直接读源码):

`set_outer_position` / `set_inner_size` 在 Win 平台**直接转发到 `SetWindowPos`**,没有针对整屏 / 超大尺寸的特殊裁剪 / cap / 限制。唯一的"特殊处理"是 undecorated + shadow 窗口的边框 offset 补偿(注释引 GPUI 实现):

> "undecorated windows with shadows have hidden offsets / we need to calculate them and account for them in new size"

`set_outer_position` 同时清除 `MAXIMIZED` flag。`set_inner_size` 走 `util::set_inner_size_physical`。

**transparent 实现路径**:在 `InitData::on_create` 中走 **DWM blur-behind region** 方案(非 layered window / 非 `DwmExtendFrameIntoClientArea`):

```rust
if self.attributes.transparent && !self.pl_attribs.no_redirection_bitmap {
  // Empty region for the blur effect, so the window is fully transparent
  let region = CreateRectRgn(0, 0, -1, -1);
  let bb = DWM_BLURBEHIND {
    dwFlags: DWM_BB_ENABLE | DWM_BB_BLURREGION,
    fEnable: true.into(), hRgnBlur: region, ...
  };
  let _ = DwmEnableBlurBehindWindow(win.hwnd(), &bb);
}
```

注释明示:**空 region 实现完全透明**。同时设置 `WindowFlags::TRANSPARENT` + `NO_BACK_BUFFER`(后者控制 `WS_EX_NOREDIRECTIONBITMAP`)。

**fullscreen 边界注释**(对本 spike 不直接适用但值得记下):tao 在 native fullscreen 模式有 DWM 接管 workaround:

> "the DWM thinks our program has frozen and takes over our program's window / Calling `PeekMessageW` here notifies Windows that our process is still running"

本项目走 setBounds 自己撑整屏(非 native fullscreen),**不触发此 workaround**。但侧面说明 **DWM 对 Win 整屏窗口很敏感**——transparent + setBounds 整屏在文档层无 cap,但真机要警惕 DWM 边界。

**Tauri 文档侧**:Context7 查询 transparent / setBounds on Windows 官方文档,**没有针对 Win transparent 的专门页 / 限制 / caveat**。`setIgnoreCursorEvents` / `setAlwaysOnTop` 是跨平台 JS API,无 Win 特殊说明。wry README 关于 transparent 的 note 只提 macOS 私有函数要求,**Win 不展开**——开箱可用但缺指引。

**结论**:
- **文档层无 fundamental block**:tao Win `set_size` / `set_position` 不对整屏特殊处理;DWM blur-behind region 实现 transparent 与窗尺寸无关
- **业界 Tauri Win 整屏 setBounds 公开实证空白**(详 §5.6),但 **OLV Electron Win 端走相同 setBounds monitors union 路径已实证**,Win API 层面可行性间接验证
- **必须真机验**:多屏 + 不同 DPR + Win 10 / 11 + 不同 GPU 下整屏 setBounds 实际行为

### 5.2 §1.2 setIgnoreCursorEvents Win 路径 · **底层实现已读穿;toggle 高频组合留真机验证**

**tao Win window_state.rs 实证**:`IGNORE_CURSOR_EVENT` flag 在 `to_window_styles` 映射到 `WS_EX_TRANSPARENT | WS_EX_LAYERED`:

```rust
if self.contains(WindowFlags::IGNORE_CURSOR_EVENT) {
  style_ex |= WS_EX_TRANSPARENT | WS_EX_LAYERED;
}
```

**应用路径**:`apply_diff` 检测到 flag 变化时统一走 `SetWindowLongW(GWL_EXSTYLE, ...)` + `SetWindowPos(SWP_FRAMECHANGED)` 刷新窗口:

```rust
if diff != WindowFlags::empty() {
  let (style, style_ex) = new.to_window_styles();
  // ...
  SetWindowLongW(window, GWL_EXSTYLE, style_ex.0 as i32);
}
```

**关键 caveat**(文档层 finding):
- **tao 不调 `SetLayeredWindowAttributes`**(无 alpha key / color key 设置)——`WS_EX_LAYERED` 只是位被设上,DWM blur-behind region 单独实现透明,**两者独立累加**
- **不 forward mousemove**(沿 [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §5.1 已读穿)——OLV `setIgnoreMouseEvents(true, { forward: true })` 的 Electron 偷懒路径在 Tauri 不可用,cursor passthrough 必走 alpha 采样或 Rust 60Hz polling
- **toggle 高频影响未知**:60Hz Rust polling 频繁 `SetWindowLongW` + `SetWindowPos(SWP_FRAMECHANGED)` 在 Win 上每次都触发窗口 frame 重算。整屏 layered + transparent + 高频 toggle 组合下是否触发 cursor 抖动 / GDI 副作用 / DWM 重合成开销过大——**文档层无答案**

**结论**:
- **底层实现已读穿**(tao Win 路径完整),不蕴含 fundamental block
- **必须真机验**:60Hz toggle 在整屏 layered + transparent 下的 cursor 流畅度 + GDI 累积 + DWM 重合成开销

### 5.3 §1.3 PixiJS rAF 60fps 在 Win Chromium WebView2 整屏 + transparent 下 · **理论可达,无公开 Tauri 实证;留真机重点验**

**Win Chromium WebView2 vs macOS WKWebView**:
- macOS NSPanel + nonactivating 给 30fps cap 是 macOS WKWebView 特性([`spike-tauri-nspanel-pet-window.md`](./spike-tauri-nspanel-pet-window.md) §5.1 反馈"NSPanel + nonactivating + 非 key window"OS-level cap)
- Win **没有 NSPanel / key window 概念**,WebView2 Chromium 内核理论上不应有相同 cap
- 但 Win Chromium WebView2 + DWM blur-behind 整屏 + transparent 组合下 rAF 行为**业界无 Tauri 公开实证**

**OLV Electron Win 间接证据**:
- OLV `src/main/window-manager.ts` Win 端跟 macOS 共用整屏 overlay 路径(详 §5.6),Live2D Cubism Web SDK + WebGL canvas 在整屏 transparent 下运行
- 但 OLV 是 Electron(内嵌 Chromium full app shell + 自己处理 WebGL alpha 配置 in `LAppDelegate`),不是 Tauri 的 wry/WebView2 wrap——**两者 IPC + render pipeline 实现不同**,OLV Win 跑通不等于 Tauri 同条件下跑通

**反向证据 [tauri#9373](https://github.com/tauri-apps/tauri/issues/9373)**(详 §5.4):Win 10 上 canvas 拉满窗 + rAF 动画 choppy,但**报告未明确指定 transparent / alwaysOnTop / 整屏配置**,关键变量缺失;且 2 年内无共鸣 / 0 reactions / 仅 1 reporter——可能是特定环境问题(显卡 driver / Win 10 + 老 Tauri / 老 WebView2 runtime)

**结论**:
- **文档层无 fundamental block,但无 Tauri 公开实证**
- **必须真机验**:PixiJS rAF 60fps 在 Win 11 + Win 10 + 新旧 WebView2 runtime + 整屏 + transparent + DWM blur-behind 完整组合下实测——这是本 spike **最 critical 的待验项**(直接决定路线 A 在 Win 端能否落地)

### 5.4 §1.4 [tauri#9373](https://github.com/tauri-apps/tauri/issues/9373) Win 10 canvas choppy · **2 年开放无 fix,但单个 reporter 无共鸣,关键变量缺失**

**Issue 当前状态**(GitHub API ground truth):
- state = **open**
- 创建时间 = 2024-04-03(至 2026-06-14 已 **2 年多无 fix 无 workaround**)
- reactions_total = **0**
- comments_count = **1**(reporter 自己 ivan-enzhaev 于 2024-04-04 追加 Three.js 复现)
- labels = **(无)**
- 关联 PR / branch = **None yet**

**问题表现**(原文摘录):
> "It works perfectly smooth in the browser ... when I build my example to EXE for Window 10 it is very choppy"
> "The animation becomes smooth if I make the canvas fixed rather than stretching it across the entire window"
> "when I resize the window with the mouse, the animation stops being smooth and becomes jerky"

reporter 同步附了 Three.js 复现(说明**不是 Live2D / pixi 特定**,是 Win + Tauri + canvas 拉满窗 + rAF 的通用现象)。Three.js / requestAnimationFrame 在浏览器流畅,Tauri Win build EXE 卡顿——根因不在用户代码。

**关键变量缺失**(高度影响调研价值):
- **未提 transparent / alwaysOnTop / 整屏 setBounds**——原报告是普通窗 + canvas 拉满,不涉及我们的桌宠 overlay 形态
- 未提 Tauri 版本 / wry 版本 / WebView2 runtime 版本
- 未提显示器规格 / DPI / 刷新率
- 未提 GPU 加速设置

**评估**:
- 2 年 open + 0 reactions + 单个 reporter 无共鸣 = **可能是特定环境问题**(显卡 driver / Win 10 + 老 Tauri / 老 WebView2 runtime),**不是 fundamental WebView2 / wry 缺陷**
- 但**"Win + Tauri + canvas 拉满 + rAF choppy"是已知反向证据**,跟桌宠场景"整屏 transparent NSPanel + PixiJS 拉满窗 + rAF" 同构性不能简单否定
- ADR 0004 §4.4 把 #9373 列为 Win 反向证据是合理的;但本 spike 调研显示**反向证据强度 = 单点 + 无共鸣 + 关键变量缺失**——真机 spike 应作为重点 A/B 验证项,但不构成 fundamental block

**结论**:
- **现有反向证据强度有限**,不构成 fundamental block
- **必须真机验**:在本项目环境(Tauri 2.11.2 / wry 0.55.1 / 较新 WebView2 / Win 11 + Win 10)下 A/B 复现 #9373;测试 transparent + 整屏 + canvas 拉满 + rAF 组合是否 choppy;如确复现,排查是否环境特定 vs WebView2 通用

### 5.5 §1.5 [wry#1691](https://github.com/tauri-apps/wry/issues/1691) + [WebView2Feedback#5536](https://github.com/MicrosoftEdge/WebView2Feedback/issues/5536) GDI 泄漏 · **critical caveat · 桌宠场景几乎是最坏受害**

**wry#1691 状态**:
- state = **closed**,state_reason = `completed`,closed_at = **2026-03-14**,closed_by = reporter johnsideserf 自己
- labels = `type: bug`, `platform: Windows`
- 4 个评论全是 reporter 自己充分诊断 → close as completed + 移交上游

**Root cause 演化**(reporter 4 条评论自述):
1. 初判:WebView2 windows leak GDI objects when mouse click triggers window composition change(GlazeWM workspace switch)
2. 排除应用代码:Rust `GetGuiResources` 增量埋点,icon bitmap / 事件处理 / provider emission 三处 GDI delta = 零
3. **触发条件升级**(2026-03-14 12:29):
   > "the GDI leak is triggered by **any mouse movement over the WebView2 surface**. No clicks, CSS hover styles, transitions, or window composition changes are needed."
4. **WRY-level fix 全部失败**(2026-03-14 16:30):
   - `disable-gpu-compositing` / `disable-features=RemoveRedirectionBitmap`:无效
   - 子类化 WebView2 child windows 节流 `WM_MOUSEMOVE`:无效
   - 线程级 `WH_MOUSE` hook:无效
   - `pointer-events: none`:无效(WebView2 仍在 OS 层接收 `WM_MOUSEMOVE`)
   - `SetIsVisible(false/true)` toggle 强制 surface recreation:**反而使更糟**(30 秒 ~6000 个 vs ~2000 个)
   - Deferred `NotifyParentWindowPositionChanged`:无效
   - 关键原文:
     > "WebView2 routes mouse input through DirectComposition, bypassing the Win32 message queue. Thread hooks, window subclassing, and message filtering cannot intercept it."
5. 同时移交上游 → 关闭 wry#1691

**漏速 + 触发底线**(reporter 实测):
- 鼠标静态悬停静态文本(无 CSS / JS / hover): 30 秒内 GDI 从 42 → 2268(**+2226,约 75/秒**)
- 鼠标静止: 30+ 分钟稳定 30-36 GDI
- Win 每进程 GDI 上限 = 10,000,**漏速 75/秒下约 2 分钟耗尽**(reporter 自报"1-2 小时正常使用后光标卡顿 / UAC 对话框延迟")
- 复现环境:**Win 11 build 26200 + WebView2 146.0.3856.59**
- **不复现环境**:Win 10 + WebView2 145(WebView2Feedback#5536 评论一名用户测试)

**上游 WebView2Feedback#5536 状态**(GitHub WebFetch ground truth):
- state = **open**
- assignee = **venky8951**(Microsoft 内部分流)
- labels / type / milestone = **(无)**
- **无 Microsoft 公开回复 / bug 确认 / fix ETA / 官方 workaround**
- 提交日期 = 2026-03-14(从 wry#1691 close 当天上抛)

**对桌宠场景的影响评估**:
- ADR 0004 路线 A 在 Win 端拟走"整屏 NSPanel-like(普通 Window + `WS_EX_TRANSPARENT | WS_EX_LAYERED` + alwaysOnTop)+ 60Hz Rust cursor polling + setIgnoreCursorEvents toggle"
- 这意味着 **cursor 频繁穿透 WebView2 surface**(整屏覆盖整个屏幕 = 用户任何鼠标移动都经过 webview surface)
- 几乎**完美匹配** #1691 触发条件"any mouse movement over the WebView2 surface" → **桌宠场景受害程度可能高于 Zebar(reporter 的实测场景,taskbar-style widget 只占屏幕边缘小片区域,reporter 已经报 1-2 小时光标卡顿)**

**Caveat 强度**:
- **upstream block + 无 workaround + WRY-level 全部失败** = 短期内无技术解
- **但仅在 Win 11 + 较新 WebView2 复现**(Win 10 / WebView2 145 不复现)= 环境特定,**OS / WebView2 升级可能解决**
- **不构成 fundamental block**(产品当前用户大部分仍在 Win 10 / 旧 WebView2)但 **Win 11 + 新 WebView2 用户体验有显著退化风险**

**结论**:
- **critical caveat**:本 spike pass-with-caveats 的核心原因
- 真机 spike 必须重点验:
  - Win 11 build 26200 + WebView2 146 实际漏速(短窗 vs 整屏 / cursor 移动频率 / 24h 长跑稳定性)
  - Win 10 + 不同 WebView2 runtime 是否同样不复现
  - 是否有产品级 mitigation(如 cursor 移出窗 N 秒后主动 hide webview / 定期 restart)
- 长期跟 WebView2Feedback#5536 status,如 6 个月内 Microsoft 无 fix → ADR 0004 §6 反转条件 3 可能触发

### 5.6 §1.6 业界 Win 整屏 overlay 先例 · **关键 finding · Tauri 业界公开实证空白 / Electron 业界已实证**

**用户 declare 阶段 pushback 的点的调研验证结果**:

#### 5.6.1 OLV(Electron · 整屏 overlay 已实证 ✓)

WebFetch OLV `src/main/window-manager.ts` raw 源码,Win 端处理:

- **跟 macOS 共用整屏 overlay 路径**——同 `setBounds({ x: minX, y: minY, width: combinedWidth, height: combinedHeight })` monitors union
- 全文 `process.platform === 'win32'` **只出现 1 次**,仅用于 icon 文件选择(`.ico` vs `.png`)
- 其他平台分支都是 `isMac` 二元判断,**Win 走 else 分支 = 跟 macOS 共用整屏路径**
- 注释原文:
  > "Calculate the bounding rectangle that covers all connected displays. / This allows the transparent pet-mode window to span across monitors, so the avatar can be dragged freely between them."
- 关键 API:`setBounds` / `setAlwaysOnTop(true, 'screen-saver')` / `setBackgroundColor('#00000000')` / `setSkipTaskbar(true)` / `setIgnoreMouseEvents(true, { forward: true })`
- 注:`{ forward: true }` 是 Electron 特有 feature,Tauri tao 三端都不 forward([`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §5.1 已确认)

**意义**:OLV 是 **Win 整屏 transparent overlay + Cubism Web SDK + WebGL 已跑通的 Electron 业界实证**。但 Electron ≠ Tauri,本 spike 不蕴含 Tauri 同条件下也跑通——只是侧证 **Win API 层(setBounds / transparent / alwaysOnTop / setIgnoreMouseEvents)可承载整屏 overlay 形态**。

#### 5.6.2 Copiwaifu(Tauri 同栈 · 固定窗 ✗ 整屏)

WebFetch `src-tauri/tauri.conf.json` 实证:

- `width: 400` / `height: 760` 固定;`minWidth: 240` / `minHeight: 456` / `maxWidth: 560` / `maxHeight: 1064`
- `resizable: false` / `transparent: true` / `backgroundColor: "#00000000"` / `decorations: false` / `shadow: false` / `fullscreen: false` / `skipTaskbar: true` / `alwaysOnTop: true` / `visibleOnAllWorkspaces: true`
- `macOSPrivateApi: true`(顶层)
- **没有 Windows / Linux / macOS 平台特定 cfg 段**——所有平台共用同一份 `windows[0]` 定义
- bundle 图标含 `.ico` / `.icns`

**结论**:Copiwaifu = Tauri 2 + Vue 3 + PixiJS + easy-live2d **同栈最对标桌宠**,**走小固定窗 + canvas aspect-fit**,**不走整屏 setBounds**。

#### 5.6.3 BongoCat(Tauri 同栈 · 固定窗 ✗ 整屏 · 21.4k stars)

WebFetch `master` branch `src-tauri/tauri.conf.json` 实证:

- 主窗 label = "main": `shadow: false / alwaysOnTop: true / transparent: true / decorations: false / acceptFirstMouse: true / skipTaskbar: true / maximizable: false`
- **未定义 width / height / fullscreen / resizable** → 走 Tauri 默认值(典型小固定窗,具体默认值待真机验)
- `macOSPrivateApi: true`(顶层)
- **没有 Windows 平台特定 cfg**(无 `app.windows[0].windows` / `windowsExtensions`)
- 第 2 窗 label = "preference" 是设置窗(`visible: false / minWidth: 800 / minHeight: 600 / titleBarStyle: "Overlay"`)——跟桌宠 overlay 形态正交

**结论**:BongoCat = 21.4k stars Tauri 桌宠,**也走小固定窗**(transparent + alwaysOnTop + decorations:false 三件套 + 默认尺寸),**不走整屏 setBounds**。这反向印证 `industry-standard-tauri-nspanel.md` §2.2 当时引的 BongoCat README 没看错——只是当时没明示"它不走整屏"。

#### 5.6.4 manasight 博文(Tauri · 不可知)

WebFetch 博文 Win 部分(MTG Arena overlay)关键数据:

- **唯一明确数据**:"On Windows 11: **14 MB RAM and under 1% CPU.**"
- **窗口尺寸 / 形态完全没披露**:固定窗 / 跟随游戏窗 / 整屏 monitors union——博文不答
- **click-through 实现底层**:作者只说"a Rust loop polls cursor position at ~60fps and toggles `setIgnoreCursorEvents`",**底层 `WS_EX_TRANSPARENT | WS_EX_LAYERED` 等具体 Win32 flag 实现刻意未公开**
- **focus 抢占问题**:作者承认"Clicking a UI panel pulled focus from Arena. Required platform-specific window flag adjustments." —— "window flag adjustments"暗示动了 Win32 ex-style,但**没指出具体 flag**
- 自报 Win 端"didn't happen"问题:ghost titlebar 伪影 / 任务栏 z-order

**评估**:作者明显有 Win32 / Direct2D 背景("worked on the Microsoft team that originally built Direct2D"),但**刻意没公开 Win overlay 底层窗口样式 + hit-test 代码**。**14MB 不能用于支持 "manasight 跑通了 Tauri Win 整屏 overlay"——因为窗口形态本身未公开**。

#### 5.6.5 judehek/tauri-plugin-overlay(Tauri · Windows only · DLL injection · 不同场景)

WebFetch README:
- 定位 = "Tauri 2 plugin wrapping `overlay-engine` — render WebView2 panel iframes into a running Windows game via DLL injection + composition."
- 关键特性:**注入到目标游戏进程**(不是独立 Tauri 窗口浮在游戏上),通过 DLL + composition
- **Windows only**(macOS / Linux 不支持)
- 0 stars / **No releases published** / 仍属早期
- 跟 Tauri 原生 transparent + setIgnoreCursorEvents 三件套**完全不同路径**

**评估**:这是 ADR 0004 §6 反转条件 3 选项 a "切 Hyprnote / overlay-engine 路线"的**具体技术参考**,但:
- **场景不契合**:它是为"游戏内 overlay"做的(浮在游戏窗口里),不是独立桌宠
- **DLL injection 反作弊 / 多 GPU 后端适配 / Microsoft Defender 误报等附带风险高**
- **跨平台代价巨大**:macOS-Win 实现完全异构,违反 ADR 0004 §2 第 8 项"统一渲染管线"目标

#### 5.6.6 综合 finding(用户 pushback 验证后的精确陈述)

| 业界 | 形态 | 实证强度 |
|---|---|---|
| OLV(Electron) | **Win 整屏 overlay** 已实证 | strong(源码可直读,跟 macOS 共用 setBounds monitors union 整屏路径) |
| Copiwaifu(Tauri 2 同栈) | **Win 小固定窗**(400×760) | strong(tauri.conf.json 实证) |
| BongoCat(Tauri 2,21.4k stars) | **Win 小固定窗**(默认尺寸) | strong(tauri.conf.json 实证) |
| manasight(Tauri 2,MTG overlay) | **不可知**(博文不公开形态) | weak(14MB <1% CPU 数据 + click-through 实现 60Hz Rust polling 已公开,但窗口形态没公开) |
| judehek/tauri-plugin-overlay | **DLL injection 注入游戏进程** | early-stage(0 stars / No releases / 场景不契合桌宠) |

**关键 finding**:**Tauri 业界 Win 整屏 overlay 公开实证空白**——OLV 是 Electron 不是 Tauri;Copiwaifu / BongoCat 是 Tauri 同栈但走固定窗;manasight 不公开 implementation。

这跟 ADR 0004 §4.4 写的"业界 Tauri Win 桌宠都是固定窗"在 Copiwaifu / BongoCat 上得到实证,但 manasight 不确证;**Electron 业界(OLV)Win 整屏可工程化是已知 strong 实证**,可作为 Tauri Win 真机 spike 的最直接对照参考。

### 5.7 哪些必须真机才能验(本 spike 给后续真机 spike 的接力清单)

文档调研覆盖度梳理后,**必须真机才能验**的项:

| 子问题 | 真机必验项 | 复现 / 验证关键 |
|---|---|---|
| §1.1 整屏 setBounds | 多屏 + 不同 DPR + Win 10 / 11 + 不同 GPU 下 setBounds monitors union 实际行为 | 算 `available_monitors` 并集 → `set_position` + `set_size`,对照 OLV Electron Win 实证路径精准复现 |
| §1.2 setIgnoreCursorEvents | 60Hz Rust polling 频繁 toggle 在整屏 layered + transparent 下 cursor 流畅度 / GDI 累积 / DWM 重合成开销 | `GetGuiResources` 24h 长跑 / `powermetrics` 等价 Win tool 实测 GPU/CPU 占用 |
| §1.3 PixiJS rAF 60fps | Win 11 + Win 10 + 新旧 WebView2 + 整屏 + transparent + DWM blur-behind + PixiJS rAF 实测 fps | Performance API 算 fps p50 / p95 / 最低值 30+ 秒稳态 ≥ 55fps;对照 macOS spike §7.6 段 B 30fps 数据 |
| §1.4 tauri#9373 复现 | 本项目环境(Tauri 2.11.2 / wry 0.55.1 / 较新 WebView2 / Win 11 + Win 10)下 transparent + 整屏 + canvas 拉满 + rAF A/B 复现 | 验证关键变量(transparent / alwaysOnTop / 整屏 / WebView2 版本)对 choppy 现象的影响 |
| §1.5 wry#1691 GDI 泄漏 | Win 11 build 26200 + WebView2 146 漏速实测 + Win 10 / 旧 WebView2 是否不复现 + 24h 长跑稳定性 | `GetGuiResources` 1Hz 采样,记录 cursor 移动频率与漏速的关系;评估产品级 mitigation 可行性 |
| §1.6 业界 Win 整屏先例 | Tauri Win 端到端 spike 跑通后,本项目本身就是首个 Tauri 业界 Win 整屏 overlay 公开实证 data point | 跟 macOS 段 B"业界从未公开实证过的 NSPanel + 整屏 transparent + WebGL 组合在 Tauri macOS 跑通"产出对齐 |

### 5.8 没真验 / 转交(明确边界)

以下沿 §2.3 + §4 边界,本 spike 不真验:
- **多屏 + 不同 DPR 整屏 setBounds 实际行为**(单源开发机 / 单显示器配置限制)
- **Win 10 / Win 11 跨版本 WebView2 runtime 行为差异**(留给真机 spike 跨版本设备覆盖)
- **wry Win `webview2/mod.rs` 完整源码细读**(WebFetch 直读 wry/dev/src/webview2/mod.rs 触发 cyber-safeguard 被拦截,改走 tao Win window.rs / window_state.rs + Tauri 官方文档 + Tauri community 实证间接推导,**结论:wry WebView2 透明实现细节未读穿,但 tao 层 + transparent DWM blur-behind region 路径足以推断整体可行性**)
- **manasight 实际窗口形态**(作者刻意未公开)
- **judehek/tauri-plugin-overlay 端到端可工程化**(场景不契合桌宠,仅作 ADR 0004 §6 反转条件 3 选项 a 参考)
- **WebView2Feedback#5536 后续 Microsoft 回复**(本 spike 截止 2026-06-14;长期跟踪转交后续 issue grooming)

---

## 6. 下一步

### 6.1 整体结论:done(pass-with-caveats)

- **pass**:§1 6 子问题每个都有"已知答案"或"显式转交真机"标注 + 没读到不可绕反向证据(文档层不存在 fundamental block 证据)
- **caveat**:[wry#1691](https://github.com/tauri-apps/wry/issues/1691) + 上游 [WebView2Feedback#5536](https://github.com/MicrosoftEdge/WebView2Feedback/issues/5536) GDI 泄漏对桌宠 cursor 持续穿透场景几乎是最坏受害;Microsoft 尚无公开回复 / workaround;WRY-level fix 全部失败;但仅在 Win 11 build 26200 + WebView2 146.0.3856.59 复现(Win 10 + WebView2 145 不复现)→ 环境特定 + OS/WebView2 升级可能解决,不构成 fundamental block
- **不触发** [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) §6 反转条件 3——路径选项 a/b/c 应在真机 spike 撞不可绕证据时再启用,本 spike 文档层未撞到

### 6.2 反馈:Tauri 业界 Win 整屏 overlay 公开实证空白(用户 pushback 的调研验证结论)

declare 阶段我假设"业界 Tauri Win 桌宠都是固定窗",用户 pushback 指出该 assumption 需要调研验证。本 spike 调研得到的更精确陈述:

- **Tauri 业界 Win 整屏 overlay 公开实证空白**(Copiwaifu / BongoCat 明确固定窗;manasight 不公开形态)
- **Electron 业界 Win 整屏 overlay 已实证**(OLV 同 setBounds monitors union 路径,Live2D Cubism Web SDK + WebGL 跑通)
- **Tauri 业界做 Win overlay 的项目走"固定窗 transparent + alwaysOnTop + skipTaskbar"是主流**(Copiwaifu 实证 + BongoCat 实证)
- **本项目真机 spike 跑通后就是首个 Tauri 业界 Win 整屏 overlay 公开 data point**(跟 macOS NSPanel + 整屏 + WebGL "业界从未公开实证过的组合" 同等地位)

### 6.3 反馈:ADR 0004 / `industry-standard-tauri-nspanel.md` 后续修订建议

按 [docs-discipline](../../../.cursor/rules/) 铁律 1(决策不就地推翻),本 spike **不修改** ADR 0004 / `industry-standard-tauri-nspanel.md` 正文。但下列陈述在后续 ADR 或文档修订时建议精确化:

- **ADR 0004 §4.4** 当前写"Win 端整屏 overlay + WebGL 业界未公开实证(业界 Tauri Win 桌宠都是固定窗)"——本 spike 实证:
  - "**Tauri** 业界 Win 桌宠公开实证全是固定窗"准确(Copiwaifu / BongoCat)
  - 但"业界 Tauri Win 桌宠都是固定窗"在 manasight 上不确证
  - **Electron 业界 Win 整屏 overlay 是已实证**(OLV strong 实证)
- **`industry-standard-tauri-nspanel.md` §7.1** 当前写"业界 Tauri Win 应用做 overlay 全是固定窗,没人 setBounds 整屏"——同上精确化建议
- 本 spike 作为这两条陈述的 ground truth,后续 ADR / industry-standard 修订时引用即可

### 6.4 Win 端到端真机 spike 接力清单(**本 spike 的最大产出**)

给后续(待真做 Live2D 接入需求 / 拿到 Win 开发机)Win 端到端真机 spike 一份接力 baseline。沿 macOS spike [`spike-tauri-nspanel-pet-window.md`](./spike-tauri-nspanel-pet-window.md) §2 / §3 同样规范展开。

#### 6.4.0 Win 机 prerequisites(开跑前一次性装)

| 项 | 安装 | 说明 |
|---|---|---|
| Rust toolchain | [`rustup-init.exe`](https://rustup.rs) | 选 MSVC 工具链(Tauri Win 默认走 MSVC ABI,不要选 GNU) |
| Visual Studio Build Tools | [VS Installer](https://visualstudio.microsoft.com/visual-cpp-build-tools/) 选 "Desktop development with C++" workload | Rust MSVC ABI 必须;Tauri 2 Win prerequisite |
| Microsoft Edge WebView2 Runtime | Win 11 默认自带;Win 10 较老版本到 [Microsoft 官方](https://developer.microsoft.com/microsoft-edge/webview2/) 装 Evergreen Bootstrapper | Tauri Win 渲染层依赖;**spike 需要锚定 runtime 版本**(对照 [wry#1691](https://github.com/tauri-apps/wry/issues/1691) 复现环境 Win 11 build 26200 + WebView2 146.0.3856.59 vs Win 10 + WebView2 145 不复现) |
| Node 22+ + pnpm | [nvm-windows](https://github.com/coreybutler/nvm-windows) 或 [fnm](https://github.com/Schniz/fnm) 装 node ≥ 22;`corepack enable pnpm` 启用 pnpm | 沿仓库 `scripts/frontend/_ensure-node.ps1` 同样约束 |
| uv | `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` | 仓库 setup 脚本前置 |
| Git LFS(可选) | [git-lfs.com](https://git-lfs.com) | 真做 Live2D 接入需求时需要;本 spike 不用 |

工具齐后跑仓库一键初始化:

```powershell
.\scripts\setup\run.ps1
```

setup 脚本做 4 件事:`uv sync` 装后端 Python 依赖 / `.env` 不存在则从 `.env.example` 复制 / `pnpm install` 装前端依赖(best-effort,工具齐才跑) / 检查 cargo 存在(不装,只提示)。

**首次跑完后,后续 git pull 拿到新依赖怎么办**:

- **锁文件变化**(`uv.lock` / `pnpm-lock.yaml`):再跑 `.\scripts\setup\run.ps1` 即可(uv sync + pnpm install 都幂等,按新 lock 升降级)
- **Cargo dep 新增**(本 spike 大概率 `Cargo.toml` 加 Win cfg dep 如 `windows` crate):setup 脚本不显式 `cargo fetch`,但首次 `pnpm tauri dev` 时 cargo 会按 `Cargo.toml` + `Cargo.lock` 自动拉,等同解决
- **工具版本本身**(Rust / node / pnpm / uv):**setup 不升级**。需要时手动:`rustup update` / `corepack prepare pnpm@latest --activate` / nvm `install <ver>` / `uv self update`
- **`.env` 新增字段**:setup 已存在则跳过。手动对照 `frontend/.env.example` / 根 `.env.example` 看是否有新 key 要补

#### 6.4.1 实施 baseline · 骨架已落 `spike/tauri-win-overlay` 分支

**实施载体**:**长期分支** `spike/tauri-win-overlay`(基于 main,push 到 origin),**不 merge 回 main**(沿 ADR 0004 §6 反转条件 3 "Win spike pass 才走产品")。Win 跑出 done(pass / pass-with-caveats / fail)结论后,分支可 archive(留作未来真做 Live2D 需求时引用,跟 macOS spike §5.5 可借鉴代码清单同等地位)。

**骨架已在 mac 端落好,Win 工程师直接拉跑**(无需自己组装代码):

```powershell
git fetch origin
git checkout spike/tauri-win-overlay
git pull
.\scripts\setup\run.ps1
cd frontend
pnpm tauri dev
# pet 窗自动加载 pet.html;手动改 URL 加 ?spike=win-overlay 触发 spike 模式
# 例如 devtools 改 location.search='?spike=win-overlay' + reload,
# 或者 vite dev URL 直接访问 http://localhost:1420/pet.html?spike=win-overlay
```

**骨架改了哪些文件**(mac 端 cargo check + pnpm install 已验通过 cross-platform 部分):

| 文件 | 改动 | mac 端验证状态 |
|---|---|---|
| `frontend/src-tauri/src/lib.rs` | 加 `apply_pet_overlay_fullscreen(window)` cross-platform 函数(monitors union → set_position + set_size,沿 macOS spike §5.5.5 同款算法) + `spike_win_log_perf(msg)` Rust command(让前端 1Hz fps HUD 数据回 invoke 打 Rust log) + setup hook `#[cfg(target_os = "windows")]` gate 调用(mac dev 保持现 240×320 形态,Win 才整屏) | ✅ `cargo check` mac 端通过(cross-platform 部分);Win cfg gate 段只一行调用 cross-platform fn,语法极简 |
| `frontend/src/pages/pet/PetSpikeWinOverlayApp.tsx` | 新文件 · PIXI v8 整屏 demo(蓝色 80×80 旋转方块 + fps HUD + 1Hz invoke `spike_win_log_perf`),沿 macOS spike §5.5.7 同款 StrictMode 双 mount cleanup pattern + PIXI destroy 顺序 | ✅ 跨平台 |
| `frontend/src/pages/pet/App.tsx` | 顶部加 `isWinOverlaySpike()` URL param dispatch:`?spike=win-overlay` → `PetSpikeWinOverlayApp`,否则 `PetAppDefault`(原 016 形态 wrap 进新函数,语义零变化) | ✅ 跨平台 |
| `frontend/package.json` + `pnpm-lock.yaml` | 加 `pixi.js ^8.6.0`(实装 8.19.0) | ✅ `pnpm install` mac 端通过 |
| `frontend/src-tauri/tauri.conf.json` | **不动**(016 已配 pet 窗 transparent + alwaysOnTop + skipTaskbar + decorations:false + shadow:false,直接被 setup hook setBounds 覆盖整屏) | ✅ |

**Win 工程师需要做的**(只有 Win 才能干的):

1. **跑 + 看 Rust 终端 fps log**:启动后看 Tauri 终端的 `spike-win-perf: fps=XX min=XX p50=XXms p95=XXms n=XXX` 行,**30+ 秒稳态 ≥ 55fps** 判 §3.1 段 B(整屏)通过
2. **24h 长跑测 GDI**:Win 11 + WebView2 146 环境精准对照 wry#1691 → 用 `GetGuiResources(GetCurrentProcess(), GR_GDIOBJECTS)`(PowerShell 调 Win32 或 Tauri 内插桩)1Hz 采样,记录漏速;cursor idle vs 持续 movement 两种场景对照
3. **#9373 复现**:本 demo = 整屏 + canvas 拉满 + rAF,正是 #9373 触发模式;Win 10 + 较老 WebView2 / 不同 GPU driver A/B 验证
4. **实测数据回填 spike doc §5**:把 Win 段实测 fps / GDI 漏速 / OS+WebView2 版本组合表 push 回 `spike/tauri-win-overlay` 分支,本 spike 状态从 `done(pass-with-caveats · 文档调研)` 进化为 `done(pass / pass-with-caveats / fail · 真机端到端)`

**骨架代码的"mac 上无法验证"硬 gap**(留 Win 工程师撞):

- `#[cfg(target_os = "windows")]` gate 段在 mac 上不编(死代码),语法错 cargo check 不报。本骨架 cfg gate 段只一行 `apply_pet_overlay_fullscreen(&pet);`(调 cross-platform fn),撞错概率极低
- `WS_EX_TRANSPARENT | WS_EX_LAYERED` 是否如预期由 tao 在 `set_ignore_cursor_events(true)` 时自动加(本 spike doc §5.2 已读穿源码确认),但**整屏 layered window + DWM blur-behind region + WebView2 渲染**完整组合是否如预期,只有 Win 真跑能验
- WebView2 runtime 版本检测(对照 wry#1691 复现 / 不复现环境):需要 Win 工程师在 spike doc §5 实测段补"测试环境精准信息"

**复现 Configuration 精准对照表**(已落代码,对照 spike doc §5.7 表):

| 维度 | 本项目落地 | 在骨架哪里 |
|---|---|---|
| 窗口类型 | 普通 Window(Win 无 NSPanel 等价) | `tauri.conf.json` `label: "pet"` 普通窗 |
| ex-style | tao 自动给 `WS_EX_TRANSPARENT \| WS_EX_LAYERED`(`set_ignore_cursor_events`) | 现有 `usePetPassthrough.ts` cursor passthrough 路径自动触发(spike 模式不挂 `usePetPassthrough`,需 Win 工程师单独验证或确认是否本 spike 阶段需要 cursor passthrough) |
| 透明 | `tauri.conf.json` `transparent: true`(Win 走 DWM blur-behind region) | 016 已有 |
| level | `alwaysOnTop: true`(Win 用 `HWND_TOPMOST`) | 016 已有 |
| 窗尺寸 | Rust 侧 `available_monitors()` union → `set_position` + `set_size` | `lib.rs::apply_pet_overlay_fullscreen()`(spike 骨架新加) |
| cursor passthrough | Rust 60Hz polling cursor + setIgnoreCursorEvents toggle | 现有 `spawn_cursor_feed`(spike 模式下整屏 = cursor 全部经过 webview surface,**建议 Win 工程师先确认 cursor 不穿透是否影响 fps 验测**,如影响,DOM `data-hit` 标 fps HUD 区即可) |
| 渲染 | PixiJS v8 + WebGL(同 macOS spike §5.5.7) | `PetSpikeWinOverlayApp.tsx`(spike 骨架新加) |

**通过条件**(沿 macOS spike §3 同样规范):

- **段 A · 固定窗 transparent + alwaysOnTop**:**直接跳过**(本骨架 setup hook Win cfg-gate 直接走整屏,不验固定窗 fallback;如需验证,Win 工程师临时 comment 掉 `apply_pet_overlay_fullscreen` 调用即可)
- **段 B · 整屏 transparent setBounds monitors union + PixiJS**:**本骨架直接验**;rAF 稳态 ≥ 55fps + GDI 漏速 < 10/秒(对照 #1691 报告 75/秒红线)
- **段 C · 24h 长跑稳定性**:GDI 上限不耗尽 + 无系统级 cursor 卡顿(对照 #1691 reporter "1-2 小时光标卡顿"红线)

**调试退路**:如段 B fail,**Win 工程师 push 回 fail 结论到 spike doc § 5 + spike 状态升级**;触发 ADR 0004 §6 反转条件 3,本分支 archive,新立 ADR 处理 Win fallback 方案(选项 a/b/c)。骨架代码 mac 端无影响(setup hook `#[cfg(target_os = "windows")]` gate 让 mac dev 保持现 240×320 形态)。

**设备覆盖建议**:Win 11(对照 #1691 复现环境 + 走整屏 + Win 11 Composition 行为)+ Win 10(对照 #9373 + 老 WebView2 不复现 #1691 的环境)+ 至少 1 个 Intel iGPU + 1 个 NVIDIA / AMD dGPU(防 GPU driver 特定问题)。

### 6.5 反转触发阈值(给真机 spike 用)

如真机 spike 撞下列任一不可绕证据 → 触发 [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) §6 反转条件 3,需立新 ADR 覆盖:

1. **PixiJS rAF 稳态 < 30fps** 在 Win Chromium WebView2 整屏 + transparent 下 + 无 workaround(产品级阻塞)
2. **#9373 在本项目环境复现** + 关键变量定位为整屏 transparent + alwaysOnTop + Tauri 特定组合 + 无 workaround
3. **#1691 GDI 漏速 > 50/秒**(reporter 报 75/秒)在桌宠 idle 场景(cursor 不主动移动)+ Microsoft / WebView2 6 个月内无 fix + 产品级 mitigation 不可行
4. **setIgnoreCursorEvents 60Hz toggle** 在整屏 layered + transparent 下导致 cursor 抖动 / 系统级响应延迟可观察 + 降频(<10Hz)后桌宠 hit-test 体验不可接受

三选一应对(沿 ADR 0004 §6 反转条件 3):
- 切 judehek/tauri-plugin-overlay 路线(macOS-Win 异构 + 反作弊 / 多 GPU driver 风险)
- 接受 Win 端走小固定窗 fallback(段 A pass + 段 B fail 即可启用,产品一致性受损)
- 重新评估整体路线 A 是否值得保留(如 Win 是主场景)

### 6.6 长期跟踪项

后续 session / 后续真机 spike 应定期(每 3-6 个月)回看下列上游状态:

- [WebView2Feedback#5536](https://github.com/MicrosoftEdge/WebView2Feedback/issues/5536):Microsoft 是否承认 + fix ETA + workaround
- [tauri#9373](https://github.com/tauri-apps/tauri/issues/9373):是否有 Tauri 团队 / 社区 workaround(2 年无响应已是反向信号)
- WebView2 runtime 主线版本 changelog:GDI 泄漏 fix 是否上线

如 WebView2Feedback#5536 6 个月内无 Microsoft 回复或 fix → 本 spike caveat 升级,真机 spike 启动前应优先用 #1691 24h 长跑作为门槛验证。
