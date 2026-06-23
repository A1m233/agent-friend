# 设置中心与跨窗口同步 - 技术方案

## 状态

CONFIRMED

## 需求文档

→ [requirement.md](./requirement.md)

## 现状分析

### 设置存储现状

| 项 | 现状 |
|---|---|
| 后端持久化机制 | 无。`src-tauri/Cargo.toml` 未引入 `tauri-plugin-store` / `tauri-plugin-fs` 等存储插件 |
| 主题持久化（027 临时方案） | 前端 `localStorage`，key = `agent-friend-theme`，位置不在 OS 应用配置目录 |
| 业务 setting 实例 | 只有 `theme: "light" \| "dark"` 一个 |
| 配置文件位置约定 | 项目内尚未约定；本期落在 OS app config dir（`tauri-plugin-store` 默认） |

### 前端入口与主题应用现状

| 入口 | HTML | `<html>` 默认 | 启动同步主题 |
|---|---|---|---|
| index | `index.html` | `theme="light"` | 无（devhub 调试入口） |
| pet | `pet.html` | `theme="light"` | 无 |
| chat | `chat.html` | `theme="light"` | 无 |
| bubble | `bubble.html` | `theme="light"` | 无 |
| settings | `settings.html` | `theme="light"` | 有，`main.tsx` 调 `initTheme()` 读 `localStorage` |
| memory-inspector | `memory-inspector.html` | `theme="light"` | 无 |

观察：只有 settings 入口跑了主题应用代码 —— 也就是说，**用户改主题后即便重启**，其他 5 个入口也只会渲染 light（HTML 默认）；这是当前实际行为。本期把"任一入口冷启动首帧即正确主题"补齐。

### 跨窗口事件机制现状

后端已有 `Emitter::emit_to(label, event, payload)` 用法（`push_subscriber.rs` / `bubble_window.rs` 用作 push channel 推流到 pet / bubble）。`Emitter::emit(event, payload)` 是全广播版本，跨所有 webview，本期复用此接口做 settings 变更广播。

### 027 useTheme 引用清点

| 文件 | 引用方式 | 本期处理 |
|---|---|---|
| `frontend/src/hooks/useTheme.ts` | 定义 `Theme` / `useTheme` / `initTheme` + STORAGE_KEY `agent-friend-theme` | **整文件删除** |
| `frontend/src/pages/settings/main.tsx` | `import { initTheme }; initTheme()` | 删除该调用 |
| `frontend/src/pages/settings/App.tsx` | `import { useTheme, type Theme }; useTheme()` | 改用 `useSetting('theme')` |

无其他业务文件 reference，破坏性删除安全。

### 依赖现状

| 依赖 | 状态 | 本期 |
|---|---|---|
| `tauri-plugin-store`（Cargo） | 未引入 | **新增** |
| `@tauri-apps/plugin-store`（npm） | 未引入 | 不引（前端不直接打 store，走 facade） |
| `sonner`（npm） | 未引入 | **新增** |
| shadcn `Sidebar` 组件 | 已存在（`components/ui/sidebar.tsx`，`chat/HistorySidebar` 已用） | 复用 |
| shadcn `Tabs` 组件 | 已存在（027 引入） | 复用 |
| shadcn `ScrollArea` 组件 | 已存在 | 复用 |

## 方案设计

### 关键思路

**两层架构**：

```
                 [Rust 主进程]
  tauri-plugin-store ── 持久化层（settings.json，OS app config dir）
        ↕
  agent-friend-settings facade plugin
   ├── 启动时同步 load 全量 settings
   ├── js_init_script(format!("window.__AGENT_FRIEND_SETTINGS__={}; document.documentElement.setAttribute('theme', '{}')", json, theme))
   ├── command get_setting / set_setting
   └── set 链路：写 store → app.emit("settings://changed", { key, value })
                        ↓ 广播
            ┌───────────┼───────────┐
       [pet webview]    [chat webview]    [settings webview]    …
        每个 webview 启动前，window.__AGENT_FRIEND_SETTINGS__ 已就位（零闪屏）
        listen("settings://changed") → setAttribute theme + 触发 React useSetting 重渲染
```

**核心选择理由**：

- **官方插件 `tauri-plugin-store` 作持久化层**：KV 语义 + autoSave/debounce + 跨平台 OS 配置目录由插件兜底。不重复造轮子。
- **自写 facade plugin 作"配置中心入口"**：因为官方 store 不暴露 `js_init_script` 注入口、也不带"按 key 广播变更"语义。facade 只是把 store 包一层 + 加 init script + 加 emit。代码量 ~ 100 行 Rust。
- **`js_init_script` 是 Tauri 2 全局 webview 注入的标准 API**：plugin builder 阶段注入的 String，对所有 webview（含 conf.json 静态创建的）在加载 HTML 之前同步执行，零 race / 零闪屏。
- **不动 conf.json 的 windows**：保留现有 pet NSPanel / bubble 复杂初始化路径不变。
- **6 个窗口都是启动时一次性创建**（chat/settings/memory-inspector 关闭走 `hide()`，不 destroy/recreate），所以 plugin builder 阶段静态拼一次 init script 就够；运行时切主题走 emit broadcast。

### 涉及文件

| 文件路径 | 改动类型 | 说明 |
|---|---|---|
| `frontend/src-tauri/Cargo.toml` | 修改 | 加 `tauri-plugin-store = "2"` 依赖 |
| `frontend/src-tauri/src/settings.rs` | **新增** | settings facade plugin：load / get / set / emit broadcast + js_init_script 构造 |
| `frontend/src-tauri/src/lib.rs` | 修改 | `.plugin(tauri_plugin_store::Builder::new().build())` + `.plugin(settings::init())`；commands 注册 `get_setting` / `set_setting` |
| `frontend/src-tauri/capabilities/default.json` | 修改 | settings 窗加入 `windows` 列表；按需加 store / facade command 的 permission 标识 |
| `frontend/package.json` | 修改 | 加 `sonner` 依赖 |
| `frontend/src/components/ui/sonner.tsx` | **新增** | shadcn sonner 组件包装（项目 token 化样式） |
| `frontend/src/components/ui/index.ts` | 修改 | re-export sonner |
| `frontend/src/lib/settings/index.ts` | **新增** | TS Settings 类型 + 默认值 + `useSetting` hook + `getInitialSettings()` 读 `window.__AGENT_FRIEND_SETTINGS__` |
| `frontend/src/lib/settings/listen.ts` | **新增** | 全局 listen `settings://changed`，应用副作用（如 theme → setAttribute）—— 在每个入口 main.tsx 调一次注册 |
| `frontend/src/hooks/useTheme.ts` | **删除** | 027 临时方案下线 |
| `frontend/src/pages/settings/main.tsx` | 修改 | 删 `initTheme()` 调用，改调 `registerSettingsListener()`，挂 `<Toaster />` |
| `frontend/src/pages/settings/App.tsx` | 修改 | 重做成两栏 shell：左 Sidebar（"通用"）+ 右 ScrollArea（"外观 / 主题"卡片），改用 `useSetting('theme')` |
| `frontend/src/pages/pet/main.tsx` / `chat/main.tsx` / `bubble/main.tsx` / `memory-inspector/main.tsx` / `devhub/main.tsx` | 修改（5 个入口） | 各 main.tsx 调一次 `registerSettingsListener()`，让主题切换运行时即时跟随 |
| `docs/issues/023-settings-theme-follow-ups/README.md` | 修改 | 顶部标注 "fix-in-028" + 链接到本需求 |
| `docs/requirements/028-.../progress.md` | **新增** | 进度追踪（Phase 3 创建） |

### 关键实现细节

#### A. Rust facade plugin（`settings.rs`）

```rust
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tauri::{
    plugin::{Builder as PluginBuilder, TauriPlugin},
    AppHandle, Emitter, Manager, Runtime,
};
use tauri_plugin_store::StoreExt;

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    pub theme: ThemeMode,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "lowercase")]
pub enum ThemeMode { Light, Dark }

impl Default for Settings {
    fn default() -> Self { Self { theme: ThemeMode::Light } }
}

const STORE_PATH: &str = "settings.json";
const EVENT_CHANGED: &str = "settings://changed";

/// 启动期同步加载（Tauri 在 builder 阶段调用本 fn 同步拼 js_init_script）。
/// store 不存在 / 解析失败 / 字段缺失 → Settings::default()，不阻断启动。
fn load_or_default<R: Runtime>(app: &AppHandle<R>) -> Settings {
    let store = match app.store(STORE_PATH) {
        Ok(s) => s,
        Err(e) => { log::warn!("settings: store open failed: {e}, using default"); return Settings::default(); }
    };
    let value = store.get("settings").unwrap_or_default();
    serde_json::from_value(value).unwrap_or_else(|e| {
        log::warn!("settings: parse failed: {e}, using default");
        Settings::default()
    })
}

fn build_init_script(s: &Settings) -> String {
    let json = serde_json::to_string(s).expect("settings serialize");
    // 主题先于业务代码 setAttribute，避免 React 启动前 html[theme] 不准。
    format!(
        r#"
        window.__AGENT_FRIEND_SETTINGS__ = {json};
        document.documentElement.setAttribute('theme', '{theme}');
        "#,
        json = json,
        theme = match s.theme { ThemeMode::Light => "light", ThemeMode::Dark => "dark" },
    )
}

#[tauri::command]
fn get_setting(state: tauri::State<'_, Arc<std::sync::Mutex<Settings>>>) -> Settings {
    state.lock().unwrap().clone()
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct SetPayload {
    key: String,
    value: serde_json::Value,
}

#[tauri::command]
fn set_setting<R: Runtime>(
    app: AppHandle<R>,
    state: tauri::State<'_, Arc<std::sync::Mutex<Settings>>>,
    payload: SetPayload,
) -> Result<(), String> {
    let mut s = state.lock().map_err(|e| e.to_string())?;
    // 按 key dispatch：未来加字段在此扩
    match payload.key.as_str() {
        "theme" => {
            let v: ThemeMode = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid theme: {e}"))?;
            s.theme = v;
        }
        _ => return Err(format!("unknown setting key: {}", payload.key)),
    }
    // 写盘
    let store = app.store(STORE_PATH).map_err(|e| e.to_string())?;
    store.set("settings", serde_json::to_value(&*s).map_err(|e| e.to_string())?);
    store.save().map_err(|e| e.to_string())?;
    // 广播
    app.emit(EVENT_CHANGED, serde_json::json!({ "key": payload.key, "value": payload.value }))
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn init<R: Runtime>() -> TauriPlugin<R> {
    // 注意：plugin builder 阶段尚无 AppHandle —— 加载 settings 与构造 init_script 必须在
    // tauri::Builder::default().setup() 之前完成。本 init 不接 app handle，加载放 .setup() 内。
    // 但 js_init_script 必须在 plugin 构造时给 —— 矛盾。
    //
    // 解法：把 load + init_script 计算上移到 lib.rs run() 内，先用一次性 AppHandle wrapper
    // （tauri 提供 `tauri::Builder::default().build_app()` 路径或更轻量地用全局 OnceCell
    // 缓存配置文件路径 + 同步读 JSON）。最干净的实现见下方"启动时序"小节。

    PluginBuilder::new("agent-friend-settings")
        .invoke_handler(tauri::generate_handler![get_setting, set_setting])
        .build()
}
```

**注**：上方代码是骨架，**最终实现**按下方"启动时序"小节调整 init_script 注入路径。

#### B. 启动时序（lib.rs `run()`）

`js_init_script` 必须在 plugin 构造阶段给定字符串，而 `AppHandle` 只在 setup 内可拿。两者错位的解法：

1. **同步读 store 文件路径，不依赖 AppHandle**：`tauri-plugin-store` 默认把文件存在 `app_config_dir`，可通过 `dirs::config_dir()` + bundle identifier 拼出绝对路径，启动同步读 JSON
2. 拼 `init_script` 字符串
3. 用 `tauri::plugin::Builder::new("agent-friend-settings").js_init_script(init_script).invoke_handler(...).build()`
4. `.setup()` 内 `app.manage(Arc<Mutex<Settings>>::new(...))`，把同步读到的 Settings 也放进 state（注：与步骤 1 读到的同一份），供 `get_setting` 读

```rust
// lib.rs run()
pub fn run() {
    // 启动同步加载 settings（不依赖 AppHandle）
    let bootstrap_settings = settings::load_from_disk_or_default();  // 用 dirs + bundle id 拼路径直接读 JSON
    let init_script = settings::build_init_script(&bootstrap_settings);
    let state = Arc::new(std::sync::Mutex::new(bootstrap_settings));

    let builder = tauri::Builder::default()
        .manage(state.clone())
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(
            tauri::plugin::Builder::new("agent-friend-settings")
                .js_init_script(init_script)
                .invoke_handler(tauri::generate_handler![settings::get_setting, settings::set_setting])
                .build()
        )
        // 既有 nspanel / log / 等 plugin 链不变
        ...
        .invoke_handler(tauri::generate_handler![
            open_chat, open_settings, hide_pet, /* ... 既有 commands */
        ])
        ...;
    builder.run(tauri::generate_context!()).expect("...");
}
```

**注意 invoke_handler 合并**：Tauri 一个 builder 只能调用一次 `.invoke_handler()`。本期把 `get_setting` / `set_setting` 注册到 facade plugin 的 `invoke_handler` 内（plugin 自己的 handler），不与 `lib.rs` 的 main handler 冲突。

#### C. 前端 Settings 类型与 hook

```ts
// frontend/src/lib/settings/index.ts
import { useEffect, useState, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { toast } from "sonner";

export type ThemeMode = "light" | "dark";
export interface Settings {
  theme: ThemeMode;
}
export const DEFAULT_SETTINGS: Settings = { theme: "light" };

const EVENT_CHANGED = "settings://changed";

declare global {
  interface Window {
    __AGENT_FRIEND_SETTINGS__?: Settings;
  }
}

/** 启动时同步读，永远 ready（由 facade plugin js_init_script 注入）。 */
export function getInitialSettings(): Settings {
  return { ...DEFAULT_SETTINGS, ...(window.__AGENT_FRIEND_SETTINGS__ ?? {}) };
}

/**
 * 单 key 订阅 + 写入。乐观更新：本地 state 立刻变 → invoke → 失败回滚 + toast。
 * 同时 listen 广播事件，让其他窗口的写入反映到本窗口。
 */
export function useSetting<K extends keyof Settings>(
  key: K,
): [Settings[K], (next: Settings[K]) => Promise<void>] {
  const [value, setValueState] = useState<Settings[K]>(() => getInitialSettings()[key]);

  useEffect(() => {
    let mounted = true;
    const unlistenPromise = listen<{ key: string; value: Settings[K] }>(EVENT_CHANGED, (e) => {
      if (!mounted) return;
      if (e.payload.key === key) setValueState(e.payload.value);
    });
    return () => {
      mounted = false;
      void unlistenPromise.then((un) => un());
    };
  }, [key]);

  const setValue = useCallback(
    async (next: Settings[K]) => {
      const prev = value;
      setValueState(next); // 乐观更新
      try {
        await invoke("set_setting", { payload: { key, value: next } });
      } catch (e) {
        setValueState(prev);
        toast.error("设置保存失败", { description: String(e) });
      }
    },
    [key, value],
  );

  return [value, setValue];
}
```

#### D. 入口全局监听（`lib/settings/listen.ts`）

```ts
import { listen } from "@tauri-apps/api/event";

/**
 * 各入口 main.tsx 调一次。监听 settings://changed，把"必须在 DOM 上立刻生效"的副作用
 * （目前只有 theme → setAttribute）独立于 React 树执行 —— 即便某个入口没用 useSetting
 * 也能跟随主题。
 */
export function registerSettingsListener() {
  void listen<{ key: string; value: unknown }>("settings://changed", (e) => {
    if (e.payload.key === "theme") {
      const theme = e.payload.value === "dark" ? "dark" : "light";
      document.documentElement.setAttribute("theme", theme);
    }
  });
}
```

每个 main.tsx 在 `createRoot()` 前后调一次（先于 React 渲染或与之并行都可，因为 init script 已经设了首帧 `theme`，本 listener 只服务运行时）。

#### E. 设置 UI shell（`pages/settings/App.tsx`）

结构（伪代码）：

```tsx
import { SidebarProvider, Sidebar, SidebarHeader, SidebarContent, SidebarMenu,
         SidebarMenuItem, SidebarMenuButton, ScrollArea, Tabs, TabsList, TabsTrigger } from "@/components/ui";
import { Sun, Moon } from "lucide-react";
import { useSetting } from "@/lib/settings";

export function SettingsApp() {
  const [theme, setTheme] = useSetting("theme");
  return (
    <SidebarProvider style={{ "--sidebar-width": "200px" }}>
      <Sidebar collapsible="none" className="border-r border-border bg-bg">
        <SidebarHeader>
          <h1 className="px-2 pt-2 text-lg font-semibold text-fg">设置</h1>
        </SidebarHeader>
        <SidebarContent>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton isActive>通用</SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarContent>
      </Sidebar>
      <main className="flex-1 bg-bg text-fg">
        <ScrollArea className="h-screen">
          <div className="mx-auto max-w-2xl p-8 flex flex-col gap-6">
            <h2 className="text-xl font-semibold">通用</h2>
            <section className="rounded-lg border border-border bg-surface/50 p-5 flex flex-col gap-4">
              <h3 className="text-sm font-medium text-muted">外观</h3>
              <div className="flex items-center justify-between">
                <span className="text-sm">主题</span>
                <Tabs value={theme} onValueChange={(v) => void setTheme(v as ThemeMode)}>
                  <TabsList>
                    <TabsTrigger value="light"><Sun className="size-4" /></TabsTrigger>
                    <TabsTrigger value="dark"><Moon className="size-4" /></TabsTrigger>
                  </TabsList>
                </Tabs>
              </div>
            </section>
          </div>
        </ScrollArea>
      </main>
    </SidebarProvider>
  );
}
```

- 左侧 Sidebar `collapsible="none"` 固定 200px，与 `chat/HistorySidebar` 同款 pattern
- 高亮态走 `SidebarMenuButton isActive`（sidebar 组件内部用 token 化的 accent 色，shadcn 已映射到项目变量）
- 右侧 `ScrollArea` + `max-w-2xl` 居中内容栏（参考企微"左导航 + 右内容居中"层级）
- 分组卡片（"外观"）+ 卡片内 row 是后续设置项一致的容器模式

#### F. sonner 集成

shadcn 提供 `npx shadcn@latest add sonner`，会生成 `components/ui/sonner.tsx`（一个套了项目主题 token 的 Toaster 包装）。手动等价物：

```tsx
// frontend/src/components/ui/sonner.tsx
import { Toaster as Sonner } from "sonner";

export function Toaster() {
  return (
    <Sonner
      theme="light"   // 由 html[theme] 决定，sonner 内部走 css var，无需动态 prop
      toastOptions={{
        classNames: {
          toast: "bg-surface text-fg border border-border",
          // …其他映射继续走项目 token，避免 sonner 默认蓝绿
        },
      }}
    />
  );
}
```

`pages/settings/main.tsx` 挂载：

```tsx
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SettingsApp />
    <Toaster />
  </StrictMode>,
);
```

其他入口不挂 Toaster（写设置只发生在 settings 窗，失败 toast 只需在 settings 窗显示）。

### 未来如何加新设置项（模型选择 / API Key 为参照）

以"新增 `modelProvider: 'anthropic' | 'openai'`" 为例：

1. **Rust schema**：在 `settings.rs::Settings` struct 加字段 + 在 `Default::default()` 给默认值 + 在 `set_setting` match 加一个 `"modelProvider" => { ... }` 分支
2. **TS schema**：在 `lib/settings/index.ts::Settings` interface 加字段 + 在 `DEFAULT_SETTINGS` 给默认值
3. **可选：在 `listen.ts` 加副作用**（如 `modelProvider` 切换需要清缓存）。如无副作用可跳过
4. **UI**：`SettingsApp` 内加一个 row（或一个 SidebarMenuItem 新分类），用 `useSetting('modelProvider')` 拿值 + setter
5. **API Key 等敏感值**：本期不引入，未来若要存敏感值应走 OS keychain 而非明文 store；届时 facade plugin 加一个 `set_secret` command 走 `keyring` crate，与 `set_setting` 并列

## 影响分析

### 上下游影响

| 范围 | 影响 |
|---|---|
| 现有 027 主题切换功能 | 切换控件迁到新 UI shell，行为等价（点击切主题立刻生效）；持久化由 `localStorage` 改为 `settings.json` —— 用户视角无差异 |
| 6 个 HTML 入口启动 | 各 `main.tsx` 增加 `registerSettingsListener()` 调用；HTML 入口 `<html theme="light">` 默认值会被 js_init_script 立刻覆盖 |
| `useTheme` 旧 API | 整文件删除。无业务依赖（grep 已确认） |
| Tauri capabilities | settings 窗加入 `default.json` 的 `windows` 列表；新增 commands 走 `core:default` 已覆盖（同 chat / pet 模式） |
| 既有 push channel 用法 | 不受影响。`emit("settings://changed", ...)` 与 `emit_to("pet", "agent://push", ...)` 事件命名空间互不冲突 |
| 跨平台 | macOS 本地完整验证；Windows / Linux 路径由 `tauri-plugin-store` 与 `dirs::config_dir()` 兜底，不本地验证（沿用 015 / 016 同款"先 macOS、其他先不阻塞"策略） |

### 风险点

| 风险 | 描述 | 缓解 |
|---|---|---|
| **R1：`js_init_script` 注入时机实测** | Tauri 文档表述为"对每个 webview 在加载前同步执行"，但跨多 HTML entry 是否完全无闪屏需要实测 | Phase 3 启动 dev 后，强制 dark 主题 + 重启，逐窗口（含全开 6 个）目测首帧；如发现单帧 light 闪，回退方案：把 `<html theme="light">` 改为不写 theme，CSS 给"无 theme 时 dark-ish 中性色"避免视觉冲突。**真有问题再上 `conf.json create:false + builder` 路径** |
| **R2：`tauri-plugin-store` 写盘失败** | 文件权限、磁盘满、并发 lock 等异常 | facade `set_setting` 返回 `Result::Err`，前端 `useSetting` 回滚 state + `sonner toast.error` |
| **R3：bootstrap 同步读 store 文件 vs 插件 API 不一致** | 我们用 `dirs::config_dir()` + bundle id 算路径直接读 JSON，绕过 plugin-store API；如 plugin-store 未来改路径策略可能漂移 | 1) 锁版本 `tauri-plugin-store = "2"`；2) Phase 3 实测 plugin-store 实际写盘路径是否与我们 bootstrap 读路径一致；3) 一旦不一致退化到 plugin builder `setup` 内异步读 + on_webview_ready eval（带 race 但兜底可用） |
| **R4：Settings struct 双侧 schema 漂移** | Rust 与 TS 各一份，加字段时漏一边 | 本期就一个 `theme` 字段，注释互锁 + AC 列出"每个 setting 字段两侧 schema 都要更新"；多字段后引 `ts-rs` / `tauri-specta` |
| **R5：bootstrap 读盘 panic** | 文件存在但格式损坏 / JSON 解析失败 → 启动 panic | `load_from_disk_or_default` 所有错误路径都返回 `Settings::default()`，启动不阻断 |
| **R6：旧 localStorage 残留** | 用户机器上残留 `agent-friend-theme` key 不会自动清 | 容忍：旧 key 不再被任何代码读，永久无害残留；不写迁移代码（增复杂度无收益） |

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|------|---------|----------------|
| 2026-06-23 | 创建技术方案文档（CONFIRMED） | - |
