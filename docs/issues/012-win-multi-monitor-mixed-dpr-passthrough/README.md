# 012 · Win 多屏混合 DPR 下 alpha-scan visible bounds 偏离 → cursor passthrough 失效

- **状态**：mitigated · 加 sanity check fallback，根治留后续
- **严重度**：medium · Win 多屏 mixed DPR 用户 ActionBar / bubble 锚点失去 alpha 精度（回 slot.getBounds() 矩形）但交互可用
- **发现日期**：2026-06-15
- **发现于**：[018-pet-live2d-state-and-lipsync](../../requirements/018-pet-live2d-state-and-lipsync/) M18.9 Win 端真跑
- **关联需求**：[017-pet-overlay-form-switch](../../requirements/017-pet-overlay-form-switch/)（17a Win 双屏拖动原本是 work 的）

## 现象

用户 Win 配置：主屏 2560×1600 @ 175% + 副屏 2560×1440 @ 125%，桌宠整屏 transparent overlay 横跨两屏。

- **17a 时**双屏 work，能拖动 / hover / 点击
- **18b 落地后**双屏穿透 ON 时 Hiyori 形象上 hover 不出 ActionBar / 拖不动 / 点不到按钮，所有鼠标事件透到下方 app；切 DevPassthroughToggle "穿透 OFF" → 所有交互恢复
- **18b 在 Win 单屏环境**恢复正常

→ **18b 引入的回归，不是 17a 既有 limit**

## 根因

18b `usePixiAvatarSlot` 引入 alpha-scan visible bounds（`cachedOffset` + PIXI `gl.readPixels`），让 ActionBar / bubble 锚点紧贴 Hiyori 像素轮廓。但在 Win 多屏不同 DPR 配置下：

- `cursor` Rust 端通过 `pet.scale_factor()` 算 logical px（取 webview owner monitor 的 DPR，主屏 1.75），max ≈ 2925
- `webview viewport` (`window.innerWidth`) 在 Win 用另一 monitor DPR 算（1.25），= 4096
- `PIXI canvas.width / readPixels` 物理像素跟 logical px 转换的 DPR 在 Win mixed 下又是另一个值

三套 DPR 不一致 → alpha-scan 的 `gl.readPixels(region * dpr)` 取到的不是 Hiyori 渲染区域的像素，扫出 bounds width 异常小（实测 `sprite=(2266,495)91x288`，width 91 vs slot 320 = ratio 0.28）→ `cachedOffset` 偏到错位置 → `spriteScreen` 矩形跟 cursor 坐标系不重叠 → DOM `elementFromPoint` / 矩形 hit-test 永远不命中 → `setIgnoreCursorEvents(true)` 永远不切回。

17a 没 alpha-scan，`spriteScreen = slot.getBounds()`（PIXI 内部 world coords，跟 cursor logical px 同一坐标系），多屏 mixed DPR 下也对齐 → 双屏 work。

## 已落 mitigation

`usePixiAvatarSlot` + `usePetPassthrough` 双点 mitigation：

1. **alpha-scan 持续返 null 检测**：`findVisibleBounds` 连续 30 帧（0.5s @ 60Hz）返 null（区域内一个 alpha > threshold 像素都没扫到——DPR 错位读到全空白）→ 设 `alphaScanGivenUpRef.current = true`。
2. **alphaHitTest 跳过**：`usePetPassthrough` 读此 ref，true 时跳过 `alphaHitTest`（同款 readPixels DPR 错位，永远返 false → 永远穿透 ON），矩形命中即生效（= 17a 行为）。
3. **spriteScreen 自动 fallback**：`cachedOffsetRef` 没设 → `computeBounds()` 返 `slot.getBounds()`，跟 17a 同款。

**为什么不用 width ratio 判别**：mac 上 Hiyori 头+身体像素轮廓 width 占 slot 矩形 ~0.29（实测）。issue 012 README 实测 Win 错位 width 91 / slot 320 = 0.28，**两者 ratio 几乎一样**——用 ratio 判别会误伤 mac 正常 alpha-scan（mac 用户实测撞 false positive）。所以只用"持续返 null"这一信号。

- **macOS / Win 单屏 / Win 多屏同 DPR**：alpha-scan 一帧扫到非空白 → nullScanCount 清零 → 不触发降级，alpha 精度保留
- **Win 多屏 mixed DPR**：连续 30 帧 readPixels 全空白 → 触发降级 → alphaHitTest 跳过 + spriteScreen 回 slot.getBounds() → cursor 命中跟 Hiyori 视觉区域对齐 ✓（ActionBar 锚到 slot.getBounds() 顶部，含 model canvas padding 离 Hiyori 头部稍远但功能正常）

## 已知未覆盖 case

issue 012 README 提到的实测分支 `sprite=(2266,495)91x288`（`findVisibleBounds` 返**非 null** 但 width 91 / slot 320 = 0.28）—— 当前 mitigation 的 nullScanCount 不能覆盖（scanned 非 null）。但用户当前 Win 双屏环境实测是 null 路径（findVisibleBounds 全空白），nullScanCount 已兜住。width 91 case 留观察是否真有用户撞，撞了再加额外判别（不能用 width ratio，跟 mac 撞）。

## 影响与判断

- mitigation 后 Win 多屏 mixed DPR 用户交互恢复（穿透 ON 下 hover / drag / 点击 work）
- 失去 alpha-scan 精度 polish：ActionBar / bubble 锚到 slot.getBounds() 矩形顶部而非 Hiyori 头发尖端
- 不阻塞 18b 任何 AC

## 根治方向（留后续）

1. **`spawn_cursor_feed` 用 Per-Monitor DPI awareness**：调 Win API `GetDpiForWindow` / 按 cursor 当前所在 monitor 拿 DPR
2. **PIXI readPixels 用 webview 实际 canvas 物理尺寸做转换**：不依赖 `app.renderer.resolution`，直接读 `canvas.width`
3. **17c / 单独立 Win mixed DPR spike** 实测三套 DPR 关系 + 实施统一坐标系

## 关联

- 17a Win spike `spike-tauri-win-overlay-research.md` §6.4.3 实测是单屏环境，多屏 mixed DPR 没真验过 — 17a 双屏 work 是因为 spriteScreen = slot.getBounds() 跟 cursor 自然对齐
- 18b 引入 alpha-scan 后多屏 mixed DPR 暴露问题
- 修复时建议先做 multi-monitor mixed DPR 的 Win spike 沉淀实测数据 + Per-Monitor DPI API plumbing 方案
