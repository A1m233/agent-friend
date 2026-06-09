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

需求 008 已落地：写入（LLM 抽取 + reconcile）与检索召回均已实现。

## 技术栈

- **存储基底**：SQLite（semantic / episodic + FTS5 trigram 全文检索）
- **召回策略（v1）**：`KeywordRetrieval`（基于 FTS5 关键词）
- **向量栈（预备，未实现）**：Chroma + 本地 BGE-small-zh，留作未来 `RetrievalStrategy` 的可插拔实现

详见 [`docs/decisions/0002-incubation-tech-stack/README.md`](../docs/decisions/0002-incubation-tech-stack/README.md) 第 3.15 / 3.16 节与 [`008 design`](../docs/requirements/008-engine-memory/design.md)。

## 内部结构

按职责拆分的扁平布局（无 `api/` 层；记忆通过 `agent` 在对话流中被调用，不直接对外暴露 HTTP）：

```
src/memory/
├── facade.py       # Memory 门面：observe / retrieve 稳定接口
├── factory.py      # build_memory() 装配入口
├── store/          # SqliteMemoryStore + schema（FTS5）
├── extraction/     # LLM 抽取 + reconciler + 异步 worker
├── retrieval/      # KeywordRetrieval + scoring + renderer
└── contracts.py    # 跨层数据契约
```

数据库默认落系统标准用户数据目录（`<user_data_dir>/memory/memory.db`，见 `agent.paths`），由调用方（CLI / bridge）通过 `build_memory(db_path=...)` 注入。

## 与其他模块的依赖

- 依赖 `llm_providers`（用 LLM 做记忆抽取）
- 被 `agent` 依赖
