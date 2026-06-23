# 023 · settings 主题切换后续两点优化

- **状态**：resolved
- **严重度**：medium
- **发现日期**：2026-06-23
- **关联需求**：027-frontend-design-token-extraction、028-settings-center-and-cross-window-sync

## 现象

在 027 需求扩展中加入的 settings.html 主题切换功能，实际体验上有两个问题：

1. **设置页布局不自然**：当前 `SettingsApp` 使用 `min-h-screen flex flex-col items-center justify-center`，把整个设置卡片居中在屏幕中央。作为设置窗口，内容应当从顶部开始、左对齐排列，居中布局显得突兀。
2. **主题切换只作用于当前窗口**：在 settings.html 里切换 light/dark 后，仅 `settings.html` 自身的 `html[theme]` 发生变化。其他窗口（如 actionbar / pet / chat / memory-inspector）是独立的 HTML 入口，有各自的 document，不会跟随变化。用户预期是在设置里切主题会全局生效。

## 根因初判

1. 布局问题是 019 需求留下的占位骨架（`items-center justify-center`），027 扩展时只往里面塞了主题切换控件，没有重新设计布局。
2. 多窗口同步问题是因为前端有 5 个独立 HTML 入口（见 `frontend/vite.config.ts` 的 `rollupOptions.input`），每个入口的 `<html>` 都是独立的。当前 `useTheme` 只读写 `localStorage` 并操作当前 document，没有跨窗口同步机制。

## 影响与判断

- 布局问题属于体感瑕疵，不影响功能。
- 跨窗口不同步是明显的产品体验问题：用户在设置里切主题，预期是全局换肤，但其他窗口仍保持原主题。
- 暂不修的原因：跨窗口同步需要后端/Tauri 侧参与（统一配置存储 + 窗口间广播），已超出 027 "前端设计 token 规则一致性" 的范围。应作为独立需求处理。

## 可选修复方向

1. **布局**：把 `SettingsApp` 改成常规设置页布局——顶部标题、下方设置项列表左对齐、合理最大宽度（如 `max-w-xl mx-auto`）。
2. **跨窗口同步**：
   - Tauri 侧提供配置读写 + 事件广播（`emit`/`listen`），把主题存在后端配置或文件中。
   - 各入口启动时读取同一配置，并在收到变更事件时更新 `html[theme]`。
   - 纯前端临时方案：监听 `storage` 事件 + 各入口启动时读 `localStorage`，但 Tauri 多窗口的 localStorage 是否共享需验证，且桌面端通常应走后端配置。

## 修复指向

本 issue 由 [028-settings-center-and-cross-window-sync](../../requirements/028-settings-center-and-cross-window-sync/) 修复：

- 设置存储下沉到 Tauri 客户端 `tauri-plugin-store`，持久化到 OS 标准配置目录。
- 主题切换通过 `settings://changed` 事件向所有 webview 广播，6 个 HTML 入口实时同步。
- 设置 UI 重做为左侧分类导航 + 右侧滚动内容的两栏 shell。
- 028 merge 后再 close 本 issue。

已在 commit `295d030`（docs(issues): 更新 023 状态并登记 024 tdesign-chat 主题适配）中关闭。
