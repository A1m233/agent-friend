# 006 · agent-bridge：核心能力的对外形态

> agent-bridge: HTTP/SSE Outer Form of the Engine
>
> 把 `agent.Conversation` 通过双协议（OpenAI ChatCompletion + AG-UI）的 HTTP SSE 网络服务对外暴露，作为所有未来下游（语音、桌宠、IM 等）的统一接入底座。

---

## 状态

<!-- DRAFT | CONFIRMED -->
CONFIRMED

---

## 1. 背景与价值

### 1.1 现状

[001](../001-foundation-chat-and-memory/requirement.md) ~ [005](../005-engine-tool-calling-and-web-search/requirement.md) 落地后，agent 引擎已经具备完整的核心能力：

- 多轮文字对话与流式输出（001）
- 会话作为引擎一等公民 + 跨会话恢复（002）
- persona 管理与动态切换（003）
- 按职责装配的 system prompt（004）
- 工具调用机制 + 互联网搜索（005）

但**这一切只通过"同进程 `import` + `agent-cli`"一种方式被使用**——所有调用方必须跟 agent 跑在同一个 Python 进程里。

### 1.2 痛点

[`0001 §2.2`](../../decisions/0001-product-vision-and-roadmap/README.md) 的系统视角图里，"核心能力层"上面还有"感官通道层"和"形态层"——这两层在任何合理的工程实现里都会是**独立进程**（桌宠是 Tauri、IM bot 是消息平台的 webhook、语音控制平面要被火山 RTC 云端反向调用）。

也就是说，未来任意一个下游需求落地的第一步都是同一件事：**先把 agent 引擎包成网络服务**。如果留到下个需求里"顺手做"，会出现两个问题：

- 第一个尝试集成的下游（比如语音）会把"网络服务怎么暴露"这种通用底座问题跟"语音控制平面"这种具体业务搅在一起，需求 scope 失控
- 第二个下游来了发现底座是为前一个下游量身打造的，要么屈就要么重做

### 1.3 为什么独立立项

把"对外网络形态"作为独立需求做，本质上是 [`0002 §3.11`](../../decisions/0002-incubation-tech-stack/README.md) 决策的"双层架构（核心库 + 薄包装）"首次工程落地——这条决策在 0002 里只是写了示意结构，本期才有真实代码兑现。

独立立项后：

- 底座本身的"做什么"在本期闭环讨论，不被语音、桌宠等具体下游绑架
- 后续任何下游需求都只需要"在已有底座上接入"，scope 大幅收窄
- 底座的对外契约（双协议 + 双语义）一次性敲定，未来下游不重复讨论这些

### 1.4 与未来需求的衔接

本期不实现的能力扩展方向（具体下游接入、鉴权、公网部署等）作为兄弟需求单独立项。本期对外契约（HTTP endpoint / 协议事件 / 错误模型 / 会话语义）一旦敲定就是**长期稳定的承诺**——下游需求只能"扩展、不替换"。

---

## 2. 本期范围（In Scope）

| 模块                  | 目标                                                                              | 优先级 |
| --------------------- | --------------------------------------------------------------------------------- | ------ |
| 双协议 HTTP 出口      | OpenAI ChatCompletion（流式 + 非流式）+ AG-UI（SSE），共享 `ConversationEvent`    | P0     |
| 双语义会话承诺        | OpenAI 默认无状态、AG-UI 有状态、OpenAI 可扩展字段升级                            | P0     |
| 装配现有引擎能力      | 复用 `SessionManager` / `PersonaCatalog` / `ToolRegistry`，sessions 共用 `data/sessions/` | P0     |
| 跨进程错误模型        | 每种失败客户端应看到什么，遵 [005 R-4.1.4](../005-engine-tool-calling-and-web-search/requirement.md) 拟人化兜底精神 | P0     |
| 非对话 HTTP 接口      | 会话 / persona / model 列举与切换                                                  | P1     |
| 调试入口              | 改造 `agent-cli` 加 `--bridge` 模式，渲染层 100% 复用                              | P1     |

> P0 = 本期必须交付；P1 = 本期需要有，但允许极简。

---

## 3. 非目标（Out of Scope）

以下能力**本期明确不做**，避免范围蔓延：

- **鉴权 / 多租户 / API key / OAuth** —— 孵化期单设备单用户，bridge 仅 bind 本机（详见 §7 Q-5）；多用户场景未来另立
- **公网部署能力**（cloudflared / 反向代理 / TLS 证书 / 端口转发等）—— 由调用方负责，bridge 不内置
- **速率限制 / 配额管理 / 请求审计** —— 同上
- **任何具体下游接入**（语音、桌宠、IM）—— 各自单独立项
- **Web 调试 UI** —— 用 `agent-cli --bridge` 代替；前端 UI 待 Phase 1 桌宠形态需求一并考虑
- **bridge 进程的守护化 / 系统服务化**（systemd / launchd 等）—— 孵化期手动 / scripts 启动足够
- **SSE 自动重连 / 客户端容错** —— 协议层兼容即可（火山 RTC 等下游自己处理）
- **OpenAPI / Swagger 文档生成** —— FastAPI 顺手能出 Swagger 是副产物，不作为本期承诺
- **分布式 / 跨主机的会话一致性** —— 同主机多进程并发写已通过 `portalocker` 文件锁解决（详见 `design.md`），跨主机协调留给未来「agent 上云」决策

---

## 4. 核心需求详述

### 4.1 双协议 HTTP 出口

**目标**：把 `agent.Conversation` 通过 HTTP 暴露成网络服务，**同时支持** OpenAI ChatCompletion 协议和 AG-UI 协议，覆盖不同类型的下游客户端。

需求点（用户/系统视角）：

- **R-4.1.1 OpenAI ChatCompletion 出口**：暴露 `POST /v1/chat/completions`（或同语义路径），request body 含 `stream` 字段。`stream:true` 时响应 `Content-Type: text/event-stream`，按 OpenAI 标准 chunk schema 推送 delta；`stream:false` 时返回单个完整 JSON 响应。任何符合 OpenAI 协议的标准客户端（OpenAI SDK / 火山 RTC `CustomLLM` / LiteLLM 等）**开箱即用**。
- **R-4.1.2 AG-UI 出口**：暴露 AG-UI 协议端点（具体路径形状由 design.md 决定），按 AG-UI 官方 spec 推送事件流。文本类事件使用**三段式**（`TEXT_MESSAGE_START` / `_CONTENT` / `_END`），工具类事件同样使用三段式 + 独立 `TOOL_CALL_RESULT`。任何 AG-UI 标准客户端（CopilotKit / `ag-ui-protocol` 官方 SDK / 自研客户端）**直接可用**。
- **R-4.1.3 单一内部模型**：两个协议**都是** `agent.ConversationEvent` 流的下游 encoder。bridge 内部对 agent 核心库**零侵入**——本期不为了适配出口形态修改 `agent.Conversation` 或任何核心类型。
- **R-4.1.4 协议合规优先**：当某个内部能力跟两个协议中任何一个的标准语义冲突时，**优先遵守协议**而不是发明扩展。仅在协议无法表达的位置（如 OpenAI 协议天然无 `TOOL_CALL_RESULT` 概念）才用**符合规范的扩展字段**或留到 AG-UI 出口表达。
- **R-4.1.5 流式必须支持**：OpenAI `stream:true` 与 AG-UI 都必须是真流式——从 LLM 首 token 到完整回复期间能逐步推送，不允许"憋整句再返回"。本期 SLA：首字符延迟 ≈ 上游 LLM 的首 token 延迟，bridge 自身不贡献可感知的额外延迟。

> 具体 endpoint 路径、扩展字段命名、`message_id` 生成策略、`role` 映射、SSE keepalive 等由 `design.md` 决定。

### 4.2 双语义会话承诺

**目标**：让两个协议各自承担它们最自然的会话语义，**不互相迁就**——这是协议正确性的关键。

需求点：

- **R-4.2.1 OpenAI 出口默认无状态**：客户端每次 request body 自带完整 `messages`；bridge **不写 session 文件**、不持久化对话历史。这是 OpenAI 协议本来的语义，火山 RTC `CustomLLM` 这种"每次重发完整历史"的客户端**开箱即用**。
- **R-4.2.2 AG-UI 出口有状态**：客户端 request 携带 `thread_id` + `run_id`；bridge 把 `thread_id` 映射到 [002](../002-engine-session-management/requirement.md) 的 `session_id`，**自己**从会话存储里查上下文。该次请求**会**写入会话事件流。
- **R-4.2.3 OpenAI 可扩展字段升级**：客户端可在 OpenAI request body 加扩展字段（具体命名 design 决定）让该次调用启用持久化、绑定到指定 session。符合 OpenAI 规范的客户端**忽略**该扩展字段，**无副作用**；理解该字段的客户端能享受到与 AG-UI 等价的有状态语义。
- **R-4.2.4 状态共享一致性**：AG-UI 的 `thread_id` 与 `agent-cli` in-process 模式下创建的 `session_id` **是同一份资源**。任一进程创建的 session 都能被另一进程拉到、续写、resume——只要它们都指向同一份会话存储。
- **R-4.2.5 默认安全**：上述"OpenAI 默认无状态"的承诺确保**最常见的 misuse**（标准 OpenAI 客户端不知道扩展字段、不传 session id）不会污染用户的会话历史。

> 扩展字段的具体形态（header / body / metadata）、`thread_id` 不存在时的"自动创建" vs "404" 策略、并发场景下的容错策略，由 `design.md` 决定。

### 4.3 装配现有引擎能力

**目标**：bridge 不是从零写引擎，而是把 001~005 已有的装配点**原样**暴露成网络服务。

需求点：

- **R-4.3.1 装配点复用**：bridge 进程启动时按 `agent-cli` 同样的方式装配 `SessionManager` + `PersonaCatalog` + `NaiveContextManager` + `ToolRegistry` + `LLMClientFactory` + `PromptBuilderFactory`。不再从零写一份装配逻辑。
- **R-4.3.2 数据共享**：sessions 默认共用 [001](../001-foundation-chat-and-memory/requirement.md) ~ [002](../002-engine-session-management/requirement.md) 已经在用的 `data/sessions/` 路径——`agent-cli` in-process 模式与 bridge AG-UI 出口看到**同一份会话视图**。本期通过在 `JsonlSessionStore` 集成 `portalocker` OS 文件锁实现跨进程并发写入的串行化保护，多 surface（CLI / bridge / 未来 IM bot / 桌宠等）写同一 session 安全（详见 `design.md`）。
- **R-4.3.3 路径符合 §3.19**：本期**不在** `data/` 下新增任何持久化子目录。本期所有持久化都复用已有的 `data/sessions/` 与 `data/personas/`。如果 design.md 评估发现确实需要新增持久化（如 bridge 自身的 runtime 状态缓存），按 [`0002 §3.19`](../../decisions/0002-incubation-tech-stack/README.md) 走系统标准用户数据目录，不再往 `data/` 加东西。
- **R-4.3.4 引擎纯库无侵入**：本期不为了适配 bridge 改动 `agent` 核心库的公开 API。bridge 通过现有 `Conversation.stream(...)` / `SessionManager.*` / `PersonaCatalog.*` 等公开接口完成所有事——保持 [`0002 §3.11`](../../decisions/0002-incubation-tech-stack/README.md) 「核心库 + 薄包装」的纯库不被污染。

> 装配的具体形态（factory 函数 / FastAPI dependency injection / 启动配置文件等）由 `design.md` 决定。

### 4.4 非对话 HTTP 接口

**目标**：除对话主流程外，bridge 还要支撑客户端对 session / persona / model 的基本管理——让 `agent-cli` 在 `--bridge` 模式下能做的事不少于 in-process 模式。

需求点：

- **R-4.4.1 会话列举与打开**：客户端能拉到当前所有 session 列表（最少含 `session_id` / `title` / `persona` / `model` / 最近活跃时间），并能打开指定 session 继续对话。等价 `agent-cli` 的 `/sessions` 与 `/open`。
- **R-4.4.2 persona 列举与切换**：客户端能拉到当前可用 persona 列表（含 user / builtin source + id + description），并能切换当前 session 或后续 session 的 persona。等价 `/personas` + `/persona <name>`。
- **R-4.4.3 model 切换**：客户端能切换当前 session 的 model。等价 `/model <name>`。
- **R-4.4.4 跟随双语义承诺**：非对话接口的"是否需要 session 上下文 / 是否走 AG-UI 风格"由 design.md 决定。但承诺：这些接口不破坏 §4.2 的双语义边界（如不能让 OpenAI 出口默默变成有状态）。

> 具体 endpoint 路径、HTTP 方法、参数形态、response schema 由 `design.md` 决定。

### 4.5 跨进程错误模型

**目标**：把同进程 `raise Exception` 的错误模型，在两个协议下转成"客户端可感知 + 拟人化兜底"的形态，**用户体验不退化**。

需求点：

- **R-4.5.1 拟人化兜底优先**：可恢复错误（LLM 限流 / 网络瞬断 / 工具调用失败等）按 [005 R-4.1.4](../005-engine-tool-calling-and-web-search/requirement.md) / [001 R-4.1.4](../001-foundation-chat-and-memory/requirement.md) 思路，AI **用拟人话术继续**，不向客户端暴露技术细节。这条**优先级高于**任何"把错误尽快告诉客户端"的工程直觉。
- **R-4.5.2 不可恢复错误才上报**：仅在不可恢复错误（认证失败 / 严重配置错误 / 引擎崩溃 / 客户端协议错误）时才以协议各自的错误形态返回客户端：
  - OpenAI 出口：HTTP 4xx/5xx + JSON error 对象；流式过程中的错误按 OpenAI SSE 错误 chunk 规范
  - AG-UI 出口：`RUN_ERROR` 事件携带（type / message / code 等）
- **R-4.5.3 错误信息不暴露技术内幕**：客户端可见的 error message 必须是**用户语言**（"网络好像出了点问题"、"AI 服务暂时不可用"等），不能直接吐 Python 异常类名 / 堆栈 / provider 名 / API key 片段等。
- **R-4.5.4 不污染会话历史**：拟人兜底的对话内容**该落 session 仍照常落**（与 005 R-4.1.4 一致）；纯技术错误（HTTP 状态码、堆栈等）可记日志但**不**进入 session events 流。

> 错误码命名、何种错误归"可恢复" vs "不可恢复"的具体边界、客户端中途断流的处理、流式过程中插入 error chunk 的具体格式，由 `design.md` 决定。

### 4.6 调试入口（改造 `agent-cli` 加 `--bridge` 模式）

**目标**：让现有 `agent-cli` 同时支持 in-process 和 bridge 两种数据源——**用户面只有一个 CLI**，避免出现"两个调试入口让人犹豫用哪个"。

需求点：

- **R-4.6.1 单一 CLI**：**不新建** 独立的 bridge 客户端。在现有 `agent-cli` 加 `--bridge <URL>` 参数（或环境变量）。**in-process 模式仍是默认**，本地开发不强制先起 bridge。
- **R-4.6.2 渲染层零侵入**：CLI 渲染层（4 种 `ConversationEvent` 的 `isinstance` 分派、历史 replay 等）**完全复用**，只在数据源那一层切换（`conv.stream(...)` ↔ `bridge_client.stream(...)`）。
- **R-4.6.3 bridge 模式走 AG-UI 出口**：客户端封一个 AG-UI SSE 解析器，把 AG-UI 事件解码回内部 `ConversationEvent` 喂给渲染层。这样选 AG-UI 而非 OpenAI 是因为：AG-UI 事件粒度跟 CLI 调试观测的需求（要看 tool 调用全过程）天然对齐。
- **R-4.6.4 行为等价**：bridge 模式下，同一序列的用户操作（多轮对话 / 工具调用 / persona&model 切换 / 历史 replay）应当跟 in-process 模式产生**等价的用户可见行为**——验收点在 §6 AC-5。

> AG-UI 客户端解析 SDK 选用、bridge 进程的启停（CLI 是否能自动 spawn subprocess 起 bridge）、网络异常下的客户端重试策略，由 `design.md` 决定。

---

## 5. 关键体验原则

引用 [`0001 §1.3`](../../decisions/0001-product-vision-and-roadmap/README.md) 三条核心原则，本期具象化为：

1. **底层可替换，上层稳定**（与 §4.1 R-4.1.3 / §4.3 R-4.3.4 直接对应）
   - 内部 `ConversationEvent` 模型与协议出口完全解耦，加新协议只需新增 encoder
   - 这是 [`0002 §3.11`](../../decisions/0002-incubation-tech-stack/README.md) 「核心库 + 薄包装」决策的首次工程兑现
   - 公开契约（双协议 + 双语义）是**长期承诺**，未来下游只能扩展、不替换

2. **像真人，不像工具**（与 §4.5 R-4.5.1 / R-4.5.3 对应）
   - 跨进程后用户感知到的"AI 出错"体验跟 in-process 一样——可恢复错误依然拟人兜底
   - 不暴露 HTTP 状态码 / 协议错误 chunk / Python 异常名等技术细节
   - 沿用 [001 R-4.1.4](../001-foundation-chat-and-memory/requirement.md) / [005 R-4.1.4](../005-engine-tool-calling-and-web-search/requirement.md) 拟人化兜底精神

3. **记忆是第一护城河**（与 §4.2 双语义承诺间接相关）
   - 双语义承诺确保"最常见的 misuse"（标准 OpenAI 客户端不知道扩展字段）不会污染用户会话历史——记忆默认不会被无声破坏

---

## 6. 验收标准

> 本期 AC **不**包含火山 RTC 端到端验收——那一条需要拉公网穿透 + 改 spike `Custom.json` 配 `CustomLLM` 模式，足够支撑下一个语音需求 007，留到那里跑端到端。本期所有 AC 都能在**本机 + 标准协议工具**上跑完。

- **AC-1 OpenAI 流式跑通**：`curl POST /v1/chat/completions` 带 `stream:true`，看到符合 OpenAI ChatCompletion 标准的 SSE chunks 流出，最终 `data: [DONE]`。
- **AC-2 OpenAI 非流式跑通**：`curl POST /v1/chat/completions` 带 `stream:false`，返回单个完整 JSON 响应，字段符合 OpenAI ChatCompletion 标准。
- **AC-3 AG-UI 跑通**：用 `ag-ui-protocol` 官方 Python 客户端（或裸 SSE）打 AG-UI 出口，看到 `RUN_STARTED` + `TEXT_MESSAGE_START` / `_CONTENT*` / `_END` + `RUN_FINISHED` 的事件序列，三段式语义正确。
- **AC-4 工具调用流双协议都正确**：
  - 让 AI 触发一次搜索 tool；OpenAI 出口流里看到 `tool_calls` delta + 后续整合回复（中间过程 OpenAI 协议天然不可见）
  - AG-UI 出口流里看到 `TOOL_CALL_START` / `_ARGS` / `_END` / `_RESULT` 完整序列 + assistant 文本三段式
- **AC-5 `agent-cli --bridge` 行为等价**：用 `agent-cli` 跑一段含「多轮对话 + 工具调用 + persona/model 切换」的序列，记录 in-process 模式的用户可见行为；同一序列在 `--bridge http://...` 模式下产生等价的用户可见行为（文本内容、tool 调用渲染、状态切换提示等不存在用户能察觉的退化）。
- **AC-6 跨进程共享会话**：`agent-cli` in-process 模式下创建并写完 session A；启动 bridge 进程，通过 AG-UI 出口（`thread_id` 指向 session A）拉到该 session 的上下文并继续对话。**反之同样成立**——bridge 创建的 session 也能在 `agent-cli` in-process 模式下打开继续。本期通过 `portalocker` 文件锁实现跨进程串行化写入，并发写不会破坏 jsonl 完整性（详见 `design.md`）。
- **AC-7 跨进程错误模型**：模拟 LLM 限流 / 网络瞬断 / 工具失败（参考 [005 AC-2](../005-engine-tool-calling-and-web-search/requirement.md) 同款手法），客户端通过 AG-UI 出口能看到拟人兜底的文本输出，**不暴露** HTTP 状态码或 Python 异常名；只有当模拟不可恢复错误（API key 错 / 引擎崩溃）时才以 `RUN_ERROR` 事件返回客户端。
- **AC-8 既有行为不退化**：001 ~ 005 已交付的所有行为（多轮对话、流式输出、跨会话恢复、persona / model 切换、system prompt 三段装配、工具调用 + 搜索、`agent-cli` in-process 模式）在本期落地后**全部继续可用**，无回归。
- **AC-9 §3.19 不违反**：本期落地后，`data/` 下**没有**任何新增的持久化子目录。所有 sessions 仍走 `data/sessions/`，没有 `data/bridge/` 或类似新增。

---

## 7. 开放问题 / 待技术文档决策

> [`0001`](../../decisions/0001-product-vision-and-roadmap/README.md) / [`0002`](../../decisions/0002-incubation-tech-stack/README.md) 中已锁定的项目级技术栈与决策不重复。
> 以下问题属于**本需求的实现策略**，**不在本需求文档中拍板**，将在同目录 `design.md` 中讨论与决策：

- **Q-1 OpenAI 出口扩展字段命名**：用 HTTP header（如 `X-Agent-Friend-Session-Id`）、body 顶层字段、还是 `metadata.session_id`？哪种与主流 OpenAI 客户端（OpenAI SDK / 火山 RTC `CustomLLM` / LiteLLM 等）兼容最好？
- **Q-2 AG-UI 事件粒度细节**：单条 assistant 回复内若有 tool 调用穿插，`message_id` 怎么切（每段一个 vs 整轮一个）？persona_change / model_change 等 002 事件类型怎么映射到 AG-UI 事件？
- **Q-3 AG-UI `thread_id` 语义**：客户端传一个不存在的 `thread_id` 时自动创建 session 还是 404？不带 `thread_id` 时怎么处理？
- ~~**Q-4 跨进程并发写 `data/sessions/*.jsonl` 的处理**~~：**已决策** —— 在 `JsonlSessionStore.append_event` 集成 `portalocker` OS 文件锁，跨进程串行化写入，多 surface（CLI / bridge / 未来 IM / 桌宠等）写同一 session 都安全。详见 `design.md`。
- **Q-5 服务监听形态**：默认 host/port（建议 `127.0.0.1:8000`）？是否仅 bind 本机？是否在 `.env` / 配置文件 / CLI 参数里配置？
- **Q-6 健康检查 endpoint**：`/healthz` / `/readyz` / `/v1/models` 等是否本期就位？
- **Q-7 bridge 进程启停管理**：CLI 命令形态（`agent-bridge serve` / `python -m agent_bridge` / 其他）？日志输出路径（stdout / 系统标准日志目录 / 文件）？
- **Q-8 AG-UI 客户端 SDK 选用**：`agent-cli --bridge` 模式下的 AG-UI 客户端复用 `ag-ui-protocol` 官方 Python SDK 还是自己写 SSE 解析？官方 SDK 的成熟度（Source reputation: Medium）是否足够？
- **Q-9 错误码映射**：每种 `agent` / `llm_providers` 内部异常（`LLMRateLimitError` / `SessionPersistError` / `ToolNotFoundError` / `PersonaNotFoundError` 等）映射到什么 OpenAI error code / AG-UI `RUN_ERROR.code`？拟人兜底 vs 上报客户端的边界？
- **Q-10 非对话 HTTP 接口形状**：会话 / persona / model 列举与切换的 REST endpoint 设计——是否走 AG-UI 协议自带的 state event，还是单独 REST，还是两条并存？
- **Q-11 bridge 与 agent 包的依赖方向**：`agent-bridge/` 作为顶层独立 uv 项目，依赖 `agent` / `llm_providers` / `memory` 等包的方式（path dependency / 本地 editable install / 其他）？

---

## 8. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-05-22 | 初版确认通过 | 全文 |
| 2026-05-22 | design 阶段决策 Q-4 集成 portalocker 文件锁，同步收紧 §3 非范围 / §4.3 R-4.3.2 / §6 AC-6 / §7 Q-4 表述 | §3 / §4.3 / §6 / §7 |

---

## 文档元信息

- **创建时间**：2026-05-22
- **确认时间**：2026-05-22
- **下一步**：撰写同目录的 `design.md`（技术方案）
