# 020 工具能力扩展：会话记录查询 - 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
COMPLETED

## 任务列表

### M20 阶段 1：工具内核 + 单测

- [x] T1. 创建 `agent/src/agent/tools/builtin/conversation_history/` 子包骨架（`__init__.py` / `tool.py` / `time_parser.py` / `render.py`）
- [x] T2. 实现 `time_parser.py`：`parse_time_expression(text, now, *, bias)` —— ISO 8601 + 16 种自然语言短语（小时/日/周/月/年级）+ `start` / `end` 双 bias 语义
- [x] T3. 实现 `render.py`：`format_hits` / `_format_one` / `_format_time`（同日/同周/同年/跨年四档）/ `_truncate(200)` / inline reminder
- [x] T4. 实现 `tool.py`：`ConversationHistoryTool` 类 + `Hit` dataclass + `_scan`（粗筛 summary → lazy load → events 过滤）
- [x] T5. 单测 `time_parser`：每短语 × 双 bias + 边界（N=0/1/大 N，跨月/跨年回退）+ 失败 case —— **28 个 case 全过**
- [x] T6. 单测 `render`：各时间档 + 空结果拟人兜底 + 配对 / 孤立 + truncate + reminder 存在性 —— **15 个 case 全过**
- [x] T7. 单测 `tool.invoke`：happy path + 失败兜底（时间格式错 / SessionPersistError）+ 参数组合（mock `SessionStore`）—— **21 个 case 全过**

### M20 阶段 2：装配集成 + runtime_context + 集成测

- [x] T8. 改造 `agent/src/agent/tools/factory.py`：`make_default_registry()` 加 `session_store: SessionStore | None = None`；既有 3 个测试不回归 + 新增 3 个 case 覆盖新行为
- [x] T9. 改造 CLI 入口 `tools/src/tools/cli/__main__.py`：构造 `SessionManager` 时把 `store` 传给 factory（一行改动）
- [x] T10. 追加 `agent/src/agent/prompt_sections/runtime_context.md` 末尾"## 关于过去和这位用户聊过的事"一段
- [x] T11. 集成测（真调 LLM，需用户授权）：装配含本工具的 registry → 验证 AC-1 / AC-3 / AC-4 / AC-5 / AC-6 —— **smoke.py 4 个剧本全通过**
- [x] T12. 手动验收 AC-2（没记忆时拟人兜底）+ AC-7（失忆话术红线）—— **smoke.py 4 个剧本一并覆盖**
- [x] T13. 既有 CI 测试不回归（`./scripts/check`）—— **lint + typecheck + 全量 pytest + 前端 lint/test 全过**

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-17 | M20 阶段 1：`conversation_history` 子包完整实现（time_parser / render / tool）+ 64 单测全过 | 实现严格遵循 design §4.1 ~ §4.4；time_parser 手撸正则覆盖 16 种短语 + ISO 8601；render 拟人化输出不含 schema 字眼；tool 走 NullSessionStore 自然降级（N-4） |
| 2026-06-17 | M20 阶段 2 大部分：factory 改造 + CLI 入口接入 + runtime_context.md 追加引导 | factory 加 `session_store=None` 可选参数（向后兼容）；CLI 启动入口一行改动把 store 注入 factory；runtime_context.md 末尾追加"关于过去和这位用户聊过的事"一段，呼应 005 §4.10 的运行时引导思路 |
| 2026-06-17 | `./scripts/check` 全绿（lint + mypy strict + pytest 250 个 agent 测试 + 26 个 agent_bridge 测试 + 84 个前端 vitest 测试） | 顺手 ruff format 了 2 个 main 上 pre-existing 的非本期文件（docs/issues/015 / experiments/qq-bot-poc）以解 check 阻塞；这两条 format 改动语义不变 |
| 2026-06-17 | T11 + T12 真调 LLM smoke 验证（`smoke.py`）全过 | 隔离 tmp 数据目录 + 注入 3 个历史 session（日语 5d / 小狗 2d / 跑步 1d），跑 4 个剧本：(1) 问聊过 → 调 recall + 拟人整合"上周五说过..."；(2) 问没聊过 → 调 recall + "头一回听你提起..."；(3) 时间范围 → 传 `since="昨天"` 正确；(4) "之前聊过什么" → 整合 3 个真实 session 内容，规避"我不记得"红线。0 红线词命中。**已知边界**：substring 找不到语义相似词（如剧本 3 query="运动"无法命中"跑步"内容）——design 已声明 YAGNI（§1.2 / N-5），AI 回应"没聊过"truth-faithful 不算 bug |


