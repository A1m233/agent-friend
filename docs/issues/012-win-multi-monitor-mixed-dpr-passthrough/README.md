# 012 · Win 多屏混合 DPR 下 cursor / viewport 双 DPR 坐标系错位 → passthrough 失效

- **状态**：**fixed** · 修复闭环 commit `78dcd26` (v3 Per-Monitor DPI 同屏) → `3db1552` (v4 webview viewport DPR 跨屏，最终修法)
- **严重度**：resolved · 修复前 Win 多屏 mixed DPR 用户穿透 ON 完全无法交互
- **发现日期**：2026-06-15
- **真根因确认日期**：2026-06-16（对照验证锁定 cursor / spriteScreen 坐标系错位）
- **修复完成日期**：2026-06-16
- **发现于**：[018-pet-live2d-state-and-lipsync](../../requirements/018-pet-live2d-state-and-lipsync/) M18.9 Win 端真跑
- **关联需求**：[017-pet-overlay-form-switch](../../requirements/017-pet-overlay-form-switch/)（17a Win 双屏拖动原本是 work 的）

## 现象（修复前）

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

- `e.payload` (cursor) Rust 端 `spawn_cursor_feed` 通过 `pet.scale_factor()` 算 logical px，取 **webview owner monitor 的 DPR**（主屏 175% → max ≈ 2925）
- `spriteScreen` 是 PIXI `slot.getBounds()`，跟 **webview viewport CSS px** 同坐标系（用 viewport DPR 算，max ≈ 4096）

**两套数字范围不重合**。用户 dev log 实测：
```
cursor=(689,751) ... sprite=(2006,504)320x449 inSprite=false
```
鼠标在 Hiyori 视觉中心时 cursor=689 但 sprite 在 viewport 坐标 2006~2326。把鼠标精确移到 (2150, 700)（sprite 矩形中心）时 `inSprite=true` 但鼠标在屏幕物理位置上**不是 Hiyori 视觉中心**（差 1317px）→ 坐标系错位确诊。

cursor 永远进不到 sprite 矩形 → `slotBoundsHit` 永远 false → 进不到 `alphaHitTest` → 默认 `apply(true)` 永远穿透 → 用户完全无法交互。

## mitigation 四轮 + 最终修法

### v1（commit `49a26c2`）— 改 spriteScreen anchor，关错位置

- `usePixiAvatarSlot` 加 `ratio < 0.5 || > 1.5` sanity check，alpha-scan visible bounds 异常时 fallback `slot.getBounds()`
- **失效原因**：(a) 真根因在 cursor 坐标系，spriteScreen 改 anchor 并不让 cursor 进得到矩形；(b) mac 上 Hiyori 头+身体 ratio ~0.29 跟 Win 错位 ratio 0.28 几乎相同，ratio 判别误伤 mac

### v2（commit `876718f`）— 改 alphaHitTest，仍关错位置

- 删除 v1 ratio check（mac false positive）+ 加 `findVisibleBounds` 连续 30 帧返 null 检测 → 设 `alphaScanGivenUpRef.current = true` → `usePetPassthrough` 读此 ref 跳过 `alphaHitTest`
- **失效原因**：`alphaHitTest` 在 `slotBoundsHit` 之后才跑，cursor 在 `slotBoundsHit` 那一关就被挡了，跳不跳过 alphaHitTest 没意义

### v3（commit `78dcd26`）— Per-Monitor DPI awareness，同屏 work 跨屏 drag 仍失效

- `spawn_cursor_feed` scale 从 `pet.scale_factor()` 改按 cursor 当前所在 monitor 的 scale_factor 算（`available_monitors()` 遍历找 cursor 命中的 monitor）
- **同屏 hover / drag / click 全恢复** ✓
- **跨屏 drag 时仍失效**：cursor 跨屏切 monitor scale → 数字范围 ×1.4 跳跃 → 跟 spriteScreen 矩形（PIXI viewport 固定坐标系）不重合 → drag 中断 + 完全穿透

### v4 最终修法（commit `3db1552`）— webview viewport 实际 DPR，固定不切换

cursor scale 必须用 **webview viewport 实际 DPR**（固定值，整个 webview 一套）。Tauri Rust 拿不到这个值（`pet.scale_factor()` 返 owner monitor DPR，跟 viewport DPR 不一定相等；实测 Win 多屏 overlay 时 viewport DPR ≠ owner DPR）。

**实施**：

- **Rust**：加 `PetWebviewState { dpr: Mutex<f64> }` state + `set_pet_webview_dpr` invoke 命令；`builder.manage(PetWebviewState::default())` 注册；`spawn_cursor_feed` 优先用 state.dpr，0.0 兜底 `pet.scale_factor()`（启动早期前端未上报的几十毫秒）
- **前端**：`pet/App.tsx` 加 useEffect mount 时 `invoke('set_pet_webview_dpr', { dpr: window.devicePixelRatio })` 上报；`matchMedia('(resolution: Xdppx)')` listener 监 DPR 变化（罕见但 mac 跨外接 Retina / Win 改 display scale 偶发）

**验证**（Win 端用户实测）：

- 跨屏 drag Hiyori 全程 cursor 数字保持连续（不再 ×1.4 跳跃）
- drag 不中断，鼠标跨屏后 Hiyori 跟着过去
- 穿透 ON 下 hover / drag / 跨屏 drag / 点击 ActionBar 按钮全 work

→ **真 PASS**

## 代码保留 vs revert

v1 / v2 / v3 的代码**对 mac 无害**，**对 Win 在 v4 接管后也不再阻断**：

- mac 上：alpha-scan 正常工作（一帧扫到非空白），nullScanCount 清零，`alphaScanGivenUpRef` 恒 false → 走原 alphaHitTest 路径 alpha 精度保留 ✓
- Win 上：v4 前端上报 viewport DPR 后，`spawn_cursor_feed` 不再走 v3 的 cursor monitor 切换分支（state.dpr > 0.0 时优先）；v1 / v2 alpha-scan mitigation 作为 mac safety net 保留

→ 保留 v1 ~ v4 全部 commit 不 revert。

## 教训记录

1. **hit-test 链路前置条件**：cursor / spriteScreen 同坐标系是关 (3) 的前提。加任何更细的关之前必须先验证 sanity（cursor 在 sprite 视觉中心时 inSprite=true）
2. **cursor scale 必须是 viewport 单一固定 DPR**：按 cursor 当前 monitor 动态切换看似合理（v3）但跨屏 drag 时会引入新错位（数字范围跳跃）
3. **Tauri Rust 拿不到 webview viewport DPR**：`pet.scale_factor()` 不等于 `window.devicePixelRatio`；解法是前端上报，Rust 用 invoke state 存储

## 关联

- 17a Win spike `spike-tauri-win-overlay-research.md` §6.4.3 实测是单屏环境，多屏 mixed DPR 没真验过 — 17a 双屏 work 可能是凑巧（窗口 / monitor 布局让两套 DPR 在视觉区域有交集），现在已无需 spike 重现
- ADR 0004 §4.4 接受的 Win trade-off 是 "WebGL 未真验"，没提多屏 mixed DPR 的 cursor 坐标系错位 —— 属设计未覆盖场景，本期 v4 修复方案补齐
- 不再需要 17c spike（issue 012 fixed）
