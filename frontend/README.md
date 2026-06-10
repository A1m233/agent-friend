# frontend

`agent-friend` 的**桌面前端**模块（Tauri 2 桌宠外壳 + 对话界面）。

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
│   ├── pages/{pet,chat,devhub}/        # 各窗口入口（main.tsx + App.tsx）
│   ├── components/ui/                  # shadcn/ui copy-in 纯 UI 组件
│   ├── stores/                         # Zustand store（会话 / 历史 / persona / 连接）
│   ├── services/                       # 基础请求层（axios + fetch-SSE）
│   ├── styles/                         # index.css + theme/（CSS 变量换肤）
│   ├── hooks/ utils/ types/ constants/
│   └── ...
└── src-tauri/                          # Tauri Rust 工程 + tauri.conf.json（双窗口）
```

## 窗口模型

两个 Tauri 窗口（解耦、便于调试）：

- **pet**（`pet.html`）：桌宠悬浮窗，后续透明 / 置顶 / 无边框 / 鼠标穿透（M10.2）。
- **chat**（`chat.html`）：常规对话窗，承载传统对话页（M10.3）。

跨窗口状态以 bridge 为唯一真相源，前端 store 仅为各窗口视图缓存，靠 Tauri 事件做失效通知后各自重新拉取。

## 与后端通信

通过 `agent-bridge`（默认 `http://127.0.0.1:18800`）的 HTTP + SSE：REST 走 meta / 会话接口，对话走 `POST /ag-ui/run`。详见需求 [`006-agent-bridge`](../docs/requirements/006-agent-bridge)。
