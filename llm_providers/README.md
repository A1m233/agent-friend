# llm_providers

`agent-friend` 的 **LLM Provider 适配层**。

## 定位

项目里**唯一**与 LLM API 直接通信的模块，对所有上层（agent、memory）暴露统一接口。

职责严格收敛在"打 LLM 这通电话"：
- 接收 messages，返回 completion（含流式）
- 抹平不同 Provider 的接口差异
- 集中处理 API key、超时、重试等基础设施级别的事

**禁止**做的事：
- 多轮对话编排（属于 `agent`）
- 记忆召回 / 拼装（属于 `agent` + `memory`）
- 工具调用编排（属于 `agent`）
- 任何业务逻辑

> 这一边界在 [`docs/decisions/0002-incubation-tech-stack/README.md`](../docs/decisions/0002-incubation-tech-stack/README.md) 第 3.14 节被显式强调："LiteLLM 只做'打 LLM 这通电话'，所有 agent 编排都在 `agent/core/` 自己实现"。

## 技术栈

- **抽象层**：LiteLLM（一套 API 覆盖 100+ Provider）
- **孵化期主力 Provider**：DeepSeek

详见 0002 第 3.13 / 3.14 节。

## 状态

孵化期实现。本模块的代码量预计极少（LiteLLM 已经做完了大部分工作），主要是封装一层项目内的统一调用入口。

## 与其他模块的依赖

- 依赖 `shared`（跨模块共享类型）
- 被 `agent` 依赖
- 可能被 `memory` 依赖（如果记忆抽取要用 LLM）
