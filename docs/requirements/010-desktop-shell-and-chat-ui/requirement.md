# 010 · 桌面外壳与对话界面（desktop shell + chat UI）

> Desktop Shell & Chat UI
>
> 做一个正经面向使用的前端：以 Tauri 桌面外壳承载「桌宠悬浮窗（占位形象 + 操作栏）」与「传统 chatbot 对话界面」，接 `agent-bridge` 跑通对话。它本身即未来桌宠形态的前端层，本期只交付最低成本的形态层骨架（L2 收窄版）。

---

## 状态

<!-- DRAFT | CONFIRMED -->
CONFIRMED

---

## 1. 背景与价值

### 1.1 现状

[001](../001-foundation-chat-and-memory/requirement.md) ~ [006](../006-agent-bridge/requirement.md) 落地后，agent 引擎已具备完整核心能力，并通过 `agent-bridge` 以双协议（OpenAI / AG-UI）HTTP+SSE 对外暴露。但目前**只有 `agent-cli`（含 `--bridge` 模式）一种调试入口**——没有任何面向使用的图形界面。

### 1.2 这次要做什么

本期做一个**正经面向使用的前端**（区别于 [`0001 M3`](../../decisions/0001-product-vision-and-roadmap/README.md) "不投入设计资源"的调试 UI、和 006 用 `agent-cli --bridge` 顶替的 web 调试）。

由于 Tauri 应用 = Web 前端 + Rust 壳，这次用 Web 栈做的对话界面**本身就是未来桌宠的前端层**，演进是叠加而非替换。因此核心命题是：**既要能快速验证对话体验，又要为将来的桌宠形态平滑留口子，避免推倒重来。**

### 1.3 立项依据

- 范围取舍来源：`docs/explorations/initial-frontend/`（L2 收窄版骨架、2 窗口模型、对话 UI 数据流自写）。
- 项目级前提已由 [`0003-frontend-stack-and-phase1-kickoff`](../../decisions/0003-frontend-stack-and-phase1-kickoff/README.md) 锁定：前端框架 = React、构建 = Vite、沿用 0002 §3.7 版本基线；并显式记录"Phase 1 形态层提前启动、本轮只做 L2 骨架"。
- 通信底座已由 [006](../006-agent-bridge/requirement.md) 提供：本期前端作为 bridge 的下游客户端接入，**不改动 bridge 与 agent 核心库**。

### 1.4 里程碑划分

本期拆成三个递进里程碑，各自可独立验收：

| 里程碑 | 名称 | 一句话目标 |
| --- | --- | --- |
| **M1** | 环境搭建 | 起 Tauri + React + Vite 工程骨架，两个窗口空壳能本地跑起来 |
| **M2** | 界面大框架（占位形象 + 操作栏） | 桌宠悬浮窗 + 占位形象 + 操作栏浮层（单按钮：打开对话界面） |
| **M3** | 传统对话页 | 对话窗接 bridge 跑通流式对话（含工具调用 / 思考 / 历史会话），本期核心价值 |

> 风险提示（非验收项）：硬骨头集中在 M2（透明悬浮窗的置顶 / 穿透 / hit-test / 拖拽 / 托盘 + Win/Mac 差异），M3 因后端原生 AG-UI 相对顺。风险排序 M2 > M3 > M1。

---

## 2. 本期范围（In Scope）

| 模块 | 目标 | 里程碑 |
| --- | --- | --- |
| 前端工程骨架 | `frontend/` 下 Tauri 2 + React + Vite + TS + pnpm，单 build 多 entry（桌宠窗 / 对话窗） | M1 |
| 跨平台开发脚本 | 安装 / dev / build 等操作进 `scripts/`，双端（sh + ps1） | M1 |
| 桌宠悬浮窗 | 桌面透明悬浮、置顶，显示**占位形象**，预留未来 Live2D 的渲染挂载点 | M2 |
| 操作栏浮层 | 窗内 DOM 浮层（hover/click 显示），本期**仅一个按钮：打开对话界面** | M2 |
| 对话窗 | 常规（不透明）窗口，承载传统 chatbot | M2 立壳 / M3 填充 |
| 流式对话主流程 | 接 bridge AG-UI 出口，消息发送 + 流式接收 + 打字机 + 工具调用展示 + 思考展示 | M3 |
| 历史会话 | 会话列表展示与切换（走 bridge meta 接口） | M3 |
| persona 展示与切换 | persona 列表展示与切换（走 bridge meta 接口） | M3（P1） |
| 拟人化错误兜底 | 客户端可见错误不暴露技术细节，沿用 [`0001 §1.3`](../../decisions/0001-product-vision-and-roadmap/README.md) | M3 |

> P1 = 本期需要有，但允许极简。

---

## 3. 非目标（Out of Scope）

以下本期**明确不做**，避免范围蔓延（多为 exploration 中"留口子"的项）：

- **Live2D 实装** —— 仅预留 PixiJS 渲染挂载点，本期占位形象用静态图 / 简单动画。
- **情绪 / 状态事件通道**（agent → bridge → 前端的表情/动作驱动）—— 占位形象本期不需要状态驱动。
- **操作栏多按钮 / 复杂交互**（如"直接弹输入框"）—— 本期仅"打开对话界面"一个按钮，留可扩展口子。
- **复杂桌宠动作 / 表情 / 物理 / lipsync**。
- **跨平台打包 / 签名 / 公证 / 自动更新** —— 属完整 Phase 1，本期只在开发态跑（dev / 本地 build 验证形态即可）。
- **鉴权 / 多用户 / 多人格并存** —— 孵化期单设备单用户，沿用 006 的本机假设。
- **对话 UI 全包 / 接管式数据流方案**（Ant Design X，或 `@tdesign-react/chat` 自带的 `useChat` / engine）—— 本期**数据流自写**、bridge 为真相源；对话**视图层**采用 `@tdesign-react/chat` 散件（见 design §4.5），但不使用其内置 engine。
- **bridge / agent 核心库的任何改动** —— 本期纯前端接入，后端零侵入。

---

## 4. 里程碑与核心需求详述

### 4.1 M1 · 环境搭建

**目标**：把前端工程地基立起来，两个窗口空壳能在本地 dev 跑起来，开发操作脚本化。

- **R-M1.1 工程骨架**：在 [`0002 §3.10`](../../decisions/0002-incubation-tech-stack/README.md) 预留的 `frontend/` 下建立 Tauri 2 + React + Vite + TypeScript + pnpm 工程，采用单 build 多 entry（桌宠窗 / 对话窗两个 HTML 入口）。
- **R-M1.2 双窗口空壳可起**：本地 dev 模式能同时拉起「桌宠窗」与「对话窗」两个窗口的空壳（占位文案即可），证明多窗口配置打通。
- **R-M1.3 开发脚本化**：依赖安装、dev 启动、构建等操作纳入 `scripts/`，按 [`cross-platform-dev`](../../../.cursor/rules/cross-platform-dev.mdc) 提供 `run.sh` + `run.ps1` 双端，并登记 `scripts/README.md`。
- **R-M1.4 不破坏既有目录约定**：前端代码落在 `frontend/`，不侵入 `agent/` `memory/` 等既有职责目录。

### 4.2 M2 · 界面大框架（占位形象 + 操作栏）

**目标**：立起桌宠形态骨架——桌面悬浮的占位形象 + 窗内操作栏，点按钮能打开对话窗。

- **R-M2.1 桌宠悬浮窗**：桌宠主窗为桌面**透明悬浮、置顶**的窗口，显示**占位形象**（静态图 / 简单动画）。窗口的鼠标穿透、拖拽、托盘等形态能力本期做到"可用"，跨 Win/Mac 差异的具体处理留 design。
- **R-M2.2 形象挂载点留口子**：占位形象区域在结构上预留未来 Live2D（PixiJS canvas）的渲染挂载点，未来换 Live2D 只是"往挂载点填渲染"，不需重构窗口形态。
- **R-M2.3 操作栏浮层**：桌宠窗内有一个 DOM 浮层操作栏（hover / click 显示），**本期只含一个按钮：打开对话界面**。操作栏不单独开窗。
- **R-M2.4 打开对话窗**：点操作栏按钮后，出现「对话界面」窗口（本里程碑可为占位空壳，真正对话主流程在 M3 填充）。桌宠窗与对话窗为两个独立窗口（2 窗口模型）。
- **R-M2.5 形态无关解耦**：对话 / 会话相关的视图与逻辑不假设自己长在"全屏聊天窗"还是"桌宠气泡"里；桌宠形态作为可替换的"皮"，未来新增形态是新增视图而非重写。

### 4.3 M3 · 传统对话页

**目标**：对话窗接 `agent-bridge` 跑通完整的流式对话体验——本期核心价值。

- **R-M3.1 流式对话主流程**：用户在对话窗输入消息并发送，经 bridge AG-UI 出口收到流式回复，逐字（打字机）渲染；消息以气泡列表呈现，支持 markdown / 代码块。
- **R-M3.2 工具调用展示**：当 AI 触发工具调用（如搜索）时，对话流中能看到工具调用的过程态（进行中 → 结果），状态推进正确。
- **R-M3.3 思考展示**：AI 的思考 / reasoning 内容（AG-UI 对应事件）能在对话流中以可区分的形式展示。
  - ⚠️ 本期挂起（详见 [issue 002](../../issues/002-frontend-no-reasoning-event/README.md)）：bridge 的 AG-UI 编码器当前不发任何 reasoning 事件，做思考展示需改后端、违反 R-M3.7 零侵入；且不在 AC-M3.* 内。前端仅在消息结构 / 渲染层**预留思考渲染位**，待后端补事件再接。
- **R-M3.4 历史会话**：能拉取并展示历史会话列表，切换后能加载对应会话上下文继续对话（走 bridge meta 接口 + AG-UI thread 语义）。
- **R-M3.5 persona 展示与切换（P1）**：能展示可用 persona 列表并切换（走 bridge meta 接口）。
  - ⚠️ 本期暂缓：前端尚无人格的增删改查能力，单给"展示 + 切换"是半截功能（用户既不能新建/编辑人格，列表更新也要手动刷新页面才可见），体验上割裂。bridge 的 persona meta 接口已具备，待前端做完整人格管理时再一并接入；本期前端不呈现 persona 入口。
- **R-M3.6 拟人化错误兜底**：对话过程中的可恢复错误（限流 / 网络瞬断 / 工具失败）对用户呈现为拟人话术，**不暴露** HTTP 状态码 / 异常类名 / provider 名等技术细节，沿用 [`0001 §1.3`](../../decisions/0001-product-vision-and-roadmap/README.md) 与 [006 §4.5](../006-agent-bridge/requirement.md)。
- **R-M3.7 后端零侵入**：本期通过 bridge 既有契约（AG-UI 出口 + meta 接口）完成所有事，不要求改动 bridge 或 agent 核心库。

---

## 5. 关键体验原则

引用 [`0001 §1.3`](../../decisions/0001-product-vision-and-roadmap/README.md)，本期具象化为：

1. **底层可替换，上层稳定**（对应 R-M2.2 / R-M2.5）
   - 形态无关的 presentation 层 + 形象可替换的"皮"；桌宠形象挂载点留口子，未来换 Live2D / 新形态是叠加不是重写。
2. **像真人，不像工具**（对应 R-M3.6）
   - 错误反馈用拟人话术，不向用户暴露技术错误码 / 堆栈。
3. **记忆是第一护城河**（间接，对应 R-M3.4）
   - 历史会话可被正确拉取与续写，体验上"它记得之前聊过什么"。

---

## 6. 验收标准

> 标注「需真 LLM」的 AC 会实际触发 LLM 厂商 API，按 [`llm-api-confirm`](../../../.cursor/rules/llm-api-confirm.mdc) 单独授权后验收；其余 AC 在本机即可跑。

**M1**
- **AC-M1.1**：执行 `scripts/` 提供的安装 + dev 脚本后，本地能拉起桌宠窗与对话窗两个窗口空壳，无报错。
- **AC-M1.2**：`scripts/` 下相关脚本 `run.sh` 与 `run.ps1` 双端齐备并登记 `scripts/README.md`。

**M2**
- **AC-M2.1**：桌宠窗以透明悬浮、置顶形态出现在桌面，显示占位形象。
- **AC-M2.2**：在桌宠窗上 hover / click 能显示操作栏浮层，浮层含"打开对话界面"按钮。
- **AC-M2.3**：点该按钮后出现独立的对话窗（M2 阶段可为占位）。

**M3**（AC-M3.1 ~ AC-M3.4 需真 LLM）
- **AC-M3.1**：在对话窗发送一条消息，能看到流式（打字机）回复，气泡列表与 markdown 渲染正常。
- **AC-M3.2**：触发一次搜索类工具调用，对话流中能看到工具调用过程态与最终整合回复。
- **AC-M3.3**：能拉取历史会话列表并切换到某历史会话继续对话，上下文延续正确（与 `agent-cli` 看到的是同一份会话视图）。
- **AC-M3.4**：模拟一次可恢复错误，用户看到拟人兜底文案，界面不暴露 HTTP 状态码 / Python 异常名等技术细节。
- ~~**AC-M3.5（P1）**：能展示 persona 列表并切换，切换后续对话生效。~~ —— 本期暂缓（见 R-M3.5），待前端补齐人格增删改查后再验收。

**全局**
- **AC-G.1**：本期落地后，001~006 既有能力（`agent-cli` in-process / `--bridge`、bridge 各接口）无回归。
- **AC-G.2**：本期未改动 `agent/` 与 `agent-bridge/` 核心代码（前端纯接入）。

---

## 7. 开放问题 / 待 design.md 决策

> [`0001`](../../decisions/0001-product-vision-and-roadmap/README.md) / [`0002`](../../decisions/0002-incubation-tech-stack/README.md) / [`0003`](../../decisions/0003-frontend-stack-and-phase1-kickoff/README.md) 已锁定的项目级技术栈不重复。以下属本需求实现策略，将在同目录 `design.md` 讨论与决策（部分已在前期探索中形成倾向，design 阶段定稿）：

- **Q-1 前端目录与分层**：`pages/` `components/`（含 `ui/`）`stores/` `services/` `hooks/` `utils/` `types/` `constants/` `styles/`（含 `theme/`）的最终结构。
- **Q-2 状态管理库**：Zustand 或其他；多窗口（多 webview 内存隔离）下 store 的定位（视图缓存 vs 真相源）与跨窗口同步策略（bridge 为真相源 + Tauri event 发 invalidate 信号）。
- **Q-3 数据流 / AG-UI 客户端**：是否采用官方 `@ag-ui/client`（`HttpAgent` + `@ag-ui/core`）拿带类型的事件流，事件 → store 的胶水如何组织；连接层（service）与事件落库（store）的边界。
- **Q-4 窗口形态实现细节**：透明 / 置顶 / 鼠标穿透 / hit-test / 拖拽 / 托盘在 Win / Mac 上的具体实现与差异处理（呼应 0002 §3.6 的 Tauri spike）。
- **Q-5 主题 / 换肤方案**：`styles/theme/` 下 `html[theme='xxx']` 注入同名不同值 CSS 变量，与 Tailwind token 的映射方式。
- **Q-6 占位形象与挂载点形态**：占位形象的资源形式（静态图 / 简单动画）与未来 Live2D PixiJS canvas 挂载点的结构预留方式。
- **Q-7 历史会话 / persona 的 meta 接口对接**：复用 006 的哪些 meta endpoint、前端如何映射 thread/session 语义。
- **Q-8 开发脚本编排**：前端 dev / build 在 `scripts/` 下的命名与组织（与既有 `scripts/bridge`、`scripts/cli` 风格一致）。

---

## 8. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-10 | 初版起草（DRAFT） | 全文 |
| 2026-06-10 | 用户确认通过，状态置 CONFIRMED | 全文 |

---

## 文档元信息

- **创建时间**：2026-06-10
- **确认时间**：2026-06-10
- **状态**：CONFIRMED
- **下一步**：撰写同目录 `design.md`（技术方案）
