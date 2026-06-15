# 012 · Win 多屏混合 DPR 下 cursor / viewport 双 DPR 坐标系错位 → passthrough 失效

- **状态**：open · 两轮 mitigation 关错位置（commit 49a26c2 改 spriteScreen anchor / commit 876718f 改 alphaHitTest），根治需 Rust 端 cursor feed 用 Per-Monitor DPI awareness · 留后续 spike
- **严重度**：medium-high · Win 多屏 mixed DPR 用户穿透 ON 下完全无法交互；workaround 切 "🔓 穿透 OFF" 用 pet 实窗（失去透明区穿透下方 app 的语义）
- **发现日期**：2026-06-15
- **真根因确认日期**：2026-06-16（18b M18.9 Win 真跑两轮 mitigation 都失效，对照验证锁定 cursor / spriteScreen 坐标系错位）
- **发现于**：[018-pet-live2d-state-and-lipsync](../../requirements/018-pet-live2d-state-and-lipsync/) M18.9 Win 端真跑
- **关联需求**：[017-pet-overlay-form-switch](../../requirements/017-pet-overlay-form-switch/)（17a Win 双屏拖动原本是 work 的）

## 现象

用户 Win 配置：主屏 2560×1600 @ 175% + 副屏 2560×1440 @ 125%，桌宠整屏 transparent overlay 横跨两屏。

- **17a 时**双屏 work，能拖动 / hover / 点击
- **18b 落地后**双屏穿透 ON 时 Hiyori 形象上 hover 不出 ActionBar / 拖不动 / 点不到按钮，所有鼠标事件透到下方 app；切 DevPassthroughToggle "穿透 OFF" → 所有交互恢复
- **18b 在 Win 单屏环境**恢复正常

→ **18b 引入的回归，不是 17a 既有 limit**

## 真根因（2026-06-16 对照验证确认）

`usePetPassthrough` 的 hit-test 链路有三关：

```ts
// (1) drag 互锁 → (2) DOM data-hit → (3) sprite alpha hit-test
if (spriteScreen && slotBoundsHit(e.payload, spriteScreen)) {  // ← 关 (3) 的矩形 fast-reject
  const hit = app ? alphaHitTest(app, e.payload) : true;        // ← 然后才是 alpha 关
  if (hit) { setCursorOnSprite(true); return apply(false); }
}
```

**真根因在 `slotBoundsHit` 那一步**（关 3 第一关），不在 `alphaHitTest`：

- `e.payload` (cursor) Rust 端通过 `pet.scale_factor()` 算 logical px，取 **webview owner monitor 的 DPR**（主屏 175% → max ≈ 2925）
- `spriteScreen` 是 PIXI `slot.getBounds()` / alpha-scan visible bounds，跟 **webview viewport CSS px** 同坐标系（实际用另一 monitor DPR 125% → max ≈ 4096）

**两套数字范围不重合**。用户 dev log 实测：
```
cursor=(689,751) ... sprite=(2006,504)320x449 inSprite=false
```
鼠标在 Hiyori 视觉中心时 cursor=689，但 sprite 矩形在 viewport 坐标 2006~2326。把鼠标精确移到 (2150, 700)（sprite 矩形中心）时 `inSprite=true` 但鼠标在屏幕物理位置上**不是 Hiyori 视觉中心**（差 1317px）→ 坐标系错位确诊。

cursor 永远进不到 sprite 矩形 → `slotBoundsHit` 永远 false → 进不到 `alphaHitTest` 那一关 → 默认 `apply(true)` 永远穿透 → 用户完全无法交互。

17a 没 alpha-scan，`spriteScreen` 也是 `slot.getBounds()` —— 同样的坐标系错位**理应也存在**。但 17a Win 双屏 work 是真实的（用户实测）。可能 17a 时窗口尺寸 / 监视器布局让两套 DPR 凑巧对齐了某个交集区。本期 18b 没改 cursor 坐标计算（Rust `spawn_cursor_feed` 不动），但配置变化暴露了潜在错位——细节需 17c spike 实测重现。

## 已落但实际无效的 mitigation 反思

### Mitigation v1（commit `49a26c2`）— 改 spriteScreen anchor，关错位置

- `usePixiAvatarSlot` 加 `ratio < 0.5 || > 1.5` sanity check，alpha-scan 推的 visible bounds 异常时 fallback `slot.getBounds()`
- **失效原因**：(a) 真根因在 cursor 坐标系，spriteScreen 改 anchor 并不让 cursor 进得到矩形；(b) mac 上 Hiyori 头+身体 ratio ~0.29 跟 Win 错位 ratio 0.28 几乎相同，ratio 判别误伤 mac

### Mitigation v2（commit `876718f`）— 改 alphaHitTest，仍然关错位置

- 删除 v1 的 ratio check（mac false positive）+ 加 `findVisibleBounds` 连续 30 帧返 null 检测 → 设 `alphaScanGivenUpRef.current = true` → `usePetPassthrough` 读此 ref 跳过 `alphaHitTest`
- **失效原因**：`alphaHitTest` 在 `slotBoundsHit` 之后才跑，cursor 在 `slotBoundsHit` 那一关就被挡了，跳不跳过 alphaHitTest 没意义

### 代码保留 vs revert

两个 commit 的代码**对 Win 没用但对 mac 无害**：

- **mac 上**：alpha-scan 正常工作（一帧扫到非空白），nullScanCount 清零，flag 恒 false → 走原 alphaHitTest 路径，alpha 精度保留 ✓
- **Win 上**：根本进不到这些 mitigation 代码（cursor 进不到矩形，提前 return），但代码留着也无害；作为 mac 上潜在 alpha-scan 失效场景的 safety net

→ 保留两个 commit 不 revert。

## 影响与判断

- Win 多屏 mixed DPR 用户**完全无法**穿透 ON 下交互（hover/drag/click 全失效）
- Win 单屏 / Win 多屏同 DPR / macOS 不受影响
- Workaround：用户切 "🔓 穿透 OFF"（pet 变实窗，所有交互恢复，但失去透明区穿透下方 app 语义）
- 不阻塞 18b 任何**功能** AC（push 路径 / 状态机 / lip-sync），仅 17a 接缝点 hover 在此配置下退化

## 根治方向（17c / 单独 spike）

按优先级：

1. **Rust `spawn_cursor_feed` 改 Per-Monitor DPI awareness** — Win API `GetDpiForWindow` / 按 cursor 当前所在 monitor 拿 DPR，cursor 坐标转换跟 webview viewport 同坐标系。这是真根治。
2. **PIXI canvas resolution 显式跟 viewport CSS px DPR 一致** — 避免 PIXI 用第三套 DPR（影响 alpha-scan 像素位置但不解 cursor 错位）
3. **Win mixed DPR spike** — 先做实测：列出三个 monitor 的 DPR / 当前 cursor 所在 monitor / webview viewport 实际宽高 / PIXI canvas 物理像素，把三套 DPR 跟坐标系关系列清楚再实施 #1

## 18b 收尾决议

- AC-8（17a Win 12 AC 补回归）走 **known-limitation**：Win 多屏 mixed DPR 整屏 overlay 下穿透 ON 失效；Win 单屏 / 同 DPR 配置不受影响
- 18b progress.md / requirement.md / design.md 总体收尾置 COMPLETED
- 根治留 17c spike

## 关联

- 17a Win spike `spike-tauri-win-overlay-research.md` §6.4.3 实测是单屏环境，多屏 mixed DPR 没真验过 — 17a 双屏 work 可能是凑巧（窗口 / monitor 布局让两套 DPR 在视觉区域有交集），细节待 spike 重现
- ADR 0004 §4.4 接受的 Win trade-off 是 "WebGL 未真验"，没提多屏 mixed DPR 的 cursor 坐标系错位 —— 属设计未覆盖场景
- 18b 两轮 mitigation 关错位置教训：cursor / spriteScreen 同坐标系是 hit-test 链路的前置条件，需先验证 sanity（cursor 在 sprite 视觉中心时 inSprite=true）再加任何更细的关
