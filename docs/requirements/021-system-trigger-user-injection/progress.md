# 021 · 主动 source 触发信号改用 user role 注入 — 进度

## 总体状态
<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
COMPLETED

---

## 里程碑与任务

> 任务粒度按"涉及文件 + 涉及单测"组织。依赖顺序：M21.1 → M21.2 → M21.3 → M21.4（与 M21.3 独立）→ M21.5（依赖前面所有）→ M21.6 → M21.7。

### M21.1 协议层加 `trailing_user` 槽位
- [x] `agent/src/agent/context/protocol.py`：`assemble_messages` 加 `trailing_user: str | None = None` 参数，插入位置为 `new_user_input` 之后 / `trailing_system` 之前；rendered 为 `Message(role="user", content=trailing_user)`
- [x] `agent/src/agent/context/protocol.py`：`ContextManager.build_messages` Protocol 签名同步加该参数
- [x] `agent/src/agent/context/protocol.py`：docstring 的"不变量"清单加一条，说明 `trailing_user` 语义边界（仅 LLM 视图、不落盘、与 `new_user_input` / `trailing_system` 互不替代）
- [x] `assemble_messages` docstring 顺序行更新为 `[system?] + extra_context? + history + [new_user_input?] + [trailing_user?] + [trailing_system?]`

### M21.2 三个 context manager 透传
- [x] `agent/src/agent/context/naive.py`：`build_messages` 加 `trailing_user` 参数 + 透传给 `assemble_messages`
- [x] `agent/src/agent/context/fifo.py`：`build_messages` 加参数；首拼 + 截断循环两处 `assemble_messages` 各加 `trailing_user=trailing_user`
- [x] `agent/src/agent/context/summarizing.py`：`_AssembleParts` dataclass 加 `trailing_user: str | None` 字段；`assemble()` 方法透传；`build_messages` 入口 + `_AssembleParts` 构造 + `_fallback_result` 内 `self._fallback.build_messages(...)` 调用三处各加 `trailing_user=trailing_user`

### M21.3 `dispatch_system_turn` 切换 + `_assemble` 透传
- [x] `agent/src/agent/conversation.py`：`_assemble` 加 `trailing_user: str | None = None` 参数 + 透传给 `_context_manager.build_messages`
- [x] `agent/src/agent/conversation.py:454`：`self._assemble(trailing_system=system_prompt_addendum)` → `self._assemble(trailing_user=system_prompt_addendum)`（**方向 B 核心一行**）
- [x] 确认 `conversation.py:913` 工具循环兜底仍走 `trailing_system`（不动）
- [x] `dispatch_system_turn` 注释更新（line 453）：说明改用 `trailing_user` 注入 + 关联 021

### M21.4 sources.py 文案改造 + system prompt 加段
- [x] `agent/src/agent/runtime/sources.py`：`DEFAULT_BEDTIME_ADDENDUM` 改写为 user 视角 + `<system_trigger>...</system_trigger>` 包裹
- [x] `agent/src/agent/runtime/sources.py`：`DEFAULT_IDLE_ADDENDUM` 保留反思语义 + `<system_trigger>...</system_trigger>` 包裹
- [x] `agent/src/agent/prompt_sections/project_identity.md`：文末追加 `<system_trigger>` tag 识别元规则段落

### M21.5 单测调整 + 新增
- [x] `agent/tests/test_context_assembly.py`：新增 `test_assemble_trailing_user_position` / `test_assemble_all_three_trailings_no_error` / `test_naive_passes_trailing_user`；既有 case 保持原样不重写（与 design 微调：既有 case 不依赖"最后一条 role" assertion，无需改）
- [x] `agent/tests/test_context_fifo.py`：新增 `test_fifo_passes_trailing_user`
- [x] `agent/tests/test_context_summarizing.py`：新增 `test_summarizing_passes_trailing_user`（折叠 / 摘要 / 兜底三条路径）
- [x] `agent/tests/test_conversation_dispatch_system_turn.py`：新增 `test_dispatch_system_turn_injects_trailing_user`（两条 visibility 路径都验最后一条 role=user）+ `test_dispatch_system_turn_does_not_persist_trailing_user`
- [x] `agent/tests/test_runtime_sources.py`：新增 `test_default_bedtime_addendum_has_system_trigger_tag` + idle 版
- [x] `agent/tests/test_system_prompt_composer.py`：新增 `test_project_identity_has_system_trigger_rule`
- [x] `agent/tests/test_context_unified_entry.py`：副作用——既有 `_SpyContextManager.build_messages` 加 `trailing_user` 参数才能通过新 `_assemble` 调用
- [x] 全套件 438 测试全绿（M21 前 437→438，新增 8 个单测）

### M21.6 文档同步
- [x] `docs/requirements/014-engine-main-loop-and-bridge-push/design.md`：§6 末尾追加"§6.2 后续架构调整：trailing_system → trailing_user（021）"小节
- [x] `docs/requirements/014-engine-main-loop-and-bridge-push/progress.md`：实现日志加一行（日期 2026-06-17）指向本需求
- [x] `docs/issues/006-bedtime-prompt-history-hijack/README.md`：顶部状态 `open` → `resolved`；加修复需求 / commit 指针（待 commit 后回填 hash）；"方向 A 尝试记录"完整保留；文末追加"方向 B 落地记录"小节
- [x] `docs/issues/006-bedtime-prompt-history-hijack/replay_silent.py`：新建 silent turn 真跑脚本（含自动回声词检测）
- [x] **副作用**：rebase 期 origin/main 已合入 `feature/020-engine-tool-conversation-history`（占用 020 编号），本需求改用 021；同步把 main 上 `docs/requirements/020-engine-tool-conversation-history/smoke.py` 的 pre-existing ruff format 错误顺手修了（让门禁过得去）

### M21.7 验证
- [x] `./scripts/check` 全绿（ruff check + format check + mypy + pytest 438 测试，零退化）
- [x] 真跑授权（用户 2026-06-17 明确授权 8-12 次 DeepSeek 调用）
- [x] 真跑 `replay.py`（bedtime 路径）：v4-flash 2/2 + v4-pro 2/2，**全部主动开口"该睡了"语义**，无续写"日本小女孩 meme"上文
- [x] 真跑 `replay_silent.py`（silent 路径）：v4-flash 2/2 + v4-pro 2/2，**全部不冒泡 + 无回声词命中**，高质量反思
- [x] AC-1 ~ AC-7 全部对应通过

---

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-17 | M21.1 协议层加 trailing_user 槽位 | `assemble_messages` 顺序新增 trailing_user 位置（new_user_input 之后、trailing_system 之前）；`ContextManager.build_messages` Protocol 签名同步；不变量 docstring 更新 |
| 2026-06-17 | M21.2 三个 context manager 透传 | naive / fifo / summarizing 全部透传；summarizing 的 `_AssembleParts` 加字段；fifo 首拼 + 截断、summarizing 折叠 / 摘要 / 兜底三条路径全覆盖 |
| 2026-06-17 | M21.3 dispatch_system_turn 切换 + _assemble 透传 | `_assemble` 加 trailing_user 参数；line 454 一行切换 `trailing_system=` → `trailing_user=`（方向 B 核心）；line 913 工具循环兜底 trailing_system 不动（语义合适） |
| 2026-06-17 | M21.4 sources.py 文案 + system prompt 加段 | DEFAULT_BEDTIME_ADDENDUM 去掉方向 A 残留"【主动轮·系统触发】"前缀，改 user 视角"到约定休息时间啦"+ `<system_trigger>` tag 包裹；DEFAULT_IDLE_ADDENDUM 保留反思语义 + tag 包裹；project_identity.md 末尾追加 tag 识别元规则段落（跟"不暴露 AI"等元规则同级，永远生效；~150 tokens 影响、文本静态、不破 prompt cache） |
| 2026-06-17 | M21.5 单测调整 + 新增 8 个 case | 既有 case 不重写（与 design 微调，扫描表明既有 case 不依赖"最后一条 role" assertion）；test_context_assembly 加 3 个、test_context_fifo 加 1 个、test_context_summarizing 加 1 个、test_conversation_dispatch_system_turn 加 2 个、test_runtime_sources 加 2 个、test_system_prompt_composer 加 1 个；副作用：test_context_unified_entry 的 `_SpyContextManager` 加 trailing_user 参数才能通过新 `_assemble` 调用 |
| 2026-06-17 | M21.6 文档同步 + replay_silent | 014 design §6.2 补段；014 progress 加日志行；issue 006 README 顶部状态 → resolved + 方向 B 落地记录小节（方向 A 尝试记录保留）；新建 replay_silent.py（含自动回声词检测）；rebase origin/main 同步 020 占编号 + main 上 020 的 smoke.py pre-existing ruff format 顺手修 |
| 2026-06-17 | M21.7 验证 sweep | `./scripts/check` 全绿（438 测试零退化，含本期新增 8 个单测）；按 llm-api-confirm rule 用户授权后真跑 8 次 DeepSeek：bedtime（v4-flash + v4-pro 各 2 次）全部主动开口"该睡了"语义、**无一例**续写上文；silent（v4-flash + v4-pro 各 2 次）全部不冒泡 + 无回声词命中、输出高质量反思——方向 B + 三层缓解在 DeepSeek 两个模型上全部成立 |

---

## 验收小结

> AC 标号对应 [`requirement.md` §6](./requirement.md#6-验收标准)。

| AC | 标题 | 状态 | 依据 |
|---|---|---|---|
| AC-1 | 协议层 trailing_user 顺序正确 | ✅ | `test_assemble_trailing_user_position` + `test_assemble_all_three_trailings_no_error` |
| AC-2 | 三个 context manager 透传 | ✅ | `test_naive_passes_trailing_user` + `test_fifo_passes_trailing_user` + `test_summarizing_passes_trailing_user`（三策略覆盖） |
| AC-3 | dispatch_system_turn 切换正确 | ✅ | `test_dispatch_system_turn_injects_trailing_user`（mock LLM 拦 messages、两条 visibility 都验 role=user）+ `test_dispatch_system_turn_does_not_persist_trailing_user`（trailing_user 不落盘） |
| AC-4 | 缓解措施落地 | ✅ | `test_default_*_addendum_has_system_trigger_tag`（sources 常量含 tag）+ `test_project_identity_has_system_trigger_rule`（system prompt 含 tag 识别说明） |
| AC-5 | 真跑验证（DeepSeek） | ✅ | bedtime 4/4 主动开口（v4-flash + v4-pro 各 2 次，无一续写"日本小女孩 meme"上文）；silent 4/4 不冒泡 + 无回声词（自动回声词检测脚本 0 命中）；真 LLM 端到端在两个模型上方向 B 假设成立 |
| AC-6 | 全量回归 + 门禁全绿 | ✅ | `./scripts/check` 全绿（438 测试，含本期新增 8 个单测、既有零退化） |
| AC-7 | 文档闭环 | ✅ | 014 design §6.2 补段；014 progress 加日志；issue 006 状态 → resolved + 方向 A 记录保留 + 方向 B 落地小节；commit hash 待最终 commit 后回填 |
