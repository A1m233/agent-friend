# 010 · 桌面外壳与对话界面 - 进度

## 总体状态
<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
IN_PROGRESS

## 里程碑与任务列表

### M10.1 · 环境搭建
- [x] 前置：本机工具链就绪（Node 22.22.3 / pnpm 11.5.2 / Rust 1.94）
- [x] 初始化 `frontend/`：Vite 6 + React 19 + TS 5 + 接入 Tauri 2（`src-tauri/`）
- [x] 单 build 多 entry（`index.html` / `pet.html` / `chat.html`）+ `tauri.conf.json` 双窗口定义（pet / chat）
- [x] 接入 Tailwind 4（`@tailwindcss/vite`）+ CSS 变量主题骨架（`styles/` + `theme/{light,dark}`）+ shadcn/ui 初始化（`components.json` + `cn`）
- [x] 建立 `src/` 分层目录 + 基础请求层骨架（axios 实例 + `/healthz` 探活）
- [x] `scripts/frontend/`（install / dev / web / build）双端脚本 + 登记 `scripts/README.md`
- [x] 替换 `frontend/README.md` 占位
- [x] 验收 AC-M1.1 / AC-M1.2：`pnpm build` 三入口产物 OK、`cargo check` 通过含双窗口配置校验、web dev server 三入口均 200、脚本可跑；桌面端 `./scripts/frontend/dev.sh` 双窗口（桌宠窗 + 对话窗）目视渲染确认通过

### M10.2 · 界面大框架（占位形象 + 操作栏）
- [ ] 桌宠窗：透明 / 置顶 / 无边框 / 占位形象 + Live2D 挂载点占位
- [ ] Tauri spike：鼠标穿透 / hit-test / 拖拽 / 托盘（Win/Mac 实测校正）
- [ ] 操作栏浮层（hover/click，单按钮"打开对话界面"）
- [ ] 点按钮打开对话窗（本里程碑可占位空壳）
- [ ] 验收 AC-M2.1 / AC-M2.2 / AC-M2.3

### M10.3 · 传统对话页
- [ ] 自写 fetch-SSE 消费 AG-UI（`@ag-ui/core` 类型）+ conversation store
- [ ] 消息列表 + 输入发送 + 流式打字机 + markdown
- [ ] 工具调用展示 + 思考展示
- [ ] 历史会话列表 + 切换（meta REST）
- [ ] persona 展示与切换（P1）
- [ ] 拟人化错误兜底
- [ ] 验收 AC-M3.1 ~ AC-M3.5（对话类需真 LLM 授权）+ AC-G.1 / AC-G.2

## 实现日志
| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-10 | 切出 `feature/010-desktop-shell-and-chat-ui` 分支；提交需求/设计/决策文档（30bf973） | 进入 Phase 3 |
| 2026-06-10 | 工具链检查：Rust/cargo OK；Node 20（基线要求 22+）；pnpm 缺失 → 待开发者处理 | M10.1 前置阻塞 |
| 2026-06-10 | nvm 升级 Node 22.22.3 并设默认；`corepack enable pnpm`（11.5.2）→ 工具链就绪 | 前置解除 |
| 2026-06-10 | 落地 M10.1 工程骨架：Vite+React+TS+Tailwind4 多 entry、`src/` 分层、axios 请求层、`tauri init` 双窗口、`scripts/frontend/` 双端脚本 | `pnpm build` / `cargo check` / web 三入口均通过 |
| 2026-06-10 | 脚本健壮性：dev/web/build 复用 `_ensure-node` 守卫（Node<22 友好提示而非崩溃）；加 `frontend/.nvmrc=22`、`package.json` 的 `engines.node`/`packageManager` | 失败路径已验；旧终端 Node20 跑 dev 崩溃问题定位为环境（需 `nvm use 22`） |
