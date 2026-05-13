# agent

`agent-friend` 的 **Agent 引擎**模块。

## 定位

整个项目的核心 IP 所在地。负责：

- 多轮对话编排（上下文管理、prompt 拼装）
- 调用 `memory` 模块进行记忆写入与召回
- 调用 `llm_providers` 模块完成 LLM 通信
- 错误重试与兜底（含禁用"我不记得"等失忆话术）
- 未来：编排 tool calling

> 本模块的设计原则之一：**核心编排逻辑必须独立于任何第三方 agent 框架**（如 LangChain），以保证长期可演进性。详见 [`docs/decisions/0002-incubation-tech-stack/README.md`](../docs/decisions/0002-incubation-tech-stack/README.md) 第 3.14 节。

## 状态

孵化期实现（详见 [`docs/requirements/001-foundation-chat-and-memory/requirement.md`](../docs/requirements/001-foundation-chat-and-memory/requirement.md)）。

## 内部结构

按 0002 第 3.11 节的"双层"约定：

```
src/agent/
├── core/      # 纯 Python 库：对话编排、上下文管理（同进程调用）
└── api/       # 薄薄一层 FastAPI（HTTP + SSE，跨进程调用入口）
```

孵化期可能仅实现 `core/`，`api/` 在前端/IM 等跨进程调用方上线时再补。具体由 [`001/design.md`](../docs/requirements/001-foundation-chat-and-memory/) 决定。

## 与其他模块的依赖

- 依赖 `memory`（记忆读写）
- 依赖 `llm_providers`（LLM 通信）
- 依赖 `shared`（跨模块共享类型）
- 不被孵化期任何其他 Python 模块反向依赖
