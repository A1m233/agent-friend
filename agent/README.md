# agent

`agent-friend` 的 **Agent 引擎**模块。

## 定位

整个项目的核心 IP 所在地。负责：

- 多轮对话编排（上下文管理、prompt 拼装、tool calling 循环）
- 会话作为引擎层一等公民（创建 / 打开 / 持久化）
- 用户自定义 / 内置人格（persona）管理
- 调用 `memory` 模块进行记忆写入与召回
- 调用 `llm_providers` 模块完成 LLM 通信
- 错误重试与兜底（含禁用"我不记得"等失忆话术）

> 本模块的设计原则之一：**核心编排逻辑必须独立于任何第三方 agent 框架**（如 LangChain），以保证长期可演进性。详见 [`docs/decisions/0002-incubation-tech-stack/README.md`](../docs/decisions/0002-incubation-tech-stack/README.md) 第 3.14 节。

## 状态

孵化期已落地需求 001–009 的引擎能力（对话 / 会话 / persona / system prompt / tool calling / 记忆挂钩 / 上下文管理）。本模块保持**纯 Python 库**形态，不含 HTTP 层。

## 内部结构

实际为按职责拆分的扁平布局（HTTP/SSE 层已独立到 `agent_bridge` 包，本模块不含 `api/`）：

```
src/agent/
├── conversation.py     # 多轮对话 + tool 循环 + memory 注入
├── sessions/           # Session / JsonlSessionStore / SessionManager（002）
├── personas/           # PersonaCatalog（builtin + user 两层，003）
├── system_prompt/      # SystemPromptComposer 槽位装配（004）
├── prompt_sections/    # 默认 prompt 槽位 markdown 资源（004/005）
├── context/            # 上下文策略：Naive / Fifo / Summarizing（009）
├── tools/              # Tool Protocol + Registry + web_search（005）
├── memory_feed.py      # 事件 → 记忆素材投影（008）
└── paths.py            # 用户数据目录解析（决策 0002 §3.19）
```

## 与其他模块的依赖

- 依赖 `memory`（记忆读写）
- 依赖 `llm_providers`（LLM 通信）
- 被 `agent_bridge` / `tools` 依赖；不被 `memory` / `llm_providers` 反向依赖
