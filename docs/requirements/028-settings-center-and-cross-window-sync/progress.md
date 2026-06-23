# 设置中心与跨窗口同步 - 进度

## 总体状态

COMPLETED

## 实施顺序

按 design.md 的依赖关系：**Rust 后端骨架 → 前端 hook 与监听 → 入口接线 → UI 重做 → 老代码下线 → 文档收尾 → 实测验证**。

## 任务列表

### A. 依赖准备

- [x] A1：`frontend/src-tauri/Cargo.toml` 新增 `tauri-plugin-store = "2"` 依赖
- [x] A2：`frontend/package.json` 新增 `sonner` 依赖（`pnpm add sonner`）

### B. Rust 后端

- [x] B1：新增 `frontend/src-tauri/src/settings.rs`：
  - `Settings` struct + `Default` impl（仅 `theme: ThemeMode`）
  - `ThemeMode` enum（`Light` / `Dark`）
  - `load_from_disk_or_default()`：用 `dirs::data_local_dir()` + bundle id（`com.agentfriend.desktop`）算路径，与 `tauri-plugin-store` 默认写盘路径保持一致；同步读 `settings.json` 内 `"settings"` key；所有错误路径返回 `Settings::default()`
  - `build_init_script(&Settings) -> String`：拼 `window.__AGENT_FRIEND_SETTINGS__ = {...}; document.documentElement.setAttribute('theme', '...');`
  - `get_setting` command（读 state）
  - `set_setting` command（按 key dispatch 写 state → 写 store → `app.emit("settings://changed", { key, value })`）
  - `pub fn init<R>() -> TauriPlugin<R>`：plugin builder + `invoke_handler(get_setting, set_setting)`（不在此处给 js_init_script，由 lib.rs 决定）
- [x] B2：修改 `frontend/src-tauri/src/lib.rs`：
  - `run()` 入口 bootstrap：`let bootstrap = settings::load_from_disk_or_default(); let init_script = settings::build_init_script(&bootstrap);`
  - `.manage(Arc<Mutex<Settings>>::new(bootstrap))`
  - `.plugin(tauri_plugin_store::Builder::new().build())`
  - `.plugin(tauri::plugin::Builder::new("agent-friend-settings").js_init_script(init_script).invoke_handler(tauri::generate_handler![settings::get_setting, settings::set_setting]).build())`
  - 模块声明 `mod settings;`
- [x] B3：修改 `frontend/src-tauri/capabilities/default.json`：把 `"settings"` 加入 `windows` 列表（与 pet / chat / bubble 并列）；新增 commands 走 `core:default` 已覆盖，不另加 permission

### C. 前端核心

- [x] C1：新增 `frontend/src/lib/settings/index.ts`：
  - `export type ThemeMode`
  - `export interface Settings`
  - `export const DEFAULT_SETTINGS`
  - `getInitialSettings()`：读 `window.__AGENT_FRIEND_SETTINGS__`
  - `useSetting<K>(key)`：初值 + listen 广播 + 乐观 setter + 失败回滚 + `sonner toast.error`
- [x] C2：新增 `frontend/src/lib/settings/listen.ts`：
  - `registerSettingsListener()`：listen `settings://changed`，对 `theme` 做 `setAttribute`
- [x] C3：新增 `frontend/src/components/ui/sonner.tsx`：
  - Toaster 组件包装 + `toastOptions.classNames` 映射项目 token（背景 / 前景 / 边框）
- [x] C4：修改 `frontend/src/components/ui/index.ts`：re-export sonner

### D. 入口接线（6 个 main.tsx）

每个入口在 `createRoot()` 之前调一次 `registerSettingsListener()`：

- [x] D1：`frontend/src/pages/pet/main.tsx`
- [x] D2：`frontend/src/pages/chat/main.tsx`
- [x] D3：`frontend/src/pages/bubble/main.tsx`
- [x] D4：`frontend/src/pages/memory-inspector/main.tsx`
- [x] D5：`frontend/src/pages/devhub/main.tsx`
- [x] D6：`frontend/src/pages/settings/main.tsx`（同步删 `initTheme()` 调用，挂 `<Toaster />`）

### E. 设置 UI 重做

- [x] E1：修改 `frontend/src/pages/settings/App.tsx`：
  - 移除 `min-h-screen flex items-center justify-center` 居中布局
  - 改用 `SidebarProvider + Sidebar (collapsible="none") + ScrollArea`
  - 左侧顶部 "设置" 标题；菜单项 "通用"（`SidebarMenuButton isActive`）
  - 右侧：分类标题 "通用" + 分组卡片 "外观" + "主题" row + `Tabs`（Sun/Moon 图标）
  - 改用 `useSetting('theme')` 代替 `useTheme()`
  - 视觉 token 全部走 027 抽好的项目变量

### F. 老代码下线

- [x] F1：删除 `frontend/src/hooks/useTheme.ts`（grep 已确认无其他 reference）
- [x] F2：确认 `agent-friend-theme` 这个 localStorage key 不再被任何代码读写（不写迁移代码、容忍残留）

### G. 文档收尾

- [x] G1：修改 `docs/issues/023-settings-theme-follow-ups/README.md`：
  - 顶部 "状态" 改为 `open · fix-in-028`
  - 末尾加一段 "本 issue 由 028 修复，028 merge 后再 close"，链接到 `docs/requirements/028-settings-center-and-cross-window-sync/`
- [x] G2：本 `progress.md` 总体状态改为 `IN_PROGRESS`（开干即改）→ 全部完成且通过验收后改 `COMPLETED`

### H. 实测验证（含 design.md 列的风险点 R1 / R3）

- [x] H1：在 macOS 启动 dev，settings 窗切到 dark → 重启 app → 6 个窗口全部首帧即 dark，**无 light 闪屏**（design R1）
- [x] H2：切主题时，**当前已打开的所有窗口**（含 pet / chat / bubble / memory-inspector / settings）立即换肤，无需重启或刷新（AC 核心目标）
- [x] H3：验证 `tauri-plugin-store` 实际写盘路径与 bootstrap `load_from_disk_or_default` 读路径一致（design R3）；写一段一次性 log 输出 store 实际路径与 bootstrap 读路径，对比
- [x] H4：手动构造 `settings.json` 损坏（非法 JSON）→ 启动不 panic，回退 default
- [x] H5：构造 facade `set_setting` 写盘失败（如手动 chmod 配置目录只读）→ 前端 `sonner toast.error` 弹出 + state 回滚
- [x] H6：跑 `scripts/check` 全过（含 027 已有的 colors / tokens guard、frontend lint / typecheck / test、Rust cargo check）

### I. 进入验收

- [x] I1：requirement.md AC 逐项核对（共 10 条）
- [x] I2：用户明确确认验收完成 → 本文件状态改 `COMPLETED`

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-23 | Rust facade plugin + settings.rs / lib.rs / capabilities；前端 settings 模块、sonner 组件、6 入口接线、settings UI 重做、useTheme 下线、023 issue 标 fix-in-028 | Phase 3 实现中 |
| 2026-06-23 | 修复 bootstrap 读路径：由 `dirs::config_dir()` 改为 `dirs::data_local_dir()`，与 `tauri-plugin-store` 默认路径一致；lib.rs setup 中补打 bootstrap theme log | R3 路径不一致修复 |
