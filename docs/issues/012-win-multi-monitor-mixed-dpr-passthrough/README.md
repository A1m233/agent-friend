# 012 · Win 多屏混合 DPR 下 cursor passthrough 全部失效

- **状态**：open
- **严重度**：medium-high · Win 多屏 mixed DPR 用户无法使用桌宠交互
- **发现日期**：2026-06-15
- **发现于**：[018-pet-live2d-state-and-lipsync](../../requirements/018-pet-live2d-state-and-lipsync/) M18.9 Win 端真跑
- **关联需求**：[017-pet-overlay-form-switch](../../requirements/017-pet-overlay-form-switch/)
- **关联 ADR**：[0004-pet-overlay-route](../../decisions/0004-pet-overlay-route/README.md)
- **关联 spike**：[`spike-tauri-win-overlay-research.md`](../../explorations/desktop-pet-form-factor/spike-tauri-win-overlay-research.md)

## 现象

用户 Win 配置：主屏 2560×1600 @ 175% + 副屏 2560×1440 @ 125%，桌宠整屏 transparent overlay 横跨两屏。

`./scripts/dev/run.ps1` 起动后：
- 穿透 ON（默认）→ Hiyori 形象上 hover 不出 ActionBar / 拖不动 / 点不到按钮，所有鼠标事件透到下方 app
- 切 DevPassthroughToggle "穿透 OFF" → 所有交互恢复正常（webview 收所有事件）

## 根因

`usePetPassthrough` dev log 抓到的关键数据：

```
viewport=4096x1280 sprite=(2266,495)91x288 inSprite=false dom=data-hit
cursor 坐标范围 0..2925, 0..822
```

坐标系断裂：

| 量 | 实际值 | 算法推断 |
|---|---|---|
| cursor 最大 x | 2925 | 物理 union 5120 / 主屏 DPR 1.75 ≈ 2925 |
| viewport 宽 | 4096 | 物理 union 5120 / 副屏 DPR 1.25 ≈ 4096 |

`spawn_cursor_feed` (lib.rs) 用 `pet.scale_factor()` 算 cursor logical px，但整屏 webview 跨多 monitor 时 `scale_factor()` 取 webview owner monitor 的 DPR（主屏 1.75）。而 PIXI canvas / `window.innerWidth` 用 webview 实际渲染的 DPR（可能取另一屏）。两套 DPR 在 mixed 配置下不对齐。

alpha-scan 推 visible bounds（sprite=91×288）也错位——`gl.readPixels` 用 canvas 物理 px，跟 cursor logical px 转换需要一致 DPR，mixed 下错。

DOM `elementFromPoint(cursor.x, cursor.y)` 在错位坐标系下命中错位置（log 显示 dom=data-hit 是因为 cursor 偶然命中 DevPassthroughToggle 按钮所在区域），sprite 命中区永远 false → `setIgnoreCursorEvents(true)` 永远不切回 → 鼠标事件穿透 → 用户无法交互。

## 影响与判断

- 影响 Win 多屏 mixed DPR 用户产品可用性（**严重**：完全无法 hover / drag / 点击）
- Win 单屏 / 多屏同 DPR 配置不受影响（17a Win spike 单屏环境已验证 OK）
- macOS 不受影响（NSPanel 整屏 overlay 在 macOS 跨多屏 DPR 由 OS 内部协调，Tauri 看到的 cursor / window 用统一坐标系）
- 不阻塞 18b spec 的 AC（18 AC 主要是 push event 路径 / 状态机 / lip-sync，跟坐标系无关）
- 用户当前 workaround：切 "🔓 穿透 OFF"（pet 变实窗，所有交互恢复，但失去透明区穿透下方 app）

## 可选修复方向

1. **`spawn_cursor_feed` 用 Per-Monitor DPI awareness**：调 Win API `GetDpiForWindow` / 按 cursor 当前所在 monitor 拿 DPR，cursor 坐标按 monitor-local DPR 转 logical，再加 union offset 拼到 webview viewport 同坐标系。复杂但根治。
2. **PIXI canvas 强制用统一 DPR**：让 `usePixiAvatarSlot` `resolution` 显式跟 `window.devicePixelRatio` 一致，避免 PIXI 自己跟 webview viewport 用不同 DPR。可能只缓解部分问题。
3. **整屏 overlay 模式下 cursor 命中改 client-side**：前端不依赖 Rust cursor feed，浏览器自己监 `mousemove` event 拿 client (x,y)（webview viewport 坐标系一致）。但 `setIgnoreCursorEvents(true)` 后 webview 不收 mousemove，需要 toggle 时机重设计。
4. **Win 端整屏 overlay 仅 single-monitor 模式**：把 Win pet 窗物理大小限制为 cursor 当前所在 monitor，不跨屏。简化坐标系但失去"形象漫游所有屏"语义（用户实际配置就是跨屏）。
5. **暂不修 + 文档标注 limit**：在 ADR 0004 / 017a / 018b requirement 标注 "Win mixed DPR 多屏配置不在支持范围"，用户用单屏 / 同 DPR 跑。短期解。

倾向方向 1（根治）+ 暂时方向 5 标 limit 让用户单屏跑通；方向 2 / 3 可作为方向 1 的过渡 patch。

## 关联

- 17a Win spike `spike-tauri-win-overlay-research.md` §6.4.3 实测是单屏环境，多屏 mixed DPR 不在该 spike 覆盖
- ADR 0004 §4.4 接受的 Win trade-off 是 "WebGL 未真验"，没提多屏 mixed DPR——属设计未覆盖场景
- 修复时建议先做一个 multi-monitor mixed DPR 的 Win spike 沉淀实测数据 + Per-Monitor DPI API plumbing 方案
