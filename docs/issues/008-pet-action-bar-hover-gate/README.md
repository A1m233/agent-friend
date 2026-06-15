# 008 · pet 操作栏按钮不响应 hover、常驻可见

- **状态**：resolved · 2026-06-15 · 17a 落地解此
- **严重度**：low
- **发现日期**：2026-06-14
- **关联需求**：[010-desktop-shell-and-chat-ui](../../requirements/010-desktop-shell-and-chat-ui/)、[016-pet-bubble-independent-window](../../requirements/016-pet-bubble-independent-window/)
- **解决方案**：[017-pet-overlay-form-switch](../../requirements/017-pet-overlay-form-switch/) · 操作栏从 pet 窗内 flex 居中改为 sprite-relative DOM 浮动 + hover gate；hover state 由 Rust 60Hz `pet://cursor` channel 驱动 `cursorOnSprite` + DOM `onMouseEnter/Leave` 驱动 `hoverActionBarDom`，通过 `stickyVisible` 状态做 400ms 防抖（消除 cursor 跨 sprite→bar 8px gap 时 ActionBar 失联）

> 修复指向：见 [017a tech-design](../../requirements/017-pet-overlay-form-switch/design.md) §3.3 + §3.4 + §5.4，落地代码 `frontend/src/pages/pet/App.tsx` 的 `stickyVisible` + `frontend/src/pages/pet/ActionBar.tsx` + `frontend/src/pages/pet/usePetPassthrough.ts` 的 `setCursorOnSprite` 驱动。
>
> 17a 真跑（macOS · 2026-06-15）AC-6 通过：默认 hidden + hover sprite 圆形浮现 + cursor 移开 400ms 后消失 + cursor 跨 gap 进 bar 正常可点。

## 现象

pet 主窗内"打开对话界面"按钮、以及 016 M16.9 调试加的"💬 短气泡 / 📜 长气泡"dev 按钮，**鼠标不 hover pet 形象时也始终可见**——与"hover 桌宠形象才浮现操作栏"的产品体感预期不符。

```
原 015 / 010 现状（M16.9 真跑确认）：
- div.group 包裹 [pet-stage, Button("打开对话界面")]
- Button 没加 group-hover 显示条件 → 默认一直显示
- 016 加的 dev 按钮（import.meta.env.DEV gate）同理一直显示
```

## 根因初判

`frontend/src/pages/pet/App.tsx` 的操作栏区域用了 Tailwind `.group` 包裹但**没有 `group-hover:` 类做显隐切换**。设计原意（M10.2 时期）大概是 hover 形象才浮按钮，落地时漏了 hover gate；015 / 016 都没顺手补。

## 影响与判断

- 桌宠形象周围常驻一个白底按钮，破坏"安静悬浮"语义，pet 主窗的视觉重心被按钮抢走。
- 不影响功能（按钮点击 / 行为完全 OK）。
- 不阻塞 016 任何 AC（hover 显隐不在 R-4.x 内）。

属于 010 / 015 / 016 共有的 UX 现状漂移，本期 016 不顺手 fix（避免越界 010 行为变更），单独登记跟进。

## 可选修复方向

1. **最小补丁**：操作栏 `<div>` + Button 加 `opacity-0 transition-opacity group-hover:opacity-100`，鼠标进 group（pet 形象 + 操作栏整体）才显示。
2. **拓展版**：抽 `<PetActionBar />` 组件，包含"打开对话界面 + 未来 persona 切换 / 静音 / dev 工具"等子按钮，统一 hover gate + 动画。
3. 进一步：键盘快捷键唤出（按 `\` 或 `space` 时显隐切换）作为无障碍替代。

## 备忘

- 修复时同时审 `frontend/src/pages/pet/App.tsx` 内 dev-only 注入按钮（💬 / 📜）的显示策略——它们是 M16.9 真跑工具，可能也想随 hover gate 走。
- 跨平台：Tailwind group-hover 在 macOS / Win / Linux 行为一致，不需要分支。
