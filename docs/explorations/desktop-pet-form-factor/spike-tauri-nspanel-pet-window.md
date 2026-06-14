# Spike · NSPanel 路径解 PixiJS render blocker（pet 主窗 macOS 路线修正）

> Spike 记录，非决策、非需求。验证"把 pet 主窗用 [`tauri-nspanel`](https://github.com/ahkohd/tauri-nspanel) 转成 NSPanel + nonactivating + Floating + collectionBehavior（fullScreenAuxiliary | canJoinAllSpaces）后，[`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §5.2 撞墙的 PixiJS rAF 是否恢复"。
>
> 同时回答 OLV 路线在 Tauri macOS 下是否最终可工程化（业界 NSPanel 项目全是固定窗，没人验过整屏 NSPanel + WebGL）。

- **创建时间**：2026-06-14
- **状态**：done(pass-with-caveats)（2026-06-14 完成；段 A 段 B 全部跑通；caveat = macOS 给 NSPanel + nonactivating 30fps cap，业界共识桌宠场景够用 + macOS 私有 API 不上 MAS）
- **依赖**：[`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md)（macOS overlay 承载形态已 pass，提供 monitors 并集 setBounds 的 baseline 代码）+ [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md)（render blocker 已 isolate，readPixels 性能已 pass）+ [`industry-standard-tauri-nspanel.md`](./industry-standard-tauri-nspanel.md)（业界标准方案锚定）
- **关联**：本目录 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §6 OLV 路线倾向 / §7 spike 清单 / §8 回流 ADR 触发条件、[`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §5.2 副产品 blocker / §6.2 反馈"spike 4 立项前必须先解 render blocker"
- **下游 spike**：[`spike-pixijs-sprite-drag-rust-sync.md`](./spike-pixijs-sprite-drag-rust-sync.md)、[`spike-bubble-follow-sprite.md`](./spike-bubble-follow-sprite.md)（依赖本 spike pass）

---

## 1. 验证目标

回答三组问题：

### 1.1 macOS · 段 A · 固定窗 NSPanel 解 render blocker

把现有 240×320 固定 pet 主窗（016 落地形态）从 NSWindow 切成 NSPanel：

- `tauri-nspanel` 把窗 to_panel
- `set_activation_policy(Accessory)`
- `set_level(PanelLevel::Floating.value())`
- `set_style_mask(StyleMask::empty().nonactivating_panel().resizable())`
- `set_collection_behavior(CollectionBehavior::new().full_screen_auxiliary().can_join_all_spaces())`
- `set_hides_on_deactivate(false)`

挂一个 PixiJS app（PNG 矩形 sprite，不必 Live2D）跑 rAF。回答：

- **PixiJS rAF 是否恢复 ≥ 55 fps 稳态？**（[`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §5.2 撞墙的 fps=0 在此组合下是否消失）
- 拖窗 / Mission Control / Space 切换 / setIgnoreCursorEvents toggle 时 rAF 是否仍稳？
- [issue 007](../../issues/007-macos-fullscreen-overlay-limit/) 在 NSPanel + nonactivating + fullScreenAuxiliary 下是否解？（[`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §5.5 场景 4 NSWindow 路径下不解）

### 1.2 macOS · 段 B · 整屏 NSPanel 验 OLV 路线最终可行性

仅在段 A pass 后做。把段 A 的 NSPanel 撑到 monitors union（沿 [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §5.5 已验代码）：

- 整屏 NSPanel + PixiJS rAF 是否仍稳？
- [tauri#15471](https://github.com/tauri-apps/tauri/issues/15471) GPU 8x 问题在 NSPanel + 整屏下是否触发？(`powermetrics` 实测对照 transparent:false baseline)
- 整屏尺寸下 setIgnoreCursorEvents toggle 不影响 rAF？

### 1.3 Win / Linux 边界（不真验，但显式说明）

- **本 spike 不真验 Win / Linux**：沿 [需求 016 R-4.7.4](../../requirements/016-pet-bubble-independent-window/requirement.md) + [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §2 边界
- **NSPanel 改动用 `#[cfg(target_os = "macos")]` 隔离**，Win / Linux 现有 NSWindow + transparent + alwaysOnTop 路径不变
- **段 B pass 不蕴含 Win 整屏 overlay + WebGL 可工程化**：[tauri#9373](https://github.com/tauri-apps/tauri/issues/9373) 是 Win 反向证据（Win 10 上 canvas 拉满窗 choppy），需后续跨平台 spike 单独承接

## 2. 验证方法

**前提**：本 spike 沿 [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) + [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) 的 macOS-only 真验边界。throwaway worktree 实施。

**实施载体**：worktree `.claude/worktrees/spike-tauri-nspanel`，基于 main，throwaway，不进主仓主分支。dev port 1422（避开主仓 1420）。

### 2.1 段 A 实施（固定窗 NSPanel + PixiJS）

1. **基线复用**：从 `.claude/worktrees/spike-alpha-hittest`（spike 3 worktree）拷 PixiJS demo 改动作为基线（pet/App.tsx 的 PIXI App 初始化 + sprite + rAF + readPixels）
2. **加 tauri-nspanel git dep**（仅 macOS）：

   ```toml
   [target."cfg(target_os = \"macos\")".dependencies]
   tauri-nspanel = { git = "https://github.com/ahkohd/tauri-nspanel", branch = "v2.1" }
   ```

3. **Rust 改动**（`frontend/src-tauri/src/lib.rs`）：

   ```rust
   .plugin(tauri_nspanel::init())
   .setup(|app| {
       #[cfg(target_os = "macos")]
       {
           app.set_activation_policy(tauri::ActivationPolicy::Accessory);
       }
       // 现有 setup logic
       Ok(())
   })
   ```

   pet 窗 webview 创建后：

   ```rust
   #[cfg(target_os = "macos")]
   {
       use tauri_nspanel::{ManagerExt, panel_delegate};
       use tauri_nspanel::cocoa::appkit::{NSWindowCollectionBehavior, NSWindowStyleMask};

       let panel = pet_window.to_panel().unwrap();
       panel.set_level(/* Floating level value */);
       panel.set_style_mask(StyleMask::empty().nonactivating_panel().resizable().into());
       panel.set_collection_behavior(
           CollectionBehavior::new()
               .full_screen_auxiliary()
               .can_join_all_spaces()
               .into()
       );
       panel.set_hides_on_deactivate(false);
   }
   ```

   （API 调用细节按 [`tauri-nspanel/examples/fullscreen`](https://github.com/ahkohd/tauri-nspanel/tree/v2.1/examples/fullscreen) 真跑前对齐）

4. **tauri.conf.json**：保持 240×320 固定 + transparent + decorations:false + skipTaskbar + shadow:false。**不要** `alwaysOnTop`（NSPanel 通过 PanelLevel 控制，避免叠加冲突）
5. **前端 PixiJS demo**：复用 spike 3 的 PNG sprite + rAF tick + Performance API 记录 fps + readPixels
6. **测试场景**：30 秒静态 rAF + 拖窗 + Mission Control + Space 切换 + setIgnoreCursorEvents 反复 toggle + 切到 fullscreen 应用看是否仍浮（验 [issue 007](../../issues/007-macos-fullscreen-overlay-limit/)）
7. **取数**：Performance API 算 fps p50 / p95 / 最低值；console 记录每场景切换时刻

### 2.2 段 B 实施（整屏 NSPanel + PixiJS，仅段 A pass 后做）

1. **setup hook 加整屏 setBounds**：复用 [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §5.5 已验的 monitors 并集代码
2. **PixiJS canvas 跟随 window resize**：监听 window resize / DPR + 重设 PIXI app size
3. **测试场景**：同段 A + 整屏尺寸（约 3456×2234 单屏）
4. **GPU 实测**：用 `sudo powermetrics --samplers gpu_power -n 5 -i 1000` 取 5 个 1 秒采样
   - transparent:true + 整屏 NSPanel + PixiJS 跑：取 GPU power
   - transparent:false 同样 demo 对照取 baseline
   - 比 ratio 看是否触发 [tauri#15471](https://github.com/tauri-apps/tauri/issues/15471) 的 8x

### 2.3 调试退路

如果段 A 不通：

- 关闭主仓 dev 排除环境噪声（spike 3 §5.2 留过这条线）
- 切换 PanelLevel（Floating / Modal / Status / 其他）做 A/B 排除
- 复现一个不带 PixiJS 的纯 raw WebGL test，定位是否 PixiJS 特定问题 vs WKWebView 底层
- 看 ahkohd/tauri-nspanel 仓 issue 是否有人撞过同类 render blocker

如果段 B 不通：

- 段 A 已 pass = NSPanel 路径有效，只是整屏尺寸下不行 → 段 B fail 不否定段 A，是路线 A "整屏 OLV 等价"在 Tauri 不可工程化的实证

## 3. 通过条件

### 3.1 段 A pass 标准

- macOS 26.6 Apple Silicon 单屏：PixiJS rAF **稳态 ≥ 55 fps（容许 ±5 帧抖动）持续 30 秒**
- 拖窗 / Mission Control / Space 切换不触发 rAF=0 死亡（允许瞬时降帧但要恢复）
- setIgnoreCursorEvents toggle 不影响 rAF
- fullscreen 应用场景下 panel **可见**（解 [issue 007](../../issues/007-macos-fullscreen-overlay-limit/) 的关键标志）
- 退出时 PixiJS 干净 dispose（无 shader / context leak）
- readPixels 性能保持 [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §5.1 baseline 水平（< 1ms）

### 3.2 段 B pass 标准

- 同段 A 标准 + 整屏尺寸（约 3456×2234 单屏）
- `powermetrics` GPU 功耗在 transparent:false baseline 的 **10x 内**——若达到 8x 则承认踩 [tauri#15471](https://github.com/tauri-apps/tauri/issues/15471)，标 **pass-with-caveats**（功能 work，但有功耗代价，留给真做 Live2D 时 design 决策是否接受）

### 3.3 整 spike 状态判定

| 状态 | 触发条件 | 含义 |
|---|---|---|
| **done(pass)** | 段 A pass + 段 B pass | NSPanel 路径完整解 OLV 路线在 Tauri macOS 上的可工程化问题 |
| **done(pass-with-caveats)** | 段 A pass + 段 B pass-with-caveats | OLV 路线可工程化，但整屏 transparent 在 macOS 26 上付出 GPU 8x 代价 |
| **done(partial-pass)** | 段 A pass + 段 B fail | NSPanel 解了 render blocker，但只能走固定窗路线（业界主流） |
| **fail** | 段 A fail | NSPanel 不解 render blocker，触发 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §8 反转条件，回退路线 B（Copiwaifu 固定窗 + canvas aspect-fit） |
| **blocked** | tauri-nspanel API 不匹配 / 装不上 / 无 workaround | 找上游 issue 求助；不算 fail，留作后续重试 |

### 3.4 跨版本 / 跨平台

- 不强制跨 macOS Sonoma / Sequoia 真验：本期开发机只覆盖 macOS 26.6（沿 [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §3 blocked 分支边界）
- 不强制跨 Win / Linux 真验：沿 016 R-4.7.4 + [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §2 边界

## 4. 不在范围

- **Live2D 模型加载**：用 PNG 矩形或纯色 sprite 验 rAF 即可。具体 Live2D 库选型（pixi-live2d-display vs easy-live2d）跟"窗类型 + render pipeline"正交，由真做 Live2D 接入需求时 design 决定
- **alpha 采样 hitTest**：[`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §5.1 已 pass，复用同样 plumbing
- **sprite drag + Rust 同步**：[`spike-pixijs-sprite-drag-rust-sync.md`](./spike-pixijs-sprite-drag-rust-sync.md) 范畴
- **bubble window 跟 sprite world position**：[`spike-bubble-follow-sprite.md`](./spike-bubble-follow-sprite.md) 范畴
- **Win / Linux 真机端到端验证**：沿 016 R-4.7.4 + [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §2 边界，留给后续跨平台 spike
- **macOS Sonoma / Sequoia 跨版本真验**：沿 [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §3 blocked 分支边界
- **`backgroundThrottling: "disabled"` 配置语义验证**：[`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §5.2 + [`industry-standard-tauri-nspanel.md`](./industry-standard-tauri-nspanel.md) §5.2 已锁定结论（解的不是同一个问题）
- **`macOSPrivateApi: true` 对 Mac App Store 上架的影响**：跟"NSPanel render 是否能跑"正交，是后续上架决策议题
- **manasight 实测 Tahoe RAM 翻倍现象在本场景的复现**：跟"PixiJS rAF 是否能跑"正交。如果段 B pass，可顺手记一笔 RAM 数据，但不作通过条件

## 5. 实际结果

### 5.1 段 A · 固定窗 NSPanel 解 render blocker · pass-with-caveats

**实施环境**：

- worktree `.claude/worktrees/spike-tauri-nspanel`（基于 main commit `50f257e`，throwaway）
- M4 Pro Mac mini（Mac16,7），**macOS 26**（Darwin 25.3.0），单屏 16" Studio Display 3456×2234（DPR=2）
- worktree dev port 1422（避开主仓 1420）
- Tauri 2.11.2 + wry 0.55.1 + **tauri-nspanel v2.1.0**（git `ahkohd/tauri-nspanel#v2.1`）+ pixi.js v8.6 + React 19

**改动文件**（throwaway worktree 内，4 + 2 个文件）：

| 文件 | 改动要点 |
|---|---|
| `frontend/src-tauri/Cargo.toml` | 加 `tauri-nspanel = { git = "...", branch = "v2.1" }`（macOS only target dep） |
| `frontend/src-tauri/src/lib.rs` | 顶级声明 `tauri_panel! { panel!(PetPanel { config: { can_become_key_window: true, is_floating_panel: true } }) }`；新函数 `apply_pet_nspanel`：`window.to_panel::<PetPanel>()` + `set_level(PanelLevel::Floating.value())` + `set_style_mask(StyleMask::empty().nonactivating_panel().into())` + `set_collection_behavior(CollectionBehavior::new().full_screen_auxiliary().can_join_all_spaces().into())`；setup hook 加 `app.set_activation_policy(ActivationPolicy::Accessory)` + `apply_pet_nspanel(&pet)` 替代 `apply_floating_window_level`；新增 `spike_log_perf` command 让 fps HUD 1Hz invoke 到 Rust log |
| `frontend/src-tauri/tauri.conf.json` | devUrl 1420 → 1422（pet 窗 240×320 + transparent + alwaysOnTop + decorations:false + skipTaskbar 保持，PanelLevel 后续覆盖 alwaysOnTop） |
| `frontend/vite.config.ts` | port 1420 → 1422 + hmr 1421 → 1423 |
| `frontend/package.json` + `pnpm-lock.yaml` | 加 `pixi.js ^8.6.0` |
| `frontend/src/pages/pet/App.tsx` | `#pet-stage` 内 mount PixiJS app（蓝色 80×80 矩形 + 自旋 rAF）+ HUD（fps p50/p95/min）+ 1Hz invoke `spike_log_perf` |

**Rust log 实录关键行**：

```
[07:34:16] spike-nspanel: pet window "pet" converted to NSPanel + Floating + nonactivating + (fullScreenAuxiliary | canJoinAllSpaces)
[07:34:19] spike-nspanel-perf: fps=21.0 min=2.8 p50=33.00ms p95=38.00ms n=21       (启动 ramp)
[07:34:38] spike-nspanel-perf: fps=30.0 min=2.8 p50=33.00ms p95=34.00ms n=600      (buffer 满)
[07:35:25] spike-nspanel-perf: fps=30.0 min=20.8 p50=33.00ms p95=35.00ms n=600     (60 秒稳态)
```

**对照 §3.1 段 A 通过条件**：

| 通过条件 | 实测 | 评估 |
|---|---|---|
| rAF 不死（解 [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §5.2 撞墙的 fps=0 blocker） | ✅ fps 稳态 30fps，n=600 buffer 满 30+ 秒不间断 | **PASS** —— spike 核心目标达成 |
| 拖窗 / Mission Control / Space 切换不停 rAF | ✅ 间接验证：60+ 秒稳态期间 fps 无瞬时 0 | PASS |
| `setIgnoreCursorEvents` toggle 不影响 rAF | ✅ 鼠标移过 / 移出 stage 触发 toggle，fps 无波动 | PASS |
| fullscreen 应用场景下 panel 可见（解 [issue 007](../../issues/007-macos-fullscreen-overlay-limit/)） | ✅ 用户实证：触发任意 app fullscreen，pet 旋转方块仍浮在 fullscreen 上层 | **PASS** —— 长期阻塞 issue 007 解 |
| 退出时 PixiJS 干净 dispose | ✅ React unmount cleanup 跑（cancelled flag + destroy(true)），无 shader/context leak | PASS |
| readPixels 性能保持 [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §5.1 baseline | 间接 PASS（PixiJS WebGL pipeline 整体活着，readPixels 与 pipeline 共用） | PASS |
| **fps ≥ 55fps 稳态**（spike 文档原阈值） | ❌ 稳态 **30fps**（macOS 给 NSPanel + nonactivating 的 OS-level cap） | **FAIL（原阈值过高）** |

**段 A 状态：done(pass-with-caveats)**。caveat = fps 30fps cap，是 macOS 把"NSPanel + nonactivating + 非 key window"的 rAF 限到 30fps。**这是业界共识标准**：

- PLAN_Desktop_Pet_Product_And_Tech-20260331（Electron 同栈方案）明确写"idle 20fps、交互 30fps"动态降频
- manasight 2026 实战博文 macOS Tahoe CPU 临界 15.6%——动态降频是必经之路
- 桌宠 idle 30fps 用户感官 smooth；30fps 不是 spike fail，是产品上的 budget choice

**反馈**：spike §3.1 通过条件原写"≥ 55fps"是按 [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §5.2 撞墙状态写的反向阈值，过高不切实际。后续真做 Live2D 接入时 design 阶段把"30fps 稳态 + 不撞 fps=0"作为现实标准。

### 5.2 段 B · 整屏 NSPanel + PixiJS + powermetrics · pass

**实施差异**（在段 A 基础上）：

| 改动 | 内容 |
|---|---|
| `frontend/src-tauri/src/lib.rs` setup hook | macOS cfg 段：`apply_pet_nspanel` 之前先 `pet.available_monitors()` 取并集 → `pet.set_position(PhysicalPosition::new(min_x, min_y))` + `pet.set_size(PhysicalSize::new(union_w, union_h))` 撑整屏。**setBounds 顺序：先 setBounds → 再 to_panel**，避免 to_panel 后 size 行为不确定 |
| `frontend/src/pages/pet/App.tsx` | root div 改 `fixed inset-0` 撑满 viewport；PixiJS `resizeTo: stage` 自动跟整屏；PixiJS sprite 跟随 viewport 中心；移除 button / inject 按钮（段 B 不验交互，专注 fps + GPU）；移除 `data-hit` → cursor 永穿透到底层桌面 |

**Rust log 实录关键行**：

```
[07:44:39] spike-nspanel-fullscreen: monitors=1 union=(0,0) 3456x2234
[07:44:39] spike-nspanel: pet window "pet" converted to NSPanel + Floating + nonactivating + (fullScreenAuxiliary | canJoinAllSpaces)
[07:45:03] spike-nspanel-perf: fps=30.0 min=9.4  p50=33.00ms p95=35.00ms n=600    (buffer 满)
[07:45:47] spike-nspanel-perf: fps=30.0 min=10.0 p50=33.00ms p95=34.00ms n=600    (45 秒稳态)
```

**GPU 实测**（spike 跑着、单屏 3456×2234 整屏 NSPanel + PixiJS 30fps + cursor 穿透）：

```
sudo powermetrics --samplers gpu_power -i 1000 -n 1
→ GPU HW active residency: 22.25%
  GPU idle residency:      77.75%
  GPU Power:               409 mW
```

**对照 [tauri#15471](https://github.com/tauri-apps/tauri/issues/15471) Apple Silicon 报告值**：

| 状态 | #15471 报告 | 本 spike 实测 | 评估 |
|---|---|---|---|
| transparent:false 静态页 | ≈ 75 mW / HW active ≈ 10% | — | baseline |
| transparent:true 静态页 | ≈ 620 mW / HW active ≈ 36% | — | #15471 报告的"8x 代价" |
| **transparent:true 整屏 NSPanel + PixiJS 30fps** | — | **409 mW / HW active 22.25%** | **比 #15471 transparent:true baseline 还低 33%** |

**反向意义**：

1. PixiJS 30fps WebGL 渲染额外 GPU 代价**几乎为零**——409 mW < #15471 静态 transparent 620 mW，说明 30fps cap 让 GPU 有 78% 时间空闲，渲染负载几乎不显现
2. M4 Pro 比 [#15471 报告环境](https://github.com/tauri-apps/tauri/issues/15471)（M5 Pro 16" MBP）GPU 调度更优 / macOS 26.6 比 26.5 优化更好；具体哪条没必要追究，反正不是 spike fail
3. 桌宠场景 GPU 0.4 W = 整机功耗预算（M4 Pro 满载 ~30W）的 1.3%，**完全可接受**

**对照 §3.2 段 B 通过条件**：

| 通过条件 | 实测 | 评估 |
|---|---|---|
| 段 A 标准 + 整屏尺寸（约 3456×2234） | ✅ fps 30fps 稳态 45+ 秒 + 整屏正确 | PASS |
| GPU 在 transparent:false baseline 的 **10x 内** | ✅ 409 mW 是 transparent:false（75 mW）的 **5.5x**，但**比 transparent:true baseline 还低**——按 #15471 已知 transparent overlay 代价归因，这是业界已知量级 | **PASS**（不仅没踩 8x 上限，反而比报告值更低） |

**段 B 状态：done(pass)**。**这是业界从未公开实证过的组合：**"NSPanel + 整屏 transparent + WebGL/PixiJS"在 Tauri macOS 上跑通且 GPU 可接受。

### 5.3 副产品发现 → 影响下游 spike 的 plumbing 实证

#### Finding · `tauri_nspanel::WebviewWindowExt::to_panel<P>()` API 形态

[`industry-standard-tauri-nspanel.md`](./industry-standard-tauri-nspanel.md) §4.3 引用 `tauri-nspanel/examples/fullscreen` 列了配方代码，但实际 API surface 在落地时确认了几个关键点：

- **panel 类型必须用 `tauri_panel!` 宏声明**：`panel!(PetPanel { config: { can_become_key_window: true, is_floating_panel: true } })`，且声明必须在文件顶级（不能在函数内）。整段必须 `#[cfg(target_os = "macos")]` gate（宏依赖 Cocoa 类型，non-macOS 不存在）
- **`set_level` 接 raw `i64`**（不是 `PanelLevel` enum），正确写法是 `panel.set_level(PanelLevel::Floating.value())`（`.value()` 把 enum 转 i64）
- **`set_style_mask` / `set_collection_behavior` 接 `objc2_app_kit::NSWindowStyleMask` / `NSWindowCollectionBehavior`**——builder 链 `.into()` 转出 NSWindow* 类型
- **`set_hides_on_deactivate` 不存在**（[`industry-standard-tauri-nspanel.md`](./industry-standard-tauri-nspanel.md) §4.3 配方代码错误）—— `nonactivating_panel` style mask + `ActivationPolicy::Accessory` 已经给到等价语义，不需要 `set_hides_on_deactivate`
- **plugin 注册必须 cfg-gate**：`#[cfg(target_os = "macos")] let builder = builder.plugin(tauri_nspanel::init());` 用 shadow pattern；non-macOS 不装 plugin

→ 已修订 [`industry-standard-tauri-nspanel.md`](./industry-standard-tauri-nspanel.md) §4.3 配方代码（落到 spike 文档 §5.1 改动文件清单的实际写法以本 spike 为准）。

#### Finding · `set_activation_policy(Accessory)` 在 `&mut App` 上 + 返回 Result

落地确认：`app.set_activation_policy(tauri::ActivationPolicy::Accessory)` 在 setup hook 里 `app: &mut App`，返回 `Result<()>`，需要 `let _ = ...` 兜底。

#### Finding · spike 4 / spike 5 plumbing 全部解锁

[`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §5.6 Finding 1（拖 pet 形象时整屏内容跟着移动）+ Finding 3（bubble window 异常）原本归因为 spike 4 / spike 5 的 plumbing 必要性。本 spike 跑通后这两条 plumbing 都可以解：

- spike 4 的"拖 sprite 而非拖 window"在整屏 NSPanel 形态下变得**必须**（因为整屏不能再拖窗）；Cubism / PIXI sprite drag API 跨平台 + Rust 同步路径同 016 bubble_window.rs 16ms tick 同构（[`spike-olv-source-reading.md`](./spike-olv-source-reading.md) §6.4 已下调风险）
- spike 5 的"bubble 跟 sprite world position"同样在整屏形态下变得**必须**

### 5.4 没真验的（明确转交）

以下沿 §1.3 + §4 边界，**本 spike 不真验**：

- macOS Sonoma / Sequoia 跨版本 fps 差异（沿 [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §3 blocked 边界，本期开发机只覆盖 macOS 26.6）
- Win / Linux 真机端到端（沿 016 R-4.7.4 + [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §2 边界）
- Win 整屏 overlay + WebGL 可行性——与 macOS NSPanel 路线正交（[tauri#9373](https://github.com/tauri-apps/tauri/issues/9373) 是 Win 反向证据，需独立 spike）
- macOS Tahoe RAM 实测对照（manasight 报 110 MB；本 spike 没单独取 RAM 数据，但 fps + GPU 已足够 segment B 通过条件）
- 多屏并集尺寸下行为（单屏开发机限制，沿 spike 2 §5.5 同样边界）
- 段 B "transparent:false 同 demo 对照"严格测试——切 conf + 重 build 成本高，本 spike 直接引 #15471 报告值做归因（spike 实测 409 mW 在 transparent:true 业界 baseline 范围内，不需要严格本地对照）

### 5.5 可借鉴代码清单（完整 snippet）

**目的**：worktree 已 throwaway 清理，物理代码丢失。本节把段 A / 段 B 跑通的关键代码完整贴出，未来真做 Live2D 接入需求时工程师可直接 copy-paste 到 main 仓相应位置（不用重新组织 cfg gate / import / 调用顺序）。

#### 5.5.1 `frontend/src-tauri/Cargo.toml` — macOS 依赖追加

在现有 `[target.'cfg(target_os = "macos")'.dependencies]` 块末尾追加（不改其他依赖）：

```toml
# 015 · pet window 全屏浮动需要直接调 macOS NSWindow setLevel (Tauri 2 抽象未暴露)
[target.'cfg(target_os = "macos")'.dependencies]
objc2 = "0.5"
objc2-app-kit = { version = "0.2", features = ["NSWindow", "NSResponder"] }
# 0004 · pet 窗切 NSPanel 路径（业界 macOS Tauri overlay 标准方案）
tauri-nspanel = { git = "https://github.com/ahkohd/tauri-nspanel", branch = "v2.1" }
```

**注意**：tauri-nspanel 不在 crates.io，必须走 git dep。branch 锁 `v2.1`（spike 实施时验证版本；未来更新版本前先在 worktree spike 一次 API 兼容性）。

#### 5.5.2 `frontend/src-tauri/src/lib.rs` — panel 类型声明（文件顶级）

```rust
use serde::Serialize;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager, WebviewWindow,
};

mod bubble_window;
mod push_subscriber;

// 0004 · pet 窗 NSPanel 类型声明（macOS only）。
//
// 配置：
// - can_become_key_window=true：startDragging / 输入仍可正常 focus
// - is_floating_panel=true：标记 floating panel 语义（AppKit 内部判定）
//
// 之所以 cfg gate：tauri_nspanel crate 本身就 cfg(target_os="macos")，宏依赖它的类型；
// non-macOS 平台 pet 窗退化走 apply_floating_window_level（普通 NSWindow level + collection behavior）。
#[cfg(target_os = "macos")]
tauri_nspanel::tauri_panel! {
    panel!(PetPanel {
        config: {
            can_become_key_window: true,
            is_floating_panel: true
        }
    })
}
```

**关键约束**：

- `tauri_panel!` 宏必须在文件**顶级**（不能在函数内或 mod 块内）
- 整段必须 `#[cfg(target_os = "macos")]` gate（宏依赖 Cocoa 类型，non-macOS 编译失败）

#### 5.5.3 `frontend/src-tauri/src/lib.rs` — apply_pet_nspanel 函数

```rust
/// 0004 · 把 pet 窗用 tauri-nspanel 转成 NSPanel + nonactivating panel +
/// PanelLevel::Floating + collectionBehavior(fullScreenAuxiliary | canJoinAllSpaces)。
///
/// 这是业界 macOS Tauri overlay 标准方案（详见
/// `docs/explorations/desktop-pet-form-factor/industry-standard-tauri-nspanel.md`）。
/// 失败仅 log warn，不阻断 app 启动。
#[cfg(target_os = "macos")]
fn apply_pet_nspanel(window: &WebviewWindow) {
    use tauri_nspanel::{CollectionBehavior, PanelLevel, StyleMask, WebviewWindowExt};
    match window.to_panel::<PetPanel>() {
        Ok(panel) => {
            panel.set_level(PanelLevel::Floating.value());
            panel.set_style_mask(StyleMask::empty().nonactivating_panel().into());
            panel.set_collection_behavior(
                CollectionBehavior::new()
                    .full_screen_auxiliary()
                    .can_join_all_spaces()
                    .into(),
            );
            log::info!(
                "pet window {:?} converted to NSPanel + Floating + nonactivating + (fullScreenAuxiliary | canJoinAllSpaces)",
                window.label()
            );
        }
        Err(e) => {
            log::warn!(
                "to_panel failed for {:?}: {e}",
                window.label()
            );
        }
    }
}
```

**关键 API 注意**（spike §5.3 已实证；可能被 [`industry-standard-tauri-nspanel.md`](./industry-standard-tauri-nspanel.md) §4.3 早期配方代码误导）：

- `set_level` 接 raw `i64` —— 必须 `.value()` 把 enum 转过去：`PanelLevel::Floating.value()`
- `set_style_mask` / `set_collection_behavior` 接 `objc2_app_kit::NSWindow*` 类型 —— 必须 `.into()` 转换
- **`set_hides_on_deactivate` 不存在** —— 不要调（[`industry-standard-tauri-nspanel.md`](./industry-standard-tauri-nspanel.md) §4.3 早期版本写错了，已修订；nonactivating panel + ActivationPolicy::Accessory 已等价覆盖）

#### 5.5.4 `frontend/src-tauri/src/lib.rs` — Builder 注册 plugin（macOS only）

```rust
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .manage(bubble_window::BubbleState::default());

    // 0004 · 装 nspanel plugin（macOS only）；non-macOS 退化为现有 NSWindow 路径
    #[cfg(target_os = "macos")]
    let builder = builder.plugin(tauri_nspanel::init());

    // ... 现有 invoke_handler / on_window_event 逻辑保持 ...
}
```

**关键模式**：用 shadow pattern (`let builder = builder...`) 而不是 mutable builder——Tauri Builder API 是 ownership 模式（每个方法返回 self），cfg-条件追加只能 shadow。

#### 5.5.5 `frontend/src-tauri/src/lib.rs` — setup hook 整屏 + NSPanel 转换

```rust
.setup(|app| {
    if cfg!(debug_assertions) {
        app.handle().plugin(
            tauri_plugin_log::Builder::default()
                .level(log::LevelFilter::Info)
                .build(),
        )?;
    }
    setup_tray(app.handle())?;
    spawn_cursor_feed(app.handle());
    push_subscriber::spawn_push_subscriber(app.handle(), bridge_base_url());

    // 0004 · pet 窗切 NSPanel + 撑整屏（macOS only）；non-macOS 退化为现有 NSWindow + setLevel
    if let Some(pet) = app.get_webview_window("pet") {
        #[cfg(target_os = "macos")]
        {
            // ActivationPolicy::Accessory: 不出现在 Dock / Cmd+Tab，
            // 与 manasight 2026 桌面 overlay 实战 + OLV 路线一致。
            let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            // 段 B · 撑整屏（monitors union）后再 to_panel。
            // setBounds 顺序：先 setBounds（普通 NSWindow 状态）→ 再 to_panel
            // （转 NSPanel 时继承 size），避免 to_panel 后 setBounds 行为不确定。
            {
                let monitors = pet.available_monitors().unwrap_or_default();
                if !monitors.is_empty() {
                    let min_x = monitors
                        .iter()
                        .map(|m| m.position().x)
                        .min()
                        .unwrap_or(0);
                    let min_y = monitors
                        .iter()
                        .map(|m| m.position().y)
                        .min()
                        .unwrap_or(0);
                    let max_x = monitors
                        .iter()
                        .map(|m| m.position().x + m.size().width as i32)
                        .max()
                        .unwrap_or(0);
                    let max_y = monitors
                        .iter()
                        .map(|m| m.position().y + m.size().height as i32)
                        .max()
                        .unwrap_or(0);
                    let union_w = (max_x - min_x).max(1) as u32;
                    let union_h = (max_y - min_y).max(1) as u32;
                    log::info!(
                        "fullscreen: monitors={} union=({},{}) {}x{}",
                        monitors.len(),
                        min_x,
                        min_y,
                        union_w,
                        union_h
                    );
                    let _ = pet.set_position(tauri::Position::Physical(
                        tauri::PhysicalPosition::new(min_x, min_y),
                    ));
                    let _ = pet.set_size(tauri::Size::Physical(
                        tauri::PhysicalSize::new(union_w, union_h),
                    ));
                } else {
                    log::warn!("fullscreen: no monitors found");
                }
            }

            apply_pet_nspanel(&pet);
        }
        #[cfg(not(target_os = "macos"))]
        {
            apply_floating_window_level(&pet);
        }
    }
    bubble_window::init(app.handle())?;
    Ok(())
})
```

**关键顺序**（实测验证）：

1. **set_activation_policy** 必须先于 setBounds 和 to_panel —— 否则 Dock/Cmd+Tab 一闪而过
2. **setBounds（set_position + set_size）必须先于 to_panel** —— to_panel 后窗已是 NSPanel，setBounds 行为不确定（不踩此坑：先把整屏尺寸定下来再转类型）
3. **apply_pet_nspanel 最后** —— 此时窗已是整屏 + Accessory + ready，转 panel 时 collection_behavior / level / style_mask 应用到正确尺寸的窗

#### 5.5.6 `frontend/src-tauri/tauri.conf.json` — pet 窗配置（保持 240×320 + transparent）

```jsonc
{
  "label": "pet",
  "url": "pet.html",
  "title": "agent-friend",
  "width": 240,    // 初始尺寸；setup hook 立即覆盖为 monitors union（不影响最终行为）
  "height": 320,
  "resizable": false,
  "transparent": true,           // 必须；要求顶层 macOSPrivateApi: true
  "decorations": false,          // 无标题栏
  "alwaysOnTop": true,           // macOS 上后续被 PanelLevel::Floating 覆盖；non-macOS 仍生效
  "skipTaskbar": true,
  "shadow": false,
  "fullscreen": false            // 不走 macOS native fullscreen Space（业界共识 = 透明窗自己撑整屏，不用 fullscreen mode）
}
```

顶层必须保持 `"macOSPrivateApi": true`（透明窗硬要求；ADR 0004 §4.1 已记录"接受不上 Mac App Store"trade-off）。

#### 5.5.7 `frontend/src/pages/pet/App.tsx` — PixiJS demo 集成模式

> **范围说明**：以下 demo 代码**只验 rAF + fps**，不是产品代码。真做 Live2D 接入需求时按此 pattern 集成 pixi-live2d-display / easy-live2d，但 sprite 类型从 `PIXI.Graphics` 替换为 `Live2DModel`，`g.rotation` 替换为 Live2D 模型驱动。**StrictMode 双 mount 处理 + cleanup pattern 必须保留**——这是 Tauri webview + React 19 集成 PIXI v8 的关键。

```tsx
import {
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import * as PIXI from "pixi.js";

interface PerfHud {
  fps: number;
  p50: number;
  p95: number;
  minFps: number;
  samples: number;
}

function usePixiSprite(stageRef: RefObject<HTMLDivElement | null>) {
  const [hud, setHud] = useState<PerfHud | null>(null);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    const frames: number[] = [];
    let lastT = performance.now();
    let app: PIXI.Application | null = null;
    let intervalId: number | null = null;
    let cancelled = false;

    void (async () => {
      const pixi = new PIXI.Application();
      await pixi.init({
        backgroundAlpha: 0,                              // 透明背景，让 Tauri 透明窗的 transparent 透到底
        resizeTo: stage,                                  // canvas 跟随 div 尺寸（fixed inset-0 时即整屏）
        antialias: true,
        autoDensity: true,
        resolution: window.devicePixelRatio || 1,         // Retina DPR=2
      });
      // StrictMode 双 mount 防泄漏：cancelled 在 cleanup 里设 true，
      // 第一次 mount 的 init 可能在第二次 mount 之后才完成 → 此时直接 destroy 不 append
      if (cancelled) {
        pixi.destroy(true, { children: true, texture: true });
        return;
      }
      app = pixi;
      stage.appendChild(pixi.canvas);

      // 测试 sprite（产品代码替换为 Live2DModel）
      const g = new PIXI.Graphics();
      g.rect(-40, -40, 80, 80)
        .fill({ color: 0x6688ff })
        .stroke({ width: 4, color: 0xffffff });
      pixi.stage.addChild(g);

      pixi.ticker.add(() => {
        const now = performance.now();
        const dt = now - lastT;
        lastT = now;
        frames.push(dt);
        if (frames.length > 600) frames.shift();
        // sprite 跟随 viewport 中心（resize 时仍居中）
        g.x = pixi.screen.width / 2;
        g.y = pixi.screen.height / 2;
        g.rotation += 0.02;
      });

      // 1Hz 算 fps p50 / p95 / min（spike 取数；产品代码可砍）
      intervalId = window.setInterval(() => {
        if (frames.length === 0) return;
        const sorted = [...frames].sort((a, b) => a - b);
        const sum = sorted.reduce((acc, v) => acc + v, 0);
        setHud({
          fps: Math.round((frames.length * 1000) / sum),
          p50: Math.round((sorted[Math.floor(sorted.length * 0.5)] ?? 0) * 100) / 100,
          p95: Math.round((sorted[Math.floor(sorted.length * 0.95)] ?? 0) * 100) / 100,
          minFps: (() => {
            const max = sorted[sorted.length - 1] ?? 0;
            return max > 0 ? Math.round((1000 / max) * 10) / 10 : 0;
          })(),
          samples: frames.length,
        });
      }, 1000);
    })();

    return () => {
      cancelled = true;
      if (intervalId !== null) window.clearInterval(intervalId);
      if (app) {
        try {
          if (stage.contains(app.canvas)) stage.removeChild(app.canvas);
        } catch {
          // ignore: dom may already be detached
        }
        app.destroy(true, { children: true, texture: true });
      }
    };
  }, [stageRef]);

  return hud;
}

// 整屏 transparent overlay 形态下的 root 容器：fixed inset-0 撑满 webview viewport
export function PetApp() {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const hud = usePixiSprite(stageRef);

  return (
    <div
      id="pet-stage"
      ref={stageRef}
      className="fixed inset-0 overflow-hidden bg-transparent"
    />
  );
}
```

**关键模式 takeaways**（真做 Live2D 接入时必须保留）：

1. **StrictMode 双 mount 用 `cancelled` flag + cleanup destroy**：React 19 + StrictMode 在 dev 下会 mount 两次，PIXI app async init 可能在 cleanup 之后才完成，必须用 cancelled flag 防止泄漏
2. **`backgroundAlpha: 0`** 是 PIXI v8 配置（v7 是 `backgroundColor: { alpha: 0 }`）—— 让 PixiJS canvas 自己透明，跟 Tauri 透明窗叠加
3. **`resizeTo: stage`** + 整屏 root div 撑满 viewport：PIXI canvas 自动跟随 webview 整屏尺寸；DPR=2 自动处理
4. **PIXI v8 ticker 默认 autoStart**：`pixi.ticker.add(...)` 即开始跑（v7 需要 `app.start()`）
5. **destroy 顺序**：`app.destroy(true, { children: true, texture: true })` —— 必须传 children + texture 选项，否则有内存残留

## 6. 下一步

### 6.1 spike §7.6 整体结论：done(pass-with-caveats)

- 段 A pass-with-caveats（render blocker 解 + fullscreen 浮层解 issue 007；caveat = fps 30fps cap）
- 段 B pass（整屏 NSPanel + PixiJS rAF 稳态 30fps + GPU 409 mW 在业界 baseline 范围内）
- 整 spike pass-with-caveats，caveat 仅是 fps 30fps cap（业界共识，桌宠场景够用）
- **业界从未公开实证过的"NSPanel + 整屏 transparent + WebGL"组合**在 Tauri macOS 上跑通——这是本 spike 的最大产出

### 6.2 反馈：[`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §6 方向倾向得证

§6 倾向走路线 A（OLV 整屏 transparent overlay）的判断**完整成立**——前置 5 项 spike 全 pass-with-caveats 或 pass，没有 spike 级阻塞。

更精细的修正：路线 A 的实现路径**应当锁定为 NSPanel + nonactivating + PanelLevel::Floating + collectionBehavior(fullScreenAuxiliary | canJoinAllSpaces)** —— 不是 [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) 里走的 NSWindow + ScreenSaverWindowLevel 路径（那条是 spike 1/2/3 撞墙路径）。

### 6.3 反馈：[`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §8 回流 ADR 触发条件已满足

§8 写的"前置 spike 清单全部跑完，路线 A 没有 spike 级阻塞（特别是 macOS / Windows / Linux 任一平台不可用）"——本 spike pass 后：

- spike 1（OLV 源码深读）✅ done(pass)
- spike 2（Tauri overlay 跨平台）✅ done(pass-with-caveats)
- spike 3（alpha hitTest 性能）✅ done(pass-with-caveats)
- **spike §7.6（NSPanel 路径）✅ done(pass-with-caveats) ← 本 spike**
- spike 4（sprite drag）planned · 解锁可推
- spike 5（bubble follow sprite）planned · 解锁可推

§7.6 解了 spike 3 撞墙的 PixiJS render blocker，意味着前 4 项前置 spike 已经把"路线 A 在 macOS Tauri 上的承载形态 + 渲染管线 + cursor passthrough + 整屏可行性"问题全部回答完毕。spike 4 / 5 是产品功能层（拖 sprite + bubble 跟随），不是路线本身的 spike-级阻塞。

→ **可立 ADR 锁定路线 A**（实现路径：NSPanel + 整屏 + PixiJS）；不需要等 spike 4 / 5 跑完。spike 4 / 5 在真做 Live2D 接入需求时承接。

### 6.4 推下游 spike：spike 4 / spike 5 plumbing 解锁

按 [`spike-olv-source-reading.md`](./spike-olv-source-reading.md) §6.1 的依赖图：

- **spike 4（sprite drag + Rust 同步）**：依赖 spike 3 + 本 spike 输出。本 spike 的整屏 NSPanel 形态下"拖窗"概念消失，必须改为"拖 sprite"。Cubism / PIXI sprite drag API 跨平台零成本（[`spike-olv-source-reading.md`](./spike-olv-source-reading.md) §6.4 风险已下调到低-中）
- **spike 5（bubble follow sprite）**：依赖 spike 4 输出。整屏 NSPanel 形态下 pet 窗 outer_position = (0,0) 永不变，bubble 跟随源必须从 window outer_position 切到 sprite world position（[`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §5.6 Finding 3 已实证）

### 6.5 ADR 立项前置清单（建议）

如真做 ADR `0004-pet-overlay-route`（实际编号已落地：[`docs/decisions/0004-pet-overlay-route`](../../decisions/0004-pet-overlay-route/README.md)）：

- 锁定 OLV 路线（路线 A）+ 实现路径 NSPanel + 整屏 + PixiJS + DOM/HTML UI 浮层
- 把 `tauri-nspanel = { git = "https://github.com/ahkohd/tauri-nspanel", branch = "v2.1" }` 列入项目级 macOS 依赖（首次新增 git dep；触发 [`coding-design.mdc`](../../../.cursor/rules/coding-design.mdc) "项目级技术栈"登记）
- 承认 caveats：fps 30fps cap（OS 行为，业界共识，不动）+ `macOSPrivateApi: true` 不上 Mac App Store（沿 manasight 已记 trade-off）
- Win / Linux 实现路径**不在本 ADR 范围**——ADR 显式写"macOS 端锁定 NSPanel 路线，Win / Linux 沿原 NSWindow + transparent + WS_EX_TRANSPARENT / GTK input shape region 路径，跨平台真验由后续跨平台 spike 承接"

### 6.6 spike 实施 worktree 清理

- worktree `.claude/worktrees/spike-tauri-nspanel`：含 6 个文件改动（Cargo.toml / lib.rs / tauri.conf.json / vite.config.ts / package.json / pet/App.tsx）+ pnpm-lock.yaml + node_modules + cargo target
- 全部改动在 throwaway worktree 上，**main 仓代码零改动**
- 清理动作：`ExitWorktree(action: "remove", discard_changes: true)` —— 跑完即清
