# 002 · 引擎层会话管理 - 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->

COMPLETED

## 当前里程碑

**M2.1 ~ M2.3 完成 + M2.3 follow-up（CLI 懒加载）+ AC-1 ~ AC-8 全部 PASS（除 AC-7 Ctrl+C 子项需用户手动验证）**

## AC 验收记录（2026-05-14）

| AC | 标题 | 状态 | 备注 |
|---|---|---|---|
| AC-1 | 跨进程恢复 | ✅ PASS | session_id 一致；banner 显示从事件流派生的 `cute_friend` + `v4-pro`（不是 initial 值） |
| AC-2 | 异常退出不丢全部 | ✅ PASS | SIGKILL 测试：前 2 轮完整保留，第 3 轮整轮丢失（finally 未执行，符合 design §1.2"接受丢最后一条"+§4.7.3"OS-level 强杀 vs Python-level 异常"语义） |
| AC-3 | 切 persona 不中断 | ✅ PASS | `default → cute_friend`，下一句回复风格差异显著（emoji + 语气词 + "超"），上下文延续 |
| AC-4 | 切 model 不中断 | ✅ PASS | `v4-flash → v4-pro`，上下文延续；assistant_message.meta 准确记录 `cute_friend + v4-pro` |
| AC-5 | 切换可追溯 | ✅ PASS | `grep '"type": "persona_change"'` / `model_change` 立即定位，含 from/to |
| AC-6 | 列表性能 | ✅ PASS | 51 个 session（含 1 个 238KB 大文件）`list()` 平均 1.25 ms ≪ 100ms 阈值 |
| AC-7 | CLI 不退化 | ✅ 主要 PASS | 多轮 / 流式 / 错误兜底 / `/persona unknown` 等已覆盖；Ctrl+C 中断当前回复在 stdin 喂场景不便测，**留用户手动验证** |
| AC-8 | 持久化可读 | ✅ PASS | 11 行 jsonl 全合法 JSON；type / uuid / ts / payload / meta 五大字段结构清晰 |

**Token 消耗估算**：≈ 2 k token（DeepSeek v4-flash + 少量 v4-pro），约 ¥0.005。

**测试副产物**：
- `data/sessions/ef73b760-*.jsonl`（AC-1 / AC-3 / AC-4 / AC-5 / AC-8 数据）
- `data/sessions/244d282e-*.jsonl`（AC-2 SIGKILL 测试，前 2 轮完整 + 第 3 轮缺失）
- 旧 2 个空 session 文件备份在 `.ac-tmp-backup/`（由旧"启动即建"策略产生，user 自行决定删/留）

## 任务列表

### M2.1 引擎层骨架（不接 Conversation / 不动 CLI）—— **已完成 ✓**

- [x] 创建 `agent/sessions/` 子包目录
- [x] `agent/sessions/errors.py`：`SessionError` / `SessionNotFoundError` / `SessionPersistError` / `SessionCorruptError`
- [x] `agent/sessions/events.py`：`Event` dataclass + 5 类事件 + jsonl 序列化（`to_jsonl` / `from_jsonl`）
- [x] `agent/sessions/session.py`：`Session` 数据类 + `current_persona` / `current_model` / `messages` 派生属性
- [x] `agent/sessions/store.py`：`SessionStore` Protocol + `JsonlSessionStore` 实现 + `SessionSummary`
- [x] `agent/sessions/manager.py`：`SessionManager`（`create / open / list / delete / latest`；`start_conversation` 占位）
- [x] `agent/sessions/__init__.py`：公开 API 重导出
- [x] `agent/__init__.py`：加上 `Session / SessionManager / SessionStore / SessionError` 系列重导出
- [x] lint（ruff）/ typecheck（mypy）通过
- [x] M2.1 smoke 验证：Python 脚本完成 "create → append 几条事件 → list → load → delete" 全流程

**M2.1 落地决策小记**（与 design.md 一致，仅记下实际选型细节，供后续 review）：

- `Event.ts` 序列化用 **UTC ISO 8601 带 `Z` 后缀**；naive datetime 视作 UTC（容错）。
- `Session.messages` 派生时把 `Event.uuid` 暂存到 `Message.meta["event_uuid"]`；M2.2 加 `Message.uuid` 字段后再切到正式字段（design §4.6）。
- `Session.append` 仅追加到**内存**事件流，IO 调用方负责（典型：先 `store.append_event` 成功后再 `session.append`，避免内存先于落盘）。
- `SessionManager` 的 factory / context_manager 在 M2.1 是 **可选参数**（设 default `None`）；`start_conversation` 调用时若未注入则抛 `AgentError`。M2.2 会让 CLI 必传，约束在调用方而非签名。
- `JsonlSessionStore.list` 首行损坏跳过 + 不记日志（孵化期不引日志框架，design §4.3.2）。

### M2.2 接入 Conversation + 切换 persona/model —— **已完成 ✓**

- [x] `Message` 加 `uuid` 字段（`field(default_factory=lambda: str(uuid4()))`），`from_dict` 兼容缺字段旧数据
- [x] `Session.messages` 把 `Event.uuid` 直接映射到 `Message.uuid`（移除 M2.1 的 `event_uuid` meta 兜底）
- [x] `Conversation` 构造签名变更：去 `history`，加 `session / store / 工厂`
- [x] `Conversation.send/stream` 改成 append event 到 session + store；assistant_message.meta 带 persona/model 快照
- [x] `Conversation.switch_persona / switch_model` 新增
- [x] `Conversation.dump / load / reset` 改为抛 `AttributeError` 引导到新 API
- [x] `SessionManager.start_conversation` 实装
- [x] lint / typecheck 通过
- [x] M2.2 mid-check（Mock LLMClient smoke）：send + stream + switch + reopen + 跨进程恢复 + partial 落盘全部正确

**M2.2 落地决策小记**：

- `switch_persona` 实施顺序：`factory(name)` → **预先 `build()`**（客户端预检 persona 文件）→ append event → swap。**关键修复**：M2.3 self-check 时发现，若把 `build()` 推迟到 swap 后，会出现"无效 persona 切换却落盘"的半状态 bug。提前 `build()` 后，无效 persona 直接抛 `PersonaNotFoundError`，事件不落盘，状态完全不变。
- `switch_model` 没有客户端预检（model 名无法本地验证），无效 model 切换会**落盘**，下次 send 才会因为 LiteLLM 报 `LLMBadRequestError`；用户可以再 `/model` 切回正确值修正。这是 design §4.7.4"切换失败可能留半状态"明确允许的语义。
- `Conversation.history` 不再有内部副本，直接走 `session.messages` property，每次 O(n) 遍历事件流；孵化期 n < 1000，无性能问题。

### M2.3 CLI 改造 —— **已完成 ✓**

- [x] `tools/cli/__main__.py` 整体重写，走 `SessionManager` 路径
- [x] 删除 `save_session` / `find_latest_session` / `RESUME_LATEST_SENTINEL` 旧函数（const 名沿用作"取最新"哨兵）
- [x] `make_spec_with_thinking_off` 接 `model_override` 参数，供 `_llm_factory` 和 `--model` 使用
- [x] `--resume <session_id>` 接 session_id（**支持前缀匹配**）；`--resume` 不带参数取最新；无 `--resume` 新建；新增 `--model <name>` 参数
- [x] 新增 `/sessions`（rich Table 渲染）/ `/open <id>` / `/persona <name>` / `/model <name>` / `/new` 命令
- [x] 删除 `/reset` 命令（保留命令名兜底，告知用户改用 `/new`）
- [x] `SessionPersistError` 在主循环 + 每个 slash 命令中红字提示但不中断
- [x] `SessionNotFoundError` / `SessionCorruptError` 启动时退出，运行时提示
- [x] lint / typecheck 通过
- [x] M2.3 self-check：CLI `--help`、启动 banner、`/sessions` 表格、`/new`、`/persona` 无效/有效、`/model`、`/quit` 全部通

**M2.3 落地决策小记**：

- `/open <id>` **支持前缀匹配**（design §4.8.3 已写）：歧义时打印候选列表，无匹配时 stderr 提示。
- `/reset` 命令保留命令名，但提示"已删除，请用 /new"——避免用户误以为 typo。
- `SessionPersistError` 在所有 slash 命令的 `try/except` 里都做了兜底，红字提示但**不中断主循环**。
- CLI 启动时 `make_spec_with_thinking_off()` 做一次预检确保 API key 配齐；session.create / start_conversation / open 各自的异常都有针对性提示。
- 输出风格保持 001 习惯：rich Console + `[red]\[xxx][/red]` 错误前缀。

### M2.3 follow-up · CLI 改为懒加载（review 后修正）

**触发原因**：用户 review 时发现"启动 CLI 即 `mgr.create()`"导致**每开一次 CLI 不发消息 /quit 就留下一个空 session 文件**，污染 `data/sessions/` 和 `/sessions` 列表。

**架构定性**：引擎层（`SessionManager`）的 `create / open / start_conversation / send` 四件事**本来就是分离的**——这只是 CLI 层的策略问题，与引擎层混写无关。前端接口（未来 HTTP）的 `POST /sessions` 与 `POST /sessions/:id/messages` 仍然天然分离。

**修正**：CLI 改为"懒加载"——启动后处于**未绑定状态**（`session/conv` 都是 `None`），用户首条消息 / `/new` / `/open` 才触发创建或打开。

具体改造：

- 引入 `_CliContext` dataclass 持有 `(mgr, session?, conv?, pending_persona, pending_model)`，统一管理"绑定 / 未绑定"状态机
- 启动 banner 区分两态显示
- 未绑定时 `/persona <name>` 做客户端预检后更新 `pending_persona`（不调 `switch_persona`）；`/model <name>` 直接更新 `pending_model`（model 无法本地预检）
- 未绑定时 `/sessions` / `/open <id>` / `/new` 都可用；`/open` 和 `/new` 直接 bind
- 输入消息时 `ctx.ensure_bound()` 触发 `mgr.create` + `start_conversation`，打印 `→ 新建会话: <id>` 状态行
- `--resume`（带 id / 取最新）依然立刻 open——这是用户显式表达"我要恢复"

**冷烟验证**（不调 LLM）：

| Case | 操作 | 期望 | 实际 |
|---|---|---|---|
| 1 | 启动 → `/quit` | 0 个 session 文件 | ✅ 0 |
| 2 | 启动 → `/sessions` / `/persona unknown` / `/persona default` / `/quit` | 0 个 session 文件 | ✅ 0 |
| 3 | 启动 → `/new` × 2 → `/quit` | 2 个 session 文件 | ✅ 2 |

**未改 design.md**：本次修正属于 CLI 层策略调整，引擎层接口未变。design §4.8.1 的 `_select_session` 描述与新实现有差异——计划在 002 全部 AC 验收后做一次 design.md 回滚校准。

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-05-14 | 启动 Phase 3，建 progress.md，开始 M2.1 | —— |
| 2026-05-14 | M2.1 引擎层骨架全部完成；lint / typecheck / smoke 全绿 | 等待用户 review 后启动 M2.2 |
| 2026-05-14 | M2.2 接入 Conversation 完成；mid-check mock smoke 全绿 | 用户授权一气呵成 M2.2+M2.3 |
| 2026-05-14 | M2.3 CLI 改造完成；self-check 冷烟通过；修复 `switch_persona` 预检 bug | 等待用户跑真 LLM 验收 |
| 2026-05-14 | M2.3 follow-up：CLI 改为懒加载（不再"启动即建"空 session）；冷烟 3 case 全过 | review 后修正 |
| 2026-05-14 | AC-1 ~ AC-8 全部 PASS（详见 §AC 验收记录）；Token ≈ 2 k；耗时 < 1 min | 002 整体完成 |
