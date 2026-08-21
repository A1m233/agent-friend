# frontend

`agent-friend` 的**桌面前端**模块（Tauri 2 桌宠外壳 + 统一主窗口）。

它既是当前可用的"正经前端"，也是未来桌宠形态的前端层。详见需求 [`docs/requirements/010-desktop-shell-and-chat-ui`](../docs/requirements/010-desktop-shell-and-chat-ui)。

## 技术栈

- **Tauri 2**（桌面外壳，多窗口）+ **Rust**
- **React 19** + **TypeScript 5** + **Vite 6**
- **Tailwind CSS 4**（`@tailwindcss/vite`，主题走 CSS 变量）
- **shadcn/ui**（copy-in 纯 UI 组件）+ **Zustand**（状态）+ **Axios**（REST）
- 流式：自写 fetch-SSE 消费 AG-UI（类型来自 `@ag-ui/core`）

详见决策 [`0003-frontend-stack-and-phase1-kickoff`](../docs/decisions/0003-frontend-stack-and-phase1-kickoff/README.md)。

## 起步

> 任何"非写代码"操作走 `scripts/`，不要手记 `pnpm ...` 长命令。

> 根目录的 `./scripts/setup/run.sh` 已会一并装前端依赖；下面是只想单独操作前端时用。

| 操作 | mac/linux | windows |
|---|---|---|
| 安装依赖（也可用根 `setup`） | `./scripts/frontend/install.sh` | `.\scripts\frontend\install.ps1` |
| 桌面端开发（双窗口） | `./scripts/frontend/dev.sh` | `.\scripts\frontend\dev.ps1` |
| 仅 web 调试（浏览器） | `./scripts/frontend/web.sh` | `.\scripts\frontend\web.ps1` |
| 构建安装包 | `./scripts/frontend/build.sh` | `.\scripts\frontend\build.ps1` |

前置工具链：Node 22+、pnpm（`corepack enable pnpm`）、Rust（Tauri 需要）。

## 目录结构

```text
frontend/
├── index.html / pet.html / chat.html   # 多窗口 = 多 entry（单 build）
├── vite.config.ts                      # 多 entry + tailwind 插件
├── src/
│   ├── pages/{pet,chat,...}/            # 窗口入口与主窗口内容 surface
│   ├── components/ui/                  # shadcn/ui copy-in 纯 UI 组件
│   ├── stores/                         # Zustand store（会话 / 历史 / persona / 连接）
│   ├── services/                       # 基础请求层（axios + fetch-SSE）
│   ├── styles/                         # index.css + theme/（CSS 变量换肤）
│   ├── hooks/ utils/ types/ constants/
│   └── ...
└── src-tauri/                          # Tauri Rust 工程 + tauri.conf.json（双窗口）
```

## 窗口模型

产品窗口按交互形态拆分：

- **pet**（`pet.html`）：透明、置顶、无边框的桌宠悬浮窗。
- **bubble**（`bubble.html`）：跟随桌宠的独立气泡窗。
- **chat**（`chat.html`）：统一主窗口；左侧切换会话、设置、IM 接入和 owner-mode 记忆面板，右侧展示对应内容。
- **voice-call**（`voice-call.html`）：独立的语音通话小窗。

设置、IM 接入和 Memory Inspector 作为 `chat` 主窗口内的平级内容 surface，不再各自创建普通内容窗口。跨窗口状态以 bridge / Tauri 主进程为真相源，前端 store 仅为视图缓存。

## 与后端通信

通过 `agent-bridge`（默认 `http://127.0.0.1:18800`）的 HTTP + SSE：REST 走 meta / 会话接口，对话走 `POST /ag-ui/run`。详见需求 [`006-agent-bridge`](../docs/requirements/006-agent-bridge)。
