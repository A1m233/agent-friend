# 008 引擎层长期记忆 - 进度

## 总体状态
<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
IN_PROGRESS

## 里程碑与任务

### M8.1 存储 + 写路径（异步）
- [x] `memory/contracts.py`：Utterance / ConversationFragment / MemoryContext / MemoryItem
- [x] `memory/store/schema.py`：DDL + Row dataclass + 迁移占位（trigram FTS）
- [x] `memory/store/sqlite_store.py`：episodic / semantic CRUD + FTS5
- [x] `agent/memory_feed.py`：list[Event] → ConversationFragment 投影（过滤策略单点）
- [x] `memory/extraction/extractor.py`：LLM 抽取
- [x] `memory/extraction/reconciler.py`：add vs supersede（独立组件）
- [x] `memory/extraction/worker.py`：AsyncExtractionWorker（单 worker FIFO）
- [x] `memory/extraction/prompts/extract.md`
- [x] `memory/facade.py`：observe / flush / close
- [x] `Conversation`：TurnDone 后接 observe 钩子
- [x] `memory/__init__.py` 导出 + memory 依赖 llm-providers
- [x] 测试（store / reconciler / feed / worker-mock）

### M8.2 读路径 + 注入
- [x] `memory/retrieval/strategy.py`：RetrievalStrategy + KeywordRetrieval
- [x] `memory/retrieval/scoring.py`：relevance × importance × time-decay
- [x] `memory/retrieval/renderer.py`：items → MemoryContext.rendered
- [x] `memory/facade.py`：retrieve 实装（返回 MemoryContext）
- [x] `Conversation._build_openai_messages_first_turn`：接 MemoryContext + extra_context
- [x] 标注 001 design §5/§6 的 retrieve 返回类型演进
- [x] 测试（retrieval / scoring / renderer）

### M8.3 pinned + 连续感 + 可观测
- [x] pinned 总注入 + 时间感渲染
- [x] CLI 调试区展示召回 / 写入（`AGENT_FRIEND_MEMORY_DEBUG`）；`memory.close()` 接退出
- [x] `build_memory` 装配 + `SessionManager` 接线（CLI in-process + bridge 持久化分支）
- [x] 真 LLM 冒烟跑通跨会话召回（AC-3 / AC-4）：建立记忆 → 全新会话隐式想起

## 实现日志
| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-05 | requirement.md / design.md 确认；进入 Phase 3 | 分支 feature/008-engine-memory |
| 2026-06-05 | M8.1~M8.3 代码 + 单测全部落地 | lint / mypy / 81 测试全绿 |
| 2026-06-05 | 真 LLM 冒烟通过 | run1 建记忆（name 自动 pinned）→ run2 全新会话隐式想起 Tom 是橘猫 |
| 2026-06-05 | bridge(HTTP) 模式接入记忆 | assembly 持久化分支注入 memory + lifespan 退出 drain；新增 `memory_db` / `memory_enabled` 配置；transient(OpenAI 无状态)分支不挂 |
