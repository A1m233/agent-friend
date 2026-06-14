# 桌宠承载形态：整屏 overlay vs 固定窗口

> 探索记录（过程笔记），非决策、非需求。本篇沉淀**桌宠形象到底"长在"哪里**的路线选择——
> 是一个带边界的小窗口，还是屏幕上自由游走的 sprite？
>
> 触发上下文：准备立 Live2D 接入需求时，"窗口写死宽高"这个隐含假设遇到全身人形 aspect 装不下的硬现实，
> 顺势重新审视了桌宠承载形态本身的两条路线。结论若成熟，应回流到 `docs/decisions/`。

- **创建时间**：2026-06-14
- **关联**：[需求 010](../../requirements/010-desktop-shell-and-chat-ui/)（pet 主窗起点 240×320 + Live2D 挂载点占位）、[需求 015](../../requirements/015-desktop-pet-bubble-and-conversation-owner/) + [需求 016](../../requirements/016-pet-bubble-independent-window/)（bubble 已升级为独立 window）、[`desktop-completeness`](../desktop-completeness/) §3 Tier 1 形象状态机 / Tier 3 Live2D 接入、本目录 [`README.md`](./README.md) §1.1 调研对象、本目录 [`positioning.md`](./positioning.md)（形态定位再审视）、`decisions/0001` 路线图
- **本篇不做**：具体技术方案（PixiJS 集成 / 状态机映射 / alpha 采样细节）—— 真做需求时由 `design.md` 负责

---

## 1. 背景：为什么"窗口承载形态"现在重新成为问题

[需求 010](../../requirements/010-desktop-shell-and-chat-ui/) 立 pet 主窗时定的尺寸是 240×320 + 中心 160×160 圆形占位形象，留好 `#pet-stage` 作为"未来 Live2D PixiJS canvas 挂载点"。这个尺寸是为**占位圆形头像 + 操作栏**设计的，没有为真实 Live2D 形象的 aspect 做适配。

[需求 015](../../requirements/015-desktop-pet-bubble-and-conversation-owner/) 把气泡塞 pet 主窗内 absolute 定位时，气泡 280px 宽超过窗口 240px → hot-fix 加大到 480×460；[需求 016](../../requirements/016-pet-bubble-independent-window/) 把 bubble 升级为独立 window 跟随 pet 主窗，pet 主窗回归 240×320 "只承载形象"语义。

到这一步"形象 + 窗口"看似已经干净了。但 Live2D 接入立项调研时浮出一个之前 010 / 015 / 016 没意识到的硬约束：

| 模型类型 | Canvas 原始 aspect | 在 160×160 placeholder 里渲染的效果 |
|---|---|---|
| 萌系头像（Hiyori sample） | ≈ 1:1 | OK |
| 半身肖像 | ≈ 3:4 | OK，稍紧凑 |
| **VTuber 全身站立** | ≈ **9:16 (1:1.78)** | 人物身高被压到 ~120px，**脸只剩 20-30px**，看不清表情 |

也就是说 `#pet-stage` 这个 160×160 占位区跟"全身人形"天然不兼容——它是为头像设计的。这把一个看似工程层（"Live2D canvas 多大"）的问题，推回到**桌宠承载形态本身的产品选择**上。

---

## 2. 业界主流做法调研

调研 4 个最对标的开源项目 + Live2D 渲染库本身（详见 §3 的对照表，本节先给事实）：

### 2.1 Copiwaifu（Tauri 2 + Vue 3 + PixiJS + easy-live2d）—— 栈最对标

固定窗口 + canvas 内 aspect-fit，**anchor 锚到底部而非居中**：

```jsonc
// src-tauri/tauri.conf.json
"width": 400, "height": 760,
"minWidth": 240, "minHeight": 456, "maxWidth": 560, "maxHeight": 1064,
"resizable": false, "transparent": true, "decorations": false,
"alwaysOnTop": true, "skipTaskbar": true
```

```ts
// src/live2d/runtime.ts —— 关键 fit 函数
const scale = Math.min(availableWidth / modelSize.width, availableHeight / modelSize.height);
sprite.y = Math.round(height - fittedHeight - SPRITE_FIT_PADDING);  // 8px padding，底对齐
sprite.x = Math.round((width - fittedWidth) / 2);
```

源：`https://raw.githubusercontent.com/Panzer-Jack/Copiwaifu/main/src-tauri/tauri.conf.json` + `/src/live2d/runtime.ts`

默认 400×760 (≈ 1:1.9 竖长) 就是为全身设计的；底对齐让头部位于窗口上 1/3，而不是几何中心（全身模型的几何中心在腰部）。

### 2.2 Open-LLM-VTuber（Electron + Cubism Web SDK 原生）—— 产品最对标

Python 后端 + 桌面客户端 + Live2D + 可插拔 LLM/ASR/TTS + 语音通话 + 持久化记忆。社区 ⭐10k+。架构跟我们的 bridge + 前端 + persona/记忆形态高度同构。

**pet 模式 = 跨所有显示器并集的整屏 transparent overlay + 点击穿透 + skipTaskbar**：

```ts
// src/main/window-manager.ts
// 普通 window 模式：
width: 900, height: 670, transparent: true, frame: false, hasShadow: false
// 切到 pet 模式：算所有 display 的并集 bounds 把窗口撑到整屏
this.window.setBounds({ x: minX, y: minY, width: combinedWidth, height: combinedHeight });
this.window.setIgnoreMouseEvents(true);
```

```ts
// src/renderer/src/hooks/canvas/use-live2d-resize.ts
const { width, height } = isPet ? { width: innerWidth, height: innerHeight } : containerBounds;
canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
// 模型缩放靠 model._modelMatrix.scale(scale, scale)（来自 modelInfo.kScale）
// 用户滚轮在 MIN_SCALE=0.1 ~ MAX_SCALE=5.0 之间无级调
```

源：`https://raw.githubusercontent.com/Open-LLM-VTuber/Open-LLM-VTuber-Web/main/src/main/window-manager.ts` + `/src/renderer/src/hooks/canvas/use-live2d-resize.ts`

模型 aspect 完全不约束：窗口 = 整屏，canvas = 整屏，任意 aspect 都能装。

### 2.3 Live2DPet（Electron + pixi-live2d-display）

离散尺寸预设 S/M/L/XL = 200/300/400/500，正方形 → 只适合 1:1 头像。运行时通过菜单切尺寸，`setWindowSize(s, s)` + canvas 内部 adapter resize。

### 2.4 pixi-live2d-display（库本身）

**API 不内置 fit helper**。`model.width / model.height` 暴露的是**当前缩放后**的 bounds（不是 intrinsic）。标准 fit 范式是用户自己写：

```js
model.anchor.set(0.5, 0.5);
model.position.set(app.screen.width/2, app.screen.height/2);
model.scale.set(Math.min(app.screen.width / model.width, app.screen.height / model.height));
// 全身建议 anchor.set(0.5, 1.0) + position(w/2, h) 底对齐
```

源：`https://github.com/guansss/pixi-live2d-display`（README 推荐范式）。

### 2.5 横向总结

| 维度 | 业界主流 |
|---|---|
| 窗口随模型 aspect 动态变（"动态 setSize"） | **无项目这么做** |
| 整屏 transparent overlay | OLV 独此一家（栈虽是 Electron，路线明确） |
| 固定窗 + canvas aspect-fit | Copiwaifu / Live2DPet / pixi-live2d-display README 都是这条 |
| anchor 锚到底部 | Copiwaifu 明示，pixi-live2d-display README 也推荐用于全身 |
| 离散尺寸预设（S/M/L） | Live2DPet 200/300/400/500、NekoAI 32/64/96/128 |
| 滚轮无级缩放 | OLV 0.1× ~ 5.0× |

业界**没人把窗口绑给模型 aspect**——要么固定窗里 fit、要么干脆窗口 = 屏幕让 aspect 问题消失。

---

## 3. 两条路线的真实 trade-off

| 维度 | 路线 A · 整屏 overlay（OLV 路线） | 路线 B · 固定竖长窗 + aspect-fit（Copiwaifu 路线） |
|---|---|---|
| **模型 aspect 适配** | 无限制（窗口=屏幕） | 受窗口尺寸约束，换 aspect 范围要改 `tauri.conf.json` |
| **形象大小调整** | 滚轮无级缩放 | 受窗口尺寸约束，要做离散 S/M/L 预设 |
| **形象拖拽** | 改 `sprite.position`，跨屏自由 | 改 window position，受 OS 桌面区域限制 |
| **Cursor passthrough** | 必须 alpha 采样**一次到位**（DOM 几何 fallback 不可用） | DOM 几何/alpha 都行（现有 DOM 几何能 work） |
| **bubble 跟随逻辑** | 要从 PixiJS 取 sprite world position 喂给 Rust，bubble 跟 sprite | 跟 window `outer_position`（016 现有逻辑） |
| **拖拽语义** | 拖 Live2D sprite（canvas 内 reposition） | 拖整个 window（`startDragging` 现有） |
| **pet 主窗 OS 占地** | 整屏 | 240/280 宽 |
| **架构纯粹度** | 高：窗口=渲染画布，形象=画布内 sprite，两者解耦 | 中：形象绑在窗口几何中心，耦合 |
| **未来扩展性** | 高：形象走动 / 多 pet / 气泡贴嘴边 / 形象跨屏漫游都自然 | 中：被窗口框住 |
| **改动量** | 大（Rust setBounds combined display + alpha 必须、拖拽改 sprite、bubble 跟 sprite、跨平台 spike） | 小（改 tauri.conf.json + 写 PixiJS aspect-fit） |
| **工程风险** | 中-高（Tauri 整屏 overlay 跨平台行为要 spike，非阻塞但不确定性大） | 低（业界主流 + Copiwaifu 同栈跑过） |
| **同栈/产品对标实证** | OLV（Electron，但产品最像） | Copiwaifu（Tauri 2 + Vue + PixiJS + easy-live2d，栈几乎一致） |

---

## 4. 为什么这是质性差异，不是量性差异

两条路线**不是"小+大"的连续光谱**，是两种不同的桌宠哲学：

- **路线 B**：桌宠 = 一个小窗口，形象长在里面。窗口有边界、有尺寸感、可以拖动整体。本质是"app 窗口"
- **路线 A**：桌宠 = 一个屏幕上自由游走的 sprite。"窗口"这个概念对用户消失，只剩形象本身。本质是"在桌面上活着的东西"

后者明显更贴近 `decisions/0001` vision 里"有人格的陪伴朋友"的"在场感"——chat 窗是"app"，pet 应该不是 app。本目录 [`positioning.md`](./positioning.md) §6 也明确："**不是'做一个桌宠'，而是'做一个有记忆有人格的朋友，它恰好能以桌宠形态出现之一'**"——形态层的设计应当服从这个语义，而不是反过来被"app 窗口"思维限定。

这就是为什么"改动量增加 30-50%"不是简单的"成本上升"——它是**桌宠形态范式的切换**。如果走路线 B 然后未来再切换路线 A，需要重做的不只是窗口尺寸，是整条 plumbing：拖拽语义改、bubble 跟随源改、cursor passthrough 兜底策略改、形象坐标系改。

---

## 5. issue 005 旧拒绝理由的重新审视

[issue 005](../../issues/005-pet-bubble-window-sizing/) 在讨论 PetBubble 该不该独立 window 时，曾把"全屏 transparent overlay"列为方案 B 并拒绝，理由：

> 缺点：cursor passthrough 需更精细的 hit-test；alwaysOnTop overlay 在多 app 切换时行为更难控

但当时的语境是**作为 PetBubble 解决方案**评估 overlay——决策点是"气泡 + 形象都塞 pet 主窗 vs 气泡独立 window"。现在 bubble 已经走方案 A 落地（016），现在讨论的是 **Live2D 形象**承载方式，跟气泡已经解耦。两条拒绝理由在新语境下需要重新审视：

| 旧拒绝理由 | 新语境下还成立吗 |
|---|---|
| "cursor passthrough 需更精细的 hit-test" | ❌ **不再是新增成本**。Live2D 接入本来就要把 hitTest 从 DOM 几何升级到 PixiJS canvas 的 alpha 采样（usePetPassthrough 早留好这条缝，见 `usePetPassthrough.ts:20`）。overlay 路线只是让 alpha 采样"必须一次到位"，无法 DOM 几何兜底 —— 但成本本来就要付 |
| "alwaysOnTop overlay 在多 app 切换时行为难控" | ⚠️ **部分成立**。OLV 实跑能 work + 能压过 fullscreen layer 是 strong 实证；但 OLV 用 Electron，Tauri 在 macOS/Windows/Linux 的具体 overlay 行为要 spike。**不是阻塞风险，是不确定性风险**——可以单独 spike 验证，不影响其他决策 |

所以 005 当时的反对权重比看起来轻很多。

---

## 6. 方向倾向：走 OLV 路线

> 性质：**当前方向倾向**，非锁定决策。回流到 `docs/decisions/` 的触发条件见 §8。

基于本次探索，倾向走路线 A（OLV 整屏 transparent overlay）。理由按权重排序：

1. **桌宠哲学契合 vision**：`decisions/0001` 和本目录 `positioning.md` 都把"陪伴朋友"作为差异化护城河；路线 A 让窗口边界对用户消失、形象成为"桌面上活着的东西"，这跟"朋友 = 实体存在感"语义契合，路线 B 更接近"app 窗口里的形象"
2. **OLV 是我们的重点参考对象**：栈虽是 Electron（不是 Tauri），但**产品定位、功能矩阵、架构分层**跟我们几乎是同构——Python 后端 + 桌面客户端 + Live2D + 可插拔 LLM/ASR/TTS + 主动行为 + 持久化记忆。它是我们前面立 vision 时调研过的"陪伴方向 + 实体形态"那个空格子里**事实上跑出来的最成熟项目**。它的路线选择本身就是对我们的强参考——不只在 Live2D 渲染这块，未来产品功能（语音通话、ASR、主动陪伴节奏、模型 / persona 切换 UX）都应该把它当作首要观摩对象
3. **架构纯粹度对未来扩展有杠杆**：路线 A 把"窗口"和"形象"彻底解耦——窗口是渲染画布，形象是画布内 sprite。未来想做"形象在屏幕上自然走动"、"多个 pet 同时出现"、"气泡贴近形象嘴边"、"形象跨屏漫游"等都是 sprite 层面的事，不动窗口 plumbing
4. **旧拒绝理由权重低**：§5 已重新审视，cursor passthrough 升级是本来就要做的，跨平台 overlay 行为是 spike 风险不是阻塞风险

不选路线 B 的代价：路线 B 的"工程稳"优势真实存在，但收益是"快一点把当前 placeholder 替换成 Live2D"——这是个**短期收益**，未来如果切换桌宠哲学还是要返工，且返工面比现在大得多（plumbing 全套要改）。

---

## 7. 后续真做需求时的前置 spike 清单

**Spike 状态**（详情见同目录 `spike-*.md`，按执行依赖顺序排）：

| Spike | 文件 | 状态 |
|---|---|---|
| §7.5 OLV 源码深读 | [`spike-olv-source-reading.md`](./spike-olv-source-reading.md) | **done(pass)** · 2026-06-14 |
| §7.1 Tauri overlay 跨平台 | [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) | **done(pass-with-caveats)** · 2026-06-14（macOS 4 场景过 + 文档调研覆盖三端；Fullscreen 屏蔽 = 已知 macOS 限制 / 多屏单屏开发机未真验） |
| §7.4 alpha hitTest 性能 | [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) | **done(pass-with-caveats)** · 2026-06-14（readPixels < 1μs pass；副产品发现 PixiJS 不渲染 blocker 留 spike 4 接班） |
| §7.6 NSPanel 路径解 render blocker | [`spike-tauri-nspanel-pet-window.md`](./spike-tauri-nspanel-pet-window.md) | **done(pass-with-caveats)** · 2026-06-14（段 A 解 spike 3 PixiJS render blocker + 浮 fullscreen 解 issue 007；段 B 整屏 NSPanel + PixiJS 30fps 稳态 + GPU 409 mW 在 #15471 业界 baseline 范围内；caveat = fps 30fps cap，OS 行为业界共识；业界标准方案对照见 [`industry-standard-tauri-nspanel.md`](./industry-standard-tauri-nspanel.md)） |
| §7.2 sprite 拖拽 + Rust 同步 | [`spike-pixijs-sprite-drag-rust-sync.md`](./spike-pixijs-sprite-drag-rust-sync.md) | planned · 依赖 §7.6 pass |
| §7.3 bubble 跟 sprite | [`spike-bubble-follow-sprite.md`](./spike-bubble-follow-sprite.md) | planned · 依赖 §7.2 pass |
| §7.7 Win 整屏 overlay 文档调研 | [`spike-tauri-win-overlay-research.md`](./spike-tauri-win-overlay-research.md) | **done(pass-with-caveats)** · 2026-06-14（文档调研型 spike：6 子问题全有"已知答案"或"显式转交真机"；caveat = [wry#1691](https://github.com/tauri-apps/wry/issues/1691) + 上游 [WebView2Feedback#5536](https://github.com/MicrosoftEdge/WebView2Feedback/issues/5536) WebView2 mouse-movement GDI 泄漏对桌宠 cursor 持续穿透场景受害；finding = Tauri 业界 Win 整屏 overlay 公开实证空白，Electron 业界已实证 OLV；给 Win 端到端真机 spike 留接力 baseline） |

每项 spike 已落地为独立计划文件（§1 验证目标 / §2 方法 / §3 通过条件 / §4 不在范围 已填，§5 实际结果 / §6 下一步 待真跑后追加）。spike 全跑完后回流 `docs/decisions/` 立 ADR（见 §8 触发条件）。

这条路线**真做需求前**应当先 spike 几个不确定性点，避免在 design 阶段才暴露：

1. **Tauri 整屏 transparent overlay 跨平台行为**
   - macOS：跨所有显示器并集的 `setBounds` 在多屏 + 不同分辨率下的行为；overlay 跟 016 已落定的 `apply_floating_window_level`（NSScreenSaverWindowLevel + CanJoinAllSpaces）的叠加效果；macOS fullscreen Space 限制（[issue 007](../../issues/007-macos-fullscreen-overlay-limit/)）在 overlay 形态下是否同样存在
   - Windows / Linux：transparent + alwaysOnTop + ignoreCursorEvents 在两个平台的可用性 spike，本目录 README §1.2 / 006-agent-bridge 没覆盖跨平台测试
2. **PixiJS canvas 内拖拽 sprite + Rust 侧位置同步**
   - 拖拽语义从"拖窗口"改为"拖 sprite"，要在 PixiJS 层做 sprite hit-test + drag state（不能复用 Rust `startDragging`）
   - sprite 在 canvas 里的 world position 需要 emit 给 Rust，供 bubble 跟随
3. **Bubble 跟 sprite 而非 window 的事件通道**
   - 016 现在 `bubble_window.rs` 16ms tick 读 pet 窗 `outer_position`；overlay 形态下 pet 窗位置是固定的（整屏），bubble 要改成"读 sprite world position + window scale_factor 算出屏幕坐标"
   - 频率：16ms tick 是否撑得住（sprite 在被拖拽时高频改位置），还是改成 sprite drag-end 才同步
4. **整屏 alpha 采样 hitTest 性能 baseline**
   - 实际上 `gl.readPixels` 只取光标位置 1 个像素，开销跟 canvas 尺寸无关——但要 baseline 验证整屏 canvas 60fps `readPixels` 不会被 GPU pipeline stall 拖累
   - 不同 GPU / 集显 / 高分屏 DPR 下的兼容性
5. **OLV 源码深读**
   - 至少把 `Open-LLM-VTuber/Open-LLM-VTuber-Web` 的 `src/main/window-manager.ts` + `src/renderer/src/hooks/canvas/use-live2d-resize.ts` + cursor passthrough / 拖拽 / 模型切换 / 滚轮缩放整个链路读透
   - 标注"哪些可直接照搬（即便从 Electron → Tauri 概念基本可迁移）"、"哪些 Electron 特有要换 Tauri 等价物"

每一项 spike 都应能独立验证、独立失败、独立修正——不要叠在一个大需求里一起做。

---

## 8. 回流 `docs/decisions/` 的触发条件

> **状态**：**已回流** · 2026-06-14 · 本文件 §6 方向倾向 + §7 前置 spike 清单已落地为 ADR [`0004-pet-overlay-route`](../../decisions/0004-pet-overlay-route/README.md)（路线 A 锁定 + macOS NSPanel 实现路径锁定 + Win 整屏 overlay 方向锁定）。后续真做 Live2D 接入需求时 requirement.md 应直接引用 ADR 0004，**本篇降级为 ADR 立项前的事实记录**。

本篇是 exploration，**不抢决策权威**。下列条件任一满足时，应回流到 `docs/decisions/` 立一份新 ADR（按 docs-discipline 三铁律：决策不就地推翻，新 ADR 覆盖原决策）：

- §7 前置 spike 清单全部跑完，路线 A 没有 spike-级阻塞（特别是 macOS / Windows / Linux 任一平台不可用） · ✅ 已满足（spike 1/2/3/§7.6 全 pass，触发本次 ADR 立项）
- 真正立 Live2D 接入需求前，需要把"桌宠承载形态选择"在项目级锁定（影响 010 / 015 / 016 之上的整条形态层 plumbing，不应该在某个具体需求的 design.md 里偷定） · ✅ ADR 0004 已锁
- 如果 spike 暴露 OLV 路线在 Tauri 下不可工程化（极小概率），回流时方向可能反转——这时也应当立 ADR 说明"探索过 overlay 路线但 Tauri 下不可行，回退到 Copiwaifu 路线"，避免后续再 revisit · 未触发（spike 全 pass-with-caveats）

回流前，本篇是 explorations 里的**最高权威**——立 Live2D 需求时 requirement.md 应直接引用本篇 §6 的方向倾向。

---

## 9. 不在本文范围

- **具体技术方案**（PixiJS 集成 / Live2D 库选型 pixi-live2d-display vs easy-live2d / 模型资产组织 / 状态机推断逻辑 / alpha 采样实现）—— 真做需求时 `design.md` 负责
- **形象状态机本身**（idle / thinking / speaking / error 4 态最小集）—— 跟"窗口承载形态"正交，无论走 A 还是 B 都要做；详见 [`desktop-completeness`](../desktop-completeness/) §3 Tier 1
- **情绪 / 状态事件通道**（agent 主动发 happy / shy / sad 给前端）—— 跨 agent + bridge + frontend 三层，是独立议题；详见 [`initial-frontend`](../initial-frontend/) §5 ④
- **桌宠 spritesheet 协议扩展**（Codex 9 状态行号 → 可声明 + 间接层）—— 走 Live2D 路线不走 spritesheet，详见本目录 [`README.md`](./README.md) §3
- **OLV 详细产品/架构观摩**—— 留给后续 reading session；本篇只引用支撑路线选择必需的部分

---

## 10. 待决问题（留给后续 spike / 立需求时）

- OQ-1：§7 spike 是否每项都独立立 issue / 跑通后才考虑立 Live2D 接入需求？还是某些可以并到 Live2D 需求的 design 阶段一并 spike？倾向前者，但成本 vs 收益的具体阈值未定
- OQ-2：操作栏（"打开对话界面"按钮 + dev 注入按钮）在 overlay 形态下放哪？现在贴在形象下方 flex 居中；overlay 形态下"形象下方"是屏幕上的某个 sprite-relative 位置，不再是窗口内 DOM——是改成跟 sprite 浮动的 UI 层（PixiJS Container / DOM absolute）、还是直接砍掉操作栏改用托盘菜单 + 右键菜单承接？后者更接近调研里桌宠标配（README §1.1）
- OQ-3：路线 A 下 pet 主窗"占整屏"在 macOS Mission Control / 拖动其他 app / Cmd+Tab 切换等场景的实际表现，要 spike 后确认是否真"对用户完全消失"
