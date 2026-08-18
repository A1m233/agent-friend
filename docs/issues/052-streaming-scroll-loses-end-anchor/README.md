# 052 · 流式回复增长时消息列表会中途丢失末端跟随

- **状态**：resolved
- **严重度**：medium
- **发现日期**：2026-08-18
- **关联需求**：[010-desktop-shell-and-chat-ui](../../requirements/010-desktop-shell-and-chat-ui/)

## 现象

用户已经位于消息列表底部并发送新消息后，列表最初会跟随正在增长的流式回复，但一段时间后可能停止在原位置；用户没有主动向上滚动，界面却显示“滚动到最新消息”按钮。点击按钮可以暂时回到底部。

当前实现使用 TanStack Virtual 的末端锚定能力：`anchorTo: "end"`、`followOnAppend: "auto"`、`scrollEndThreshold: 80`，并通过 `measureElement` 测量动态消息高度。依赖锁定为 `@tanstack/react-virtual 3.14.3` / `@tanstack/virtual-core 3.17.1`。

## 根因初判

高度吻合 TanStack Virtual 已在 `virtual-core 3.17.2` 修复的上游缺陷（TanStack/virtual#1209）：末端锚定列表的最后一项在 token streaming 中持续增高时，虚拟列表会在 React 尚未增长内部 size container 前写入滚动修正；浏览器将超出旧 `scrollHeight` 的 `scrollTop` 截断且不产生新的 scroll event。下一轮 `wasAtEnd` 判断读取到陈旧位置后失败，视口便逐渐偏离末端。

上游修复说明与本地症状一致，而当前安装的 `virtual-core 3.17.1` 正好早一个补丁版本。当前最新版变更记录已到 `@tanstack/react-virtual 3.14.9` / `@tanstack/virtual-core 3.17.7`：

- <https://github.com/TanStack/virtual/blob/main/packages/virtual-core/CHANGELOG.md>
- <https://github.com/TanStack/virtual/blob/main/packages/react-virtual/CHANGELOG.md>

此外，TanStack 当前官方 React chat 示例除了末端锚定与动态测高，还启用了 `directDomUpdates: true` 并把内部 size container 绑定到 `virtualizer.containerRef`，使容器尺寸和 item 位置直接在 DOM 中同步更新。项目当前尚未采用这一配置：

- <https://tanstack.com/virtual/latest/docs/framework/react/examples/chat>

## 影响与判断

- 破坏桌面端流式对话的核心阅读体验；用户无法仅凭按钮判断自己主动离开了底部，还是自动跟随发生故障。
- 长回复、Markdown 重新排版或单个消息跨越多屏时更容易暴露。
- 用户可通过点击“滚动到最新消息”临时规避，未发现消息数据丢失，因此严重度定为 medium，当前先登记不修。
- 现有 `messageScrollFollow` 单元测试只覆盖按钮布尔值与一次性滚动请求序号，没有驱动真实虚拟列表连续多帧增高，无法捕获该回归。

## 可选修复方向

- 优先将 `@tanstack/react-virtual` 升级到当前稳定补丁版本，使 `virtual-core` 至少包含 3.17.2 的末端流式测高修复；不要先用额外滚动 effect 掩盖上游缺陷。
- 升级后用真实虚拟列表夹具连续增大最后一项高度，验证滚动位置、`isAtEnd()` 与“滚动到最新消息”按钮始终一致。
- 若升级仍不能覆盖 Tauri WebView 下的时序，再对齐官方 chat 示例评估 `directDomUpdates` + `containerRef`；启用时必须让 virtualizer 独占内部容器高度和 item 主轴位置，避免 React style 与直接 DOM 写入互相覆盖。
- 回归测试同时覆盖：贴底时流式增长持续跟随、用户主动向上滚动后不被拉回、重新点击到底后恢复跟随，以及流式结束时 Markdown 组件切换不会再次脱底。

---

已在需求 085 当前分支合入前通过撤回未提交的 TanStack end-anchor 重构恢复主干滚动行为；对应变更将随需求 085 提交。
