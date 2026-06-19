# 022 · memory-inspector 切换 semantic/episodic tab 时列表出现紫色矩形闪烁

- **状态**：open
- **严重度**：low
- **发现日期**：2026-06-20
- **关联需求**：026-memory-inspector

## 现象

在 memory-inspector 窗口左侧记忆列表区域：

1. 当前显示 semantic 或 episodic 列表。
2. 点击顶部 tab 切换到另一类型（semantic ↔ episodic）。
3. 切换瞬间列表区域会出现**紫色矩形闪动**，随后才渲染出目标类型的列表内容。

视觉上像是一块紫色背景/占位块先出现再消失，干扰阅读。

## 根因初判

待定位。初步怀疑点：

- Tabs / ScrollArea / 列表骨架在状态切换时的过渡动画或 focus ring 暴露。
- 未绑定数据前，某个容器组件的默认背景色或 `data-state` 样式呈现为紫色（可能来自 shadcn/ui Tabs 的激活 indicator 或 scrollbar/track 颜色）。
- 异步加载下一页或空状态组件在切换时短暂渲染紫色占位。

## 影响与判断

- 影响范围：仅 memory-inspector 前端 UI 的 tab 切换体验。
- 不违反 026 任何功能级 AC，属于体感瑕疵。
- 当前需求已处于收尾阶段，核心功能与门禁均已通过；为避免引入新的回归，本期暂不修复，单独登记 backlog。

## 可选修复方向

1. 检查 `frontend/src/pages/memory-inspector/MemoryList.tsx` 中 Tabs 与列表容器的状态切换逻辑，给列表区域加 `bg-background` 等明确背景色，避免透见底层紫色元素。
2. 检查 shadcn/ui Tabs / ScrollArea 的默认 token，确认是否有 `--accent` / `--muted` 类颜色在切换时短暂生效。
3. 若使用了 `Suspense` 或条件渲染，给空/加载状态提供无色或 skeleton 占位，避免默认紫色背景。
4. 通过 React DevTools Profiler 捕获切换帧，定位具体出现紫色矩形的 DOM 节点。
