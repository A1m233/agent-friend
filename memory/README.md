# memory

`agent-friend` 的**记忆系统**模块。

## 定位

项目的**核心护城河**。负责：

- 从对话流中识别值得记住的信息（事实 / 偏好 / 事件）
- 持久化存储（基于 SQLite）
- 跨会话不丢
- 根据当前对话上下文进行检索召回

体验底线：**禁止出现"我不记得 / 我没有持续记忆"等失忆话术**——这一点在 [`docs/decisions/0001-product-vision-and-roadmap/README.md`](../docs/decisions/0001-product-vision-and-roadmap/README.md) 第 1.3 节被列为"不可妥协的核心体验原则"。

## 状态

孵化期实现（详见 [`docs/requirements/001-foundation-chat-and-memory/requirement.md`](../docs/requirements/001-foundation-chat-and-memory/requirement.md) 第 4.2 节）。

## 技术栈

- **存储基底**：SQLite
- **向量栈（预备，是否启用待 `001/design.md`）**：Chroma + 本地 BGE-small-zh

详见 [`docs/decisions/0002-incubation-tech-stack/README.md`](../docs/decisions/0002-incubation-tech-stack/README.md) 第 3.15 / 3.16 节。

## 内部结构

按 0002 第 3.11 节的"双层"约定：

```
src/memory/
├── core/      # 纯 Python 库：记忆写入 / 检索（同进程调用）
└── api/       # （可选）跨进程访问入口
```

`api/` 是否需要、检索策略如何选择、记忆抽取策略，全部由 [`001/design.md`](../docs/requirements/001-foundation-chat-and-memory/) 决定。

## 与其他模块的依赖

- 依赖 `shared`（跨模块共享类型）
- 被 `agent` 依赖
- 与 `llm_providers` 平级，可能被 `memory` 自身用到（如用 LLM 做记忆抽取）
