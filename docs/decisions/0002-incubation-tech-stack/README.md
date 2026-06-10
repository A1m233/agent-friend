# 决策 0002 · 孵化期技术选型

> Incubation Tech Stack
>
> 本文档锁定 `agent-friend` **项目级**的技术栈决策，服务于孵化期（Phase 0）启动，并预设到 Phase 1（桌宠形态期）启动前。
> **本文档只决"项目级技术栈"——具体到某个需求的实现策略（如记忆抽取算法、调试 UI 形态等），由对应需求的 `design.md` 决定，不在本文档范围内。**

---

## 0. 元信息

- **状态**：草稿（Draft）
- **创建时间**：2026-05-13
- **影响范围**：全项目
- **重新评估触发条件**：见第 6 节

---

## 1. 背景与决策范围

### 1.1 决策背景

`agent-friend` 的产品愿景与路线图见 [`0001-product-vision-and-roadmap`](../0001-product-vision-and-roadmap/README.md)。本文档基于其中的若干关键认知做出技术选型：

- **形态**：长期是常驻桌面的 AI 朋友，需要支持 Win + Mac
- **结构**：AI 大仓（monorepo），多模块平铺
- **核心**：对话能力 + 记忆能力是地基，长期还会扩展出客户端、IM 接入、语音等能力
- **角色定位**：开发者是前端背景，长期需要 Python + 前端技术栈共存

### 1.2 决策范围（In Scope）

- 目标平台
- 各模块语言与运行时
- Monorepo 管理策略 + 顶层目录约定
- Python 模块的对外形态 + 跨进程通信范式
- LLM Provider 与抽象层
- 记忆系统的存储基底 + 预备向量栈
- 配置与密钥管理
- Python / TypeScript / Tauri 等具体工具链版本与代码规范

### 1.3 不在范围（Out of Scope，延后或归属其他文档）

- 各需求的具体实现策略 → 对应需求的 `design.md`
- 记忆系统的检索策略与召回算法 → `001-foundation-chat-and-memory/design.md`
- 调试 UI 的具体形态 → `001-foundation-chat-and-memory/design.md`
- 客户端"操控电脑"的进程模型与能力边界 → 待 Phase 1 启动前评估
- CI/CD、打包、签名、公证、自动更新 → 待 Phase 1 启动前评估
- 开发协作规范（pre-commit、commit message 规范等） → 待孵化期实践后补

---

## 2. 决策清单速查表

| #  | 项                              | 决策                                                              |
| -- | ------------------------------- | ----------------------------------------------------------------- |
| 1  | 目标平台                        | Win + Mac（不做 Linux）                                            |
| 2  | agent 引擎语言                  | Python                                                            |
| 3  | Python 版本                     | 3.12                                                              |
| 4  | Python 包管理                   | uv                                                                |
| 5  | Python 代码规范                 | ruff（lint+format+isort）+ mypy（类型检查）                        |
| 6  | 桌宠前端框架方向                | Tauri 2                                                           |
| 7  | 未来前端技术栈方向              | Node LTS（22+）+ TypeScript 5.x + pnpm + Tauri 2.x                 |
| 8  | 客户端（操控电脑）              | 孵化期不实现，架构预留位置                                        |
| 9  | Monorepo 策略                   | 各语言各用各的工具，根目录只做约定                                |
| 10 | 顶层目录风格                    | 按职责分                                                          |
| 11 | Python 模块对外形态             | 双层：核心库（pure Python）+ 薄包装（FastAPI HTTP/SSE）            |
| 12 | 跨进程通信协议                  | HTTP REST + SSE 流式                                              |
| 13 | LLM Provider（孵化期主力）      | DeepSeek                                                          |
| 14 | LLM 抽象层                      | LiteLLM                                                           |
| 15 | 记忆存储基底                    | SQLite                                                            |
| 16 | 记忆检索 · 预备向量栈           | Chroma + 本地 BGE-small-zh（待 `001/design.md` 决定是否启用）       |
| 17 | 配置 · 开发期                   | `.env` + pydantic-settings                                        |
| 18 | 配置 · 生产期                   | TOML 文件（系统标准目录）+ keyring（API key 等敏感信息）            |
| 19 | 用户数据存储路径                | 已有沿用 `data/`；**新增即直接走系统标准用户数据目录**             |

---

## 3. 各项详述

> 每项格式：**候选 → 决策 → 决策理由 → 放弃理由 → 重新评估触发条件**

### 3.1 目标平台

- **候选**：Windows / macOS / Linux
- **决策**：Win + Mac
- **理由**：覆盖目标用户群，资源聚焦
- **放弃 Linux 的理由**：用户群占比低、双端已能覆盖核心场景、节省跨平台测试与发布的精力
- **重评触发**：出现明确的 Linux 用户需求 / 团队规模扩大有余力支持

### 3.2 agent 引擎语言：Python

- **候选**：Python / TypeScript / Rust
- **决策**：Python
- **理由**：LLM/Agent 生态最丰富、prototype 速度最快、未来若引入本地 embedding/向量库等都首选 Python
- **放弃 TS 的理由**：Agent / LLM 生态短板明显
- **放弃 Rust 的理由**：开发速度劣势在早期项目放大；Rust 留给客户端层与前端内核
- **重评触发**：出现性能瓶颈无法通过 Python 解决，或团队全员转向其他语言

### 3.3 Python 版本：3.12

- **候选**：3.11 / 3.12 / 3.13
- **决策**：3.12
- **理由**：稳定、生态全兼容（LiteLLM、Chroma、sentence-transformers 等）、性能比 3.11 显著提升
- **放弃 3.13 的理由**：部分包未完成适配，孵化期不冒险
- **放弃 3.11 的理由**：性能落后于 3.12
- **重评触发**：3.13 生态完全成熟（约 1 年内）

### 3.4 Python 包管理：uv

- **候选**：uv / poetry / pip+venv / pdm
- **决策**：uv
- **理由**：Astral 出品，Rust 实现，速度比 poetry 快 10-100 倍；已成 2025 年事实标准；统一管理 venv + dependencies + lockfile + python 版本
- **放弃 poetry 的理由**：明显较慢，安装/解析体验已被 uv 全面超越
- **放弃 pip+venv 的理由**：缺少 lockfile 与 dependency resolver，团队协作易出问题
- **放弃 pdm 的理由**：方向类似 uv，但社区势能不如 uv
- **重评触发**：uv 出现重大破坏性变更或维护中断

### 3.5 Python 代码规范：ruff + mypy

- **候选**：ruff / black+isort+flake8 / 完全不加
- **决策**：ruff（lint+format+isort 三合一）+ mypy（类型检查）
- **理由**：ruff 一个工具替代多个，速度极快；mypy 是 Python 类型检查的事实标准
- **放弃传统组合的理由**：多工具配置碎片化、速度慢
- **放弃"完全不加"的理由**：多人协作必须有统一规范基线
- **重评触发**：ruff 出现重大问题，或 ty（Astral 自家类型检查器）成熟到可替代 mypy

### 3.6 桌宠前端框架方向：Tauri 2

- **候选**：Tauri 2 / Electron / 双套（Electron+Swift）/ 纯原生 GUI
- **决策**：Tauri 2
- **理由**：
  - 一套 Web 前端代码同时打 Win + Mac 安装包
  - 包体积比 Electron 小一个数量级（关键，桌面常驻 app）
  - 性能/内存远好于 Electron
  - 桌宠所需的窗口能力（透明 / 置顶 / 鼠标穿透 / 托盘 / 全局快捷键）齐全
  - 与未来"客户端（操控电脑）"的 Rust 工具链可统一
- **放弃 Electron 的理由**：包体积/内存对桌面常驻 app 不友好；长期看 Tauri 是趋势
- **放弃双套的理由**：违背"一个框架适配多端"目标，且双套维护成本高
- **放弃纯原生 GUI 的理由**：开发者前端经验作废；桌宠的视觉表现力 Web 优于原生 GUI
- **代价**：需要团队接受少量 Rust（日常 90% 写 Web 前端，10% 偶尔涉及 Rust 插件/原生通道）
- **重评触发**：Phase 1 启动时再做一次小型 spike，验证 Tauri 在透明窗口/桌宠交互上的真实表现

### 3.7 未来前端技术栈方向

- **决策**：Node LTS（22+）+ TypeScript 5.x + pnpm + Tauri 2.x
- **状态**：~~先记方向，Phase 1 启动时锁版本~~ → 框架与版本基线已由 [`0003-frontend-stack-and-phase1-kickoff`](../0003-frontend-stack-and-phase1-kickoff/README.md) 锁定（框架 = React，构建 = Vite）
- **理由**：业界主流稳定组合；pnpm 比 npm/yarn 在 monorepo 场景下更优（workspace 协议、磁盘占用小）
- **重评触发**：Phase 1 启动前

### 3.8 客户端（操控电脑）：孵化期不实现，架构预留

- **决策**：本期不写代码，但顶层目录预留 `client/` 空目录 + README，明确这是未来的位置
- **未来语言倾向**：Rust（与 Tauri 内核栈一致，跨平台原生能力成熟）
- **未来架构倾向**：独立进程（不与 Tauri 同进程，避免桌宠崩溃影响操控能力）
- **重评触发**：Phase 1 桌宠形态稳定后；或操控电脑能力被提前需要

### 3.9 Monorepo 策略：各语言各用各的工具

- **候选**：A 各语言各管各 / B 元工具统一编排（Nx/Bazel）/ C 折中（JS 用 Turborepo，其他各管各）
- **决策**：A
- **理由**：
  - 早期项目模块少、依赖图浅，元工具是过度设计
  - 各语言用社区最佳实践，新人 onboarding 直觉
  - 升级成本可控：未来真有需要可平滑迁移到 C 或 B
- **放弃 B 的理由**：学习成本高、配置膨胀，对早期项目偏重
- **放弃 C 的理由**：JS 部分孵化期不存在（前端不写），暂无收益
- **重评触发**：模块数量超过 10 个 / CI 时间无法忍受

### 3.10 顶层目录风格：按职责分

- **候选**：按职责分 / 按语言分 / packages+apps 二分
- **决策**：按职责分
- **理由**：
  - 业务模块感是主线，工具链是辅线
  - 多语言场景下，按语言分会割裂"业务模块"的概念
  - 加新模块（如未来 `voice/`、`im/`）一目了然
- **预想目录结构**（仅参考，孵化期不需要全部创建）：
  ```
  agent-friend/
  ├── agent/              # Python · agent 引擎（对话编排、prompt 管理）
  ├── memory/             # Python · 记忆模块（独立于 agent）
  ├── llm_providers/      # Python · LLM Provider 适配层（含 LiteLLM 封装）
  ├── frontend/           # TS · Tauri 前端（孵化期不写，预留 README）
  ├── client/             # Rust · 操控电脑（孵化期不写，预留 README）
  ├── shared/             # 跨模块共享：协议/类型/常量
  ├── tools/              # 调试 UI、eval 脚本、辅助工具
  ├── docs/               # 已存在
  └── README.md
  ```
- **重评触发**：模块数量超过 10 个；或出现明显不适合"按职责"组织的模块

### 3.11 Python 模块对外形态：双层（核心库 + HTTP 薄包装）

- **候选**：A 纯库 / B 自带 HTTP server / C 双层
- **决策**：C
- **理由**：
  - 同进程调用方（CLI、单元测试、eval）直接 import 纯库，零开销
  - 跨进程调用方（前端、IM、客户端）走外层 HTTP，松耦合
  - 核心 IP（agent 编排、记忆）写成纯库，框架污染降到最低
  - 未来若引入 sidecar 等其他传输，加一层适配即可，core 不动
- **典型结构**（以 agent 模块为例）：
  ```
  agent/
  ├── core/              # 纯 Python 库（无网络/框架污染）
  └── api/               # 薄薄一层 FastAPI（HTTP + SSE）
  ```
- **放弃 A 的理由**：跨语言/跨进程调用方无法直接用
- **放弃 B 的理由**：CLI / 测试 / eval 也被迫走 HTTP，调试与测试都变重
- **重评触发**：HTTP 通信成为性能瓶颈

### 3.12 跨进程通信协议：HTTP REST + SSE 流式

- **候选**：HTTP+SSE / Tauri sidecar+stdio / WebSocket / gRPC
- **决策**：HTTP REST + SSE 流式
- **理由**：
  - 通用：桌面前端、IM、客户端都能用同一套接口
  - 调试友好：curl / Postman / 浏览器都能直接打
  - SSE 适合"打字机式"流式输出（LLM 流式回复天然契合）
  - 配合 FastAPI 自带 OpenAPI 文档，前端类型可自动生成
- **放弃 sidecar+stdio 的理由**：把 Python 锁死在"必须被宿主拉起"的角色，IM 接入等场景用不了
- **放弃 WebSocket 的理由**：状态管理复杂，调试不直观
- **放弃 gRPC 的理由**：早期项目过度工程，工具链与调试成本高
- **安全约束**：仅 bind 127.0.0.1，加 token 鉴权，不向外网暴露
- **重评触发**：Phase 2/3 出现需要双向流式或更强类型契约的场景

### 3.13 LLM Provider（孵化期主力）：DeepSeek

- **候选**：Claude / OpenAI / DeepSeek / 其他国产 / 本地模型
- **决策**：孵化期主力 = DeepSeek
- **理由**：
  - 国内零障碍访问、零支付门槛
  - 价格极低（孵化期高频调试友好）
  - 中文能力第一梯队
  - 即便最终主力换成 Claude，孵化期阶段也足够验证整个系统闭环
- **架构上的"主力"含义**：仅指"先针对它调 prompt + 买它的 key"。代码层面通过 LiteLLM 100+ Provider 支持是统一的
- **放弃 Claude 作为孵化期主力的理由**：访问/支付门槛拖慢迭代节奏（不否认其作为长期主力的优势）
- **放弃本地模型作为孵化期主力的理由**：能力上限会干扰对系统问题的判断
- **重评触发**：
  - 孵化期结束验证完闭环后，可评估切换主力
  - 拟人感与中文表现的实际效果如不达预期，优先评估 Claude

### 3.14 LLM 抽象层：LiteLLM

- **候选**：LiteLLM / 裸 SDK 自写抽象 / LangChain / LlamaIndex
- **决策**：LiteLLM
- **理由**：
  - 职责严格收敛在"调用层"，不染指 agent 编排
  - 一套 API 覆盖 100+ Provider，切换成本极低
  - 流式、function calling 等差异已基本抹平
- **重要边界**：LiteLLM 只做"打 LLM 这通电话"。所有 agent 编排（多轮上下文管理、记忆召回拼装、工具调用编排、错误重试与兜底话术）都在 `agent/core/` 自己实现
- **放弃 LangChain 的理由**：抽象层过重，会劫持 agent 编排层，让自有核心 IP 难以独立演进；版本变化大调试难
- **放弃 LlamaIndex 的理由**：偏 RAG 数据接入场景，对本项目过厚
- **放弃裸 SDK 的理由**：要自己维护多 Provider 适配，重复造轮子
- **重评触发**：LiteLLM 出现重大破坏性变更；或团队需要更细粒度控制时再考虑裸 SDK

### 3.15 记忆存储基底：SQLite

- **候选**：SQLite / 文件（JSON、YAML）/ 文档数据库
- **决策**：SQLite
- **理由**：
  - Python 内置 `sqlite3`，零依赖
  - 单文件、跨平台稳定，桌面 app 事实标准
  - 同时支撑结构化数据 + FTS5 全文检索 + 未来若引入 sqlite-vec 还能上向量
- **放弃文件的理由**：规模一大就崩、并发问题
- **放弃文档数据库的理由**：要起独立服务，桌面 app 不适合
- **重评触发**：单库容量超过百 GB 量级（可预见的几年内不会发生）

### 3.16 记忆检索 · 预备向量栈

- **决策**：**本项不在 0002 强制启用**。是否使用向量检索 / 何时启用 / 如何与关键词检索结合，由 [`docs/requirements/001-foundation-chat-and-memory/design.md`](../../requirements/001-foundation-chat-and-memory/design.md) 决定
- **预备技术栈**：若 design 决定启用向量检索，**默认采用以下组合**，避免到时再花时间选型：
  - **向量库**：Chroma（嵌入式、Python 原生、无独立服务）
  - **Embedding 模型**：本地 BGE-small-zh（中文质量好、零网络、模型 ~100MB）
- **若 design 决定不启用向量**：本预备技术栈在文档中保留作为未来升级路径
- **重评触发**：
  - design 启用向量后，BGE 实际效果不达预期 → 评估升级到 BGE-m3 / multilingual-e5
  - 向量库出现性能瓶颈 → 评估 LanceDB / sqlite-vec

### 3.17 配置 · 开发期：`.env` + pydantic-settings

- **候选**：pydantic-settings+.env / 纯 dotenv / TOML 文件 / 裸 os.environ
- **决策**：pydantic-settings + `.env`
- **理由**：
  - 类型安全（pydantic 字段校验）
  - 自动支持 env > .env > 默认值的优先级
  - `.env` 加入 `.gitignore`，配 `.env.example` 给新人参考
- **放弃裸 dotenv 的理由**：缺少类型校验，要自己写校验逻辑
- **放弃 TOML 的理由**：开发期不需要复杂层级配置；留给生产期
- **放弃裸 os.environ 的理由**：缺少结构化、缺少校验

### 3.18 配置 · 生产期：TOML 文件 + keyring

- **决策**：
  - **非敏感配置**：用户级 TOML 文件，存放于系统标准目录
    - Win：`%APPDATA%/agent-friend/config.toml`
    - Mac：`~/Library/Application Support/agent-friend/config.toml`
  - **敏感信息（API key 等）**：通过 Python `keyring` 包写入系统安全存储
    - Win：Credential Manager
    - Mac：Keychain
- **理由**：用户体验好（不需手动改 .env）、API key 安全合规、跨平台统一接口
- **状态**：**孵化期不实现**，结构上让 pydantic-settings 预留多 source 加载能力，Phase 1 桌宠化时落地
- **边界说明**：本节只覆盖**配置**（用户可调的开关、API key 等）。**用户数据**（sessions、personas、记忆数据等产品代用户保管的内容）的存储路径策略另立，见 §3.19

### 3.19 用户数据存储路径：已有沿用 / 新增即外部

- **适用范围**：sessions、用户自定义 personas、未来的记忆数据等——凡是「产品代用户保管、用户视角属于其本人」的数据。**不含**配置（配置走 §3.17 / §3.18）
- **决策**：分两段处理
  - **本决策立项前已写入项目 `data/` 的部分**（如 `data/sessions/`、`data/personas/`）：**保留不动**，Phase 1 桌宠化打包时一并迁移
  - **本决策立项后新增的此类持久化**：**直接走系统标准用户数据目录**，**不再往项目 `data/` 里加新东西**——开发期与生产期同一套行为
    - Win：`%APPDATA%/agent-friend/`
    - Mac：`~/Library/Application Support/agent-friend/`
- **关键工程原则**：
  - 新增模块代码层**必须支持 path 参数注入**（构造函数 / 环境变量），便于测试用临时目录隔离、生产由调用方装配
  - 默认值直接指向系统标准目录，**避免开发期写项目内、生产期再迁移**这种二次重构
  - 跨平台路径解析的具体实现（如 `platformdirs` 等库的选用）由真正落地该模块的需求 `design.md` 决定，本决策不强制
- **理由**：
  - 未来打包成 Win / Mac 安装包后，应用安装目录无写权限（Mac 应用沙箱 / Win Program Files 限制），用户数据必须落到系统标准目录
  - "新增即外部"避免 Phase 1 重构面积积累——孵化期还可能新增多个持久化模块（记忆增强、未来 bridge 状态等），若都先写 `data/xxx`、Phase 1 一次性全改，工作量与回归风险都会膨胀
  - 现有 `JsonlSessionStore` 等已经接受 path 参数注入，**代码层不需要大改**，Phase 1 打包时只需把 CLI / bridge 等入口的默认值切到新路径
- **状态**：~~孵化期已有路径**不实现迁移**；完整迁移在 Phase 1 桌宠化打包时一并落地~~ → **已于 2026-06-09 提前完成全量迁移**（见下方变更说明）。当前 sessions / personas / memory / CLI history 默认全部落到系统标准用户数据目录，仓库内 `data/` 不再被生产或开发路径写入。
- **重评触发**：迁移已落地，本节无待办；后续若引入新的用户数据类持久化，按「新增即外部」原则直接走系统目录。
- **变更说明（2026-06-09）**：
  - 原计划「已有路径 Phase 1 再迁移」提前到本轮一并做掉，理由是避免历史债跨阶段累积（决策核心「用户数据走系统标准目录」未变，仅提前执行时机并纳入已有数据）。
  - 修正一处历史偏差：008 长期记忆（`data/memory/memory.db`）是本节立项**之后**新增的持久化，本应「新增即外部」，实际仍落在仓库内 `data/`，违反本节原则；本轮一并迁出。
  - 跨平台路径解析选用 `platformdirs`（本节 §3.19 原文允许由落地需求自行选型）。
  - 仓库内 `data/` 下的旧本地开发数据**不自动搬移**（属用户机器状态），新运行直接使用系统目录；如需保留旧会话可手动拷贝。

---

## 4. 暂缓决策项（待 Phase 1 启动前再定）

以下项目级决策**当前不需要做**，但已显式标记，避免被遗忘：

| 项                                           | 推迟原因                                | 触发评估的时机             |
| -------------------------------------------- | --------------------------------------- | -------------------------- |
| 客户端"操控电脑"的进程模型与能力边界         | 孵化期不实现，无真实输入                | Phase 1 启动前             |
| CI/CD（GitHub Actions 跨平台 runner 配置）   | 孵化期手动跑足够                        | Phase 1 启动前 / 多人协作时 |
| 打包 / 签名（Apple notarization、Win signing）| Phase 1 桌宠化时才需要                  | Phase 1 启动前             |
| 自动更新机制                                 | 同上                                    | Phase 1 启动前             |
| pre-commit hooks                             | 工程规范增强，可后补                    | 多人协作前                 |
| Commit message 规范                          | 单人开发可缓                            | 多人协作前                 |

---

## 5. 影响与后续

### 5.1 本决策影响的下游文档

- [`docs/requirements/001-foundation-chat-and-memory/requirement.md`](../../requirements/001-foundation-chat-and-memory/requirement.md)：第 8 章「开放问题」中已被本文档解决的全局项需相应清理
- 未来的 `docs/requirements/001-foundation-chat-and-memory/design.md`：基于本文档的技术栈展开实现设计

### 5.2 本决策直接产出的工程动作（不在本文档执行）

- 建立顶层目录骨架（`agent/` `memory/` `llm-providers/` `tools/` 等）
- 初始化 Python 工具链（`pyproject.toml` + `uv` + `ruff` + `mypy` 配置）
- 补全 `.gitignore`（已配套执行）
- `frontend/` `client/` 等预留目录添加 README 占位

### 5.3 一句话总结

> **孵化期 = Python 3.12 + uv + DeepSeek + LiteLLM + SQLite + FastAPI(HTTP/SSE) + 各模块按职责分目录。前端与客户端方向已锁定但不实现。**

---

## 6. 重新评估的触发条件

本文档应在以下情形被重新审视：

1. 任何"重评触发"小节里描述的情况发生
2. Phase 1（桌宠形态期）启动前，逐项 review 一遍未来方向项是否仍然合适
3. 出现重大行业变化（如某 LLM 选型出现颠覆性替代）
4. 团队规模或定位发生变化
5. 任意已锁定项的代价被低估到不可接受程度

每次重大修订通过 git 历史保留即可，不需要为每次修订单开新文件。
