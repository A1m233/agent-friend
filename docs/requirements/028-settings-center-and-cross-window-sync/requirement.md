# 设置中心与跨窗口同步

## 状态

CONFIRMED

## 背景

027 在 `settings.html` 临时加了 light / dark 主题切换控件，存储用的是浏览器 `localStorage`。落地后暴露两个问题（详见 `docs/issues/023-settings-theme-follow-ups`）：

1. **跨窗口不同步**：设置窗里切主题，只有 `settings.html` 自身 `<html theme>` 变化；pet / bubble / chat / memory-inspector 这些独立 HTML 入口（见 `frontend/vite.config.ts` 的 `rollupOptions.input`）各有各的 document，不跟随。
2. **设置 UI 不像设置页**：019 留下的居中卡片骨架，被 027 顺手塞了主题切换控件，体感像 modal 而不是设置面板。

更本质的问题：项目目前**没有"设置中心"**，只有挂在 localStorage 上的临时开关。`localStorage` 在 Tauri 桌面端也不是偏好存储的合适载体——存储位置不在 OS 标准配置目录（macOS 在 `~/Library/WebKit/` 之下而非 `~/Library/Application Support/<app>/`）、跨 webview 实例的 storage 事件不可靠、运维 / 备份 / 卸载迁移都不友好。

模型选择 / API Key / 通知开关等设置项是未来必然要加的。再继续在 `localStorage` 上凑，到时只能整片推倒。本期把"配置中心 + 跨窗口同步 + 设置 UI shell"这套基础架构搭起来，主题作为首个落地的设置项从 `localStorage` 迁过来，023 issue 顺手清账。

## 目标

把设置存储下沉到 Tauri 客户端、跨窗口同步、UI 重做成可承载多组设置项的 shell。**只搭骨架与首个落地项，不扩业务设置项**。

可衡量：

- 在设置窗修改主题，**6 个 HTML 入口（index / pet / chat / bubble / settings / memory-inspector）所有当前打开的窗口立即生效**，无需重启、无需刷新。
- 重启应用后，设置项保持（持久化到 OS 标准配置目录下的文件）。
- 任一窗口启动时，**首帧即应用正确主题**，无 light → dark 闪屏（含冷启动与切主题后重启两种路径）。
- `localStorage` 不再承担设置存储职责，`useTheme` 旧实现连同 `agent-friend-theme` 这个 key 一并下线。
- 设置 UI 改为"左侧分类导航 + 右侧滚动内容"两栏布局（结构上参考企微示例，视觉走项目自己的 token），目前只承载"通用 → 主题"一项。
- 文档说明未来加新设置项（如模型选择 / API Key）应如何接入这套架构。

## 范围

### 包含

**配置中心架构（客户端侧）**：

- 单一权威源 = Tauri 主进程持有的配置文件，位于 OS 标准配置目录（macOS `~/Library/Application Support/<bundle>/`、Windows `%APPDATA%/<app>/`、Linux `~/.config/<app>/`）。
- 提供"读 / 写 / 订阅变更"三类能力，前端通过 Tauri command + event 调用。
- 跨窗口同步：写入后由主进程向所有 webview 广播变更事件。
- **首屏防闪**：window 创建时通过 webview 初始化脚本把当前配置同步注入到前端，前端启动直接读全局对象（不走异步 `invoke`，不走 `localStorage`）。

**前端 hook 抽象**：

- 提供一个统一的 hook（暂定 `useSetting`），屏蔽"读初值 / 监听变更 / 写后端"细节，业务侧只关心 key + value。
- 主题切换走该 hook，旧 `useTheme` + `initTheme` + `agent-friend-theme` localStorage 路径全部移除。
- hook 的能力边界与扩展模式在 design 阶段细化。

**设置 UI 重做**：

- 整体改为两栏 shell：左侧分类导航 + 右侧滚动内容区，顶部"设置"标题。
- **结构上**参考企业微信设置面板（截图见关联 issue），**视觉 token 走项目自己的设计变量**（高亮 / 边框 / 间距 / 字号），不引入企微蓝等外部颜色，暗色模式下不撕裂。
- 左侧导航当前**只列一项**"通用"，右侧内容当前**只承载主题项**——不挖假占位、不堆未来不会立刻落地的 tab 名。后续设置项接入时再扩。
- 移除 027 留下的 `min-h-screen flex items-center justify-center` 居中布局。

**文档预留扩展点**：

- `design.md` 中给一节"未来如何加一个新设置项"，以模型选择 / API Key 为参照说明完整链路（后端 schema、前端 hook、UI 接入点）。

**修 023 issue**：

- 023 issue 的 README 标注 "fix-in-028"，链接到本需求。028 实际合并后再 close 023。

### 不包含

- **不落地模型 / API Key 等设置项本身**：本期只搭骨架与示意，业务设置项作为独立需求。
- **不接入系统级状态**：窗口尺寸 / 位置 / 桌宠当前位置等不归属"用户偏好"语义，不进配置中心（继续维持现有机制）。
- **不做跨平台差异验证**：仅 macOS 本地验证。Windows / Linux 跨窗口同步与首屏注入路径理论上同源，但本期不开机验证，遗留作 follow-up。
- **不做 `prefers-color-scheme` 跟随**：沿用 027 决定，作为独立需求。
- **不重新设计视觉 / 不动 token**：UI 结构调整不改 token 体系，所有颜色 / 间距 / 字号继续走 027 抽取的设计 token。
- **不引入新的 settings 类目**："消息通知 / 日程 / 存储管理 / 快捷键 / Debug" 这些企微截图里的 tab 名一律不照搬，agent-friend 自己未来要加什么再加。
- **不动 bridge 后端**：本期配置完全在 Tauri 客户端侧，bridge（Python）不参与，不接 HTTP。

## 关联

- **触发 issue**：`docs/issues/023-settings-theme-follow-ups`
- **前序需求**：`docs/requirements/027-frontend-design-token-extraction`（建立了主题 token 与 027 的 `useTheme` 临时实现，本期替换）
- **相关入口**：`frontend/vite.config.ts` 列出的 6 个 HTML 入口
- **相关现有代码**：
  - `frontend/src/hooks/useTheme.ts`（027 落地的 localStorage 方案，本期下线）
  - `frontend/src/pages/settings/main.tsx` & `App.tsx`（设置窗入口与界面，本期重做）
  - `frontend/src-tauri/src/lib.rs`（已有 `emit_to` 用法可作跨窗口广播参考）

## 验收标准

- [ ] Tauri 主进程能从 OS 标准配置目录读写一份 JSON 配置文件；首次启动文件不存在时按默认值兜底。
- [ ] 设置窗切换主题 → pet / bubble / chat / memory-inspector / index / settings 6 个入口已打开窗口全部立即换肤，不需重启 / 刷新。
- [ ] 重启应用后主题持久化生效。
- [ ] 任一窗口冷启动首帧即应用正确主题，肉眼无 light → dark 闪屏（含修改主题后重启的路径）。
- [ ] `frontend/src/hooks/useTheme.ts` 不再读写 `localStorage`，旧 `agent-friend-theme` key 移除。
- [ ] 提供 `useSetting`（或等价命名）hook，主题切换走它。
- [ ] 设置 UI 为两栏布局：左侧导航（当前仅"通用"一项，高亮态走项目 token）+ 右侧"主题"切换区，无 027 居中卡片样式。
- [ ] 设置面板在 light / dark 主题下均无视觉撕裂（颜色 / 边框 / 间距均走项目 token）。
- [ ] `design.md` 含"未来如何加新设置项"一节，以模型 / Key 为参照说明完整链路。
- [ ] `docs/issues/023-settings-theme-follow-ups/README.md` 标注 fix-in-028。
- [ ] `scripts/check` 全过（含 027 已有的 colors / tokens guard 与前端 lint / typecheck / test）。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-23 | 创建需求文档（CONFIRMED） | - |
