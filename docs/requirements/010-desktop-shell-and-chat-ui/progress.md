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
- [x] 桌宠窗：透明 / 置顶 / 无边框 / skipTaskbar / 无阴影；占位圆形形象 +`#pet-stage`（Live2D PixiJS canvas 未来挂载点）
- [x] 对话窗默认隐藏（`visible:false`）+ 关闭转隐藏（`on_window_event` 拦截 CloseRequested）；`macOSPrivateApi` 开启
- [x] Tauri spike：鼠标穿透 hit-test —— Rust 内置 `cursor_position` ~60fps 喂 `pet://cursor`，前端 `usePetPassthrough` 据 DOM 几何（`elementFromPoint`+`[data-hit]`）切 `setIgnoreCursorEvents`；命中判定留缝，Live2D 期换 alpha 采样
- [x] 形象拖拽（`startDragging`）+ 托盘菜单（打开对话 / 显示隐藏桌宠 / 退出）
- [x] 操作栏浮层（hover 显示，单按钮"打开对话界面" → `invoke('open_chat')` 显示并聚焦对话窗）
- [x] 验收 AC-M2.1 / AC-M2.2 / AC-M2.3：实机目视通过（透明悬浮 + 拖拽 + hover 操作栏；圆外直角区穿透 OK；按钮/托盘均能打开对话窗、关后可重开）。caveat：托盘在刘海屏菜单栏溢出时不可见（已确认创建成功，换大屏可见），属 macOS 显示限制

### M10.3 · 传统对话页
- [x] 自写 fetch-SSE 消费 AG-UI（`@ag-ui/core` 类型）+ conversation store
- [x] 消息列表 + 输入发送 + 流式打字机 + markdown
- [x] 工具调用展示（思考展示挂起 issue 002）
- [x] 历史会话列表 + 切换（meta REST）
- [~] persona 展示与切换（P1）——本期暂缓（前端无人格 CRUD，半功能割裂），已从 UI 撤下；见 requirement R-M3.5
- [x] 拟人化错误兜底
- [x] 验收 AC-M3.1~M3.4 通过（真 LLM 实机 + 错误兜底 mock 验收）；AC-M3.5 暂缓；AC-G.1/G.2 通过（scripts/check 全绿 + 未动后端）

## 实现日志
| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-10 | 切出 `feature/010-desktop-shell-and-chat-ui` 分支；提交需求/设计/决策文档（30bf973） | 进入 Phase 3 |
| 2026-06-10 | 工具链检查：Rust/cargo OK；Node 20（基线要求 22+）；pnpm 缺失 → 待开发者处理 | M10.1 前置阻塞 |
| 2026-06-10 | nvm 升级 Node 22.22.3 并设默认；`corepack enable pnpm`（11.5.2）→ 工具链就绪 | 前置解除 |
| 2026-06-10 | 落地 M10.1 工程骨架：Vite+React+TS+Tailwind4 多 entry、`src/` 分层、axios 请求层、`tauri init` 双窗口、`scripts/frontend/` 双端脚本 | `pnpm build` / `cargo check` / web 三入口均通过 |
| 2026-06-10 | 脚本健壮性：dev/web/build 复用 `_ensure-node` 守卫（Node<22 友好提示而非崩溃）；加 `frontend/.nvmrc=22`、`package.json` 的 `engines.node`/`packageManager` | 失败路径已验；旧终端 Node20 跑 dev 崩溃问题定位为环境（需 `nvm use 22`） |
| 2026-06-10 | 穿透方案定调：弃"小窗规避"，改"真穿透 + 命中缝"。Tauri 2 内置 `cursor_position` 免引 rdev；几何判定起步，Live2D 期只换 hitTest 为 alpha 采样 | 既 de-risk §4.4 最高风险项，又让 Live2D 升级局部化；设计 §4.7 不变 |
| 2026-06-10 | 落地 M10.2：tauri.conf 桌宠窗透明置顶无边框 + 对话窗隐藏 + macOSPrivateApi；lib.rs 加托盘 / `open_chat` / 光标喂给 / 关闭转隐藏；前端 `usePetPassthrough` + 桌宠页（拖拽 + 操作栏） | `pnpm build` / `cargo check` 通过；AC-M2.* 待实机目视 |
| 2026-06-10 | 穿透命中精度修正：`data-hit` 从外层容器挪到圆形象/按钮本身（`elementFromPoint` 尊重 border-radius），圆外直角区可穿透；按钮隐藏时 `pointer-events-none` 避免误触与挡穿透 | 实机确认圆外可穿透 |
| 2026-06-10 | 托盘加文字标签 🐾 + tooltip + 创建日志；日志证实托盘创建成功，刘海屏菜单栏溢出不可见（环境限制），换大屏可见 | M10.2 三条 AC 实机目视通过 |
| 2026-06-10 | 组件方案与 harness 修订：引入 tdesign-chat（design）、ESLint forbid-elements 门禁、Button 整改、requirement §3 同步 | commit cd1f6bd |
| 2026-06-10 | M10.3 起步：摸清 agent-bridge 契约（AG-UI run camelCase / SSE 事件 / meta REST / 数据结构）；R-M3.3 思考展示挂起登记 issue 002（bridge 无 reasoning 事件） | 进入 M10.3 实现 |
| 2026-06-10 | M10.3 实现：领域消息模型（types/chat）+ 自写 fetch-SSE（services/stream，@ag-ui/core 类型）+ meta API（sessions/personas）+ 四 store（conversation/sessions/personas/connection）+ AG-UI 事件 reducer + 历史投影 | types/services/stores 层，typecheck+lint 全绿 |
| 2026-06-10 | M10.3 UI：对话窗接入 @tdesign-react/chat 散件（ChatMessage 受控渲染 + ChatSender 输入）+ 历史侧栏 + persona 选择 + 连接态 + 拟人化错误兜底；领域模型→tdesign 内容块投影隔离 alpha 库 | 生产构建通过（chat bundle 2MB，仅告警） |
| 2026-06-10 | 核心逻辑单测（reducer 5 例 + 历史投影 2 例，vitest）+ scripts/frontend/test 双端 + 并入 scripts/check 第 4 步 | 7 测全过，check 前端段 lint+typecheck+test |
| 2026-06-10 | 关键发现：@tdesign-react/chat 实为 tdesign-web-components(alpha 1.3.0-alpha.2, Omi/shadow DOM) 的 React 封装；内容块模型与 AG-UI 近 1:1；自带 AG-UI engine 但按决策不用、仍自写数据流 | 待真 LLM 验收 AC-M3.* |
| 2026-06-10 | 真 LLM 验收回归：AC-M3.1 markdown 流式目视通过；AC-M3.3 历史通过。AC-M3.2 缺陷——后端确发 tool_call_request/result，但 tdesign `ChatMessage`(alpha) 不渲染 toolcall 块 → 按 design §4.5 降级：文本仍交 tdesign，工具卡片改自写 `ToolCard`，按 text→tool→text 顺序穿插（MessageList 改按块渲染，projection 收敛为只产 markdown 块） | lint+typecheck+test 全绿；待实机复验 |
| 2026-06-10 | AC-M3.5 定位：persona 切换接口实测 200、前端链路通；观感"没反应"因 catalog 仅 default 一个。曾加 user persona `coco` 作对照（已删） | —— |
| 2026-06-10 | 修 AC-M3.2 历史缺口：历史投影原丢弃工具事件，切走再切回卡片消失。sessionProjection 增投影 tool_call_request/result→工具块（args 对象序列化、[error] 前缀兜底），复用 reducer 的 TOOL_ERROR_PREFIX；补 2 个单测 | test 9 过 |
| 2026-06-10 | AC-M3.4 验收：补 conversation store 错误兜底 mock 单测（流式失败 / RUN_ERROR / 历史加载失败 3 路径），断言用户拿到拟人文案且不含 http/error/status/三位状态码等技术 token；测试 13 过 | 确定性逻辑，按 dev-workflow 用 mock 覆盖 |
| 2026-06-10 | 决策：persona 暂缓接入前端（前端无人格增删改查，仅"展示+切换"是半功能、列表更新还要手动刷页，体验割裂）。撤下 PersonaSelector + personas store/api + switchPersona + PersonaInfo 类型；删除演示用 coco.md。R-M3.5/AC-M3.5 标暂缓，待前端做人格 CRUD 再接 | lint+typecheck+test 全绿 |
