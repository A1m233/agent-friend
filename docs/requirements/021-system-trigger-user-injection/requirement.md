# 021 · 主动 source 触发信号改用 user role 注入（system-trigger user-role injection）

> System-trigger user-role injection
>
> 把 014 主动 source（`BedtimeSource` / `IdleReflectionSource` / 未来 A/D 类）注入触发信号的方式从 `role="system"` trailing_system 改为 `role="user"` trailing_user。架构层根治"trailing_system 被 history 拽走、LLM 续写上文"的问题（见 [`issue 006`](../../issues/006-bedtime-prompt-history-hijack/)），同时按 Claude Code 同款模式加 `<system_trigger>` tag 包裹 + system prompt 训练，挡住"模型把注入内容归因给用户"的副作用。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 1. 背景

### 1.1 现状

014 design §6 当年选了"system trigger → `trailing_system` 注入"路径：在发给 LLM 的 messages 末尾追加一条 `role="system"` 消息，承载 `system_prompt_addendum`（如 bedtime 的"该睡了"提示）。这条 addendum 不写入 `session.events`（落盘只留 `system_trigger` marker），仅活在当前请求的 LLM 视图里。

### 1.2 这次要做什么

[`issue 006`](../../issues/006-bedtime-prompt-history-hijack/) 在 015 M15.8 端到端真跑中暴露：session 历史末尾若是上一轮 assistant 提问，BedtimeSource fire 后 LLM **不主动提醒"该睡了"**，而是以 user 口吻续写上一轮、虚构对话内容。

issue 006 方向 A（强化 prompt 文案）已在 2026-06-17 真跑证伪（DeepSeek v4-flash + v4-pro 各跑一次均失败，详见 issue README "方向 A 尝试记录"）：根因不是文案不够强，是 **LLM API 语义层问题——`role="system"` 接在 history 末尾不是 turn 切换信号**，LLM 把它当现有对话的额外提示、顺着前一轮 assistant 续写。

升级到方向 B：把 trailing addendum 的 role 从 `system` 切到 `user`——user role 才是 chat-completions 语义里真正的"新一轮 turn 开始"信号。本期**对所有主动 source（bedtime / idle reflection / 未来 A/D 类）通用**，在架构层根治该类问题，不打补丁。

### 1.3 这是 014 的架构调整，不是 issue 修复

014 实施期没暴露这个问题（手工真跑用的 session history 末尾刚好不是"诱导续写"的形态）；015 端到端真跑覆盖到才暴露。所以本期是 014 设计当年选错路径的**后续架构调整**，按 [`dev-workflow.mdc`](../../../.cursor/rules/dev-workflow.mdc) 应走 `feature/021-...` 分支（非 `fix/006-...`），影响范围是协议层 + 三个 context manager + dispatch_system_turn + sources 文案 + 014 文档更新。

### 1.4 业界参考

Claude Code 用同样的 `role="user"` 注入模式（`<system-reminder>` block），并配套三层缓解：(a) 用 tag 包裹让 LLM 视觉上区分、(b) 在 system prompt 里训练模型识别 tag 含义、(c) `isMeta: true` 标记从持久化日志剥离。其中 (a)(b) 是本期要做的；(c) 本期天然成立——`trailing_user` 只活在 messages 拼装阶段、不写入 `session.events`。

Claude Code 自身 issue [#23537](https://github.com/anthropics/claude-code/issues/23537) / [#27128](https://github.com/anthropics/claude-code/issues/27128) 也报告了"不加缓解时模型会把注入内容归因给用户"——本期 §6 AC-4 / AC-5 专项验证。

---

## 2. 本期范围（In Scope）

| 模块 | 目标 |
| --- | --- |
| `agent/src/agent/context/protocol.py` | `assemble_messages` 加 `trailing_user` 参数；`ContextManager.build_messages` Protocol 同步加该参数 |
| `agent/src/agent/context/{naive,fifo,summarizing}.py` | 三个 context manager 实现透传 `trailing_user` |
| `agent/src/agent/conversation.py` | `_assemble` 加 `trailing_user` 参数；`dispatch_system_turn` 改用 `trailing_user=addendum`（不再走 `trailing_system`） |
| `agent/src/agent/runtime/sources.py` | `DEFAULT_BEDTIME_ADDENDUM` / `DEFAULT_IDLE_ADDENDUM` 文案改成"user 视角对 agent 的提示"语气 + 用 `<system_trigger>` tag 包裹 |
| `agent/src/agent/system_prompt/`（具体文件由 design 锁） | system prompt 加一段说明：遇到 `<system_trigger>` tag 包裹的 user 消息，表示这是定时器触发，不是真用户说的话 |
| `docs/requirements/014-engine-main-loop-and-bridge-push/{design.md,progress.md}` | 在 014 design §6 + progress 末尾补一段"021 架构调整"记录，留 archaeology 指针 |
| `docs/issues/006-bedtime-prompt-history-hijack/README.md` | 状态改 resolved + 文末补"已在 commit `<hash>`（feature/021）修复" |
| 单测 | protocol.py 顺序断言 + context manager 透传 + dispatch_system_turn 切换 + sources 文案 tag 检查 |
| 真跑验证 | 用 `docs/issues/006-bedtime-prompt-history-hijack/replay.py` 复用同份 session jsonl 跑真 LLM，验证方向 B 在 DeepSeek 上成立 |

---

## 3. 非目标（Out of Scope）

- **删除 `trailing_system` 槽位**：保留给"兜底收尾"用（`conversation.py:913` 工具调用循环兜底收尾仍走 `trailing_system`，语义合适——那是 LLM 自己 tool_call 后被强制收尾，不是 turn 切换）。
- **改 user 正常输入路径**：pull encoder / `Conversation.send` / `stream` 行为零变化；`new_user_input` 参数不动。
- **改 session 落盘格式**：`system_trigger` marker event 行为不变；`trailing_user` 只活在 `assemble_messages` 拼装阶段、不入 events；老 session JSONL 完全兼容。
- **强制 `trailing_user` 与 `trailing_system` / `new_user_input` 互斥 assert**：三者语义不同（`new_user_input` 来自 caller 已落盘；`trailing_user` 仅 LLM 视图不落盘；`trailing_system` 兜底收尾），实际场景下不会同时出现。在 docstring 说明语义边界即可，不加 runtime assert。
- **改 `<system_trigger>` tag 名 / Claude Code 同款 `<system-reminder>`**：用 `<system_trigger>` 与项目内既有 `system_trigger` event type 命名一致，便于排查；不刻意对齐 Claude Code 的 tag 名。
- **多 LLM provider 验证**：本期只在 DeepSeek（项目默认）真跑验证；其他 provider（Anthropic / OpenAI / Gemini）行为留观察，未来发现问题再立项。
- **assistant 回应过滤 / 回声检测**：LLM 输出里检测"既然你说"等回声词、拒答重跑——是缓解 D 类副作用的高成本方案，本期不做。AC-5 真跑观察即可，若 DeepSeek 在 tag + system prompt 加固下仍频繁回声，再立项加 D 类。

---

## 4. 核心需求详述

### 4.1 协议层：trailing_user 槽位

- **R-4.1.1 `assemble_messages` 加 `trailing_user` 参数**：新参数 `trailing_user: str | None = None`，rendered 为 `Message(role="user", content=trailing_user)`，位置紧跟 `new_user_input` 之后、`trailing_system` 之前。完整顺序：`[system?] + extra_context? + history + [new_user_input?] + [trailing_user?] + [trailing_system?]`。
- **R-4.1.2 `ContextManager.build_messages` Protocol 同步**：Protocol 签名加 `trailing_user: str | None = None`；既有调用方默认 None，行为零变化。
- **R-4.1.3 不变量更新**：protocol.py docstring 的"不变量"清单加一条说明 `trailing_user` 位置 + 与 `new_user_input` / `trailing_system` 的语义边界。
- **R-4.1.4 不强制互斥**：`trailing_user` / `trailing_system` / `new_user_input` 三者同时传不抛异常，按上述顺序拼接即可；调用方语义责任。

### 4.2 三个 context manager 透传

- **R-4.2.1 naive / fifo / summarizing 全部透传**：三个 manager 的 `build_messages` 实现加 `trailing_user` 参数并透传给 `assemble_messages`；既有 `trailing_system` 透传逻辑作 reference。
- **R-4.2.2 summarizing 的 `MessageParts` 同步**：`summarizing.py:59` `MessageParts` dataclass 加 `trailing_user` 字段；`assemble()` 透传。

### 4.3 dispatch_system_turn 切换

- **R-4.3.1 bedtime 路径切到 trailing_user**：`Conversation.dispatch_system_turn` 在 `output_visibility="user"` 路径下，把 `self._assemble(trailing_system=system_prompt_addendum)` 改为 `self._assemble(trailing_user=system_prompt_addendum)`。
- **R-4.3.2 silent turn 路径同步切换**：`output_visibility="memory_only"` 路径也切到 `trailing_user`——架构层一致 + 同份代码路径少分支。AC-5 真跑验 silent turn 输出仍合预期（仍不冒泡到用户、仍记 `memory_observation`，且 LLM 不出现"既然你说"等回声词污染 memory）。
- **R-4.3.3 不落盘**：`trailing_user` 这条消息**只**活在当前请求的 messages 拼装；`_append_system_trigger_event` 落 marker 行为不变；session.events 不增加新事件类型。

### 4.4 缓解措施：tag 包裹 + system prompt 训练

- **R-4.4.1 addendum 文案 tag 包裹**：`DEFAULT_BEDTIME_ADDENDUM` / `DEFAULT_IDLE_ADDENDUM` 的文本用 `<system_trigger>...</system_trigger>` 包裹（具体文案在 design 锁），让 LLM 视觉上跟普通 user 消息区分。
- **R-4.4.2 system prompt 加 tag 识别说明**：在 agent 顶层 system prompt（位置 design 锁）加一段约 1-3 句的说明：用 user role 但被 `<system_trigger>` tag 包裹的消息，是系统定时器触发，不代表用户真的说了这句话；agent 应当主动开口、不要把这段当作用户提问回应、不要在后续轮次复述这段。
- **R-4.4.3 文案"user 视角"对齐**：包裹内文案改成更贴近 "user 提醒 agent" 口吻（去掉"按你当前 persona 自然说一句"等显得是给 agent 的指令的写法），让 LLM 视觉上看到的就是 user role 角色一致的提示。

### 4.5 文档同步

- **R-4.5.1 014 design.md 补段**：在 014 design §6（`dispatch_system_turn` 入口）末尾补一段 "021 架构调整"小节，说明 trailing_system → trailing_user 切换的动因（issue 006 真跑证伪）、影响、下游同步点。
- **R-4.5.2 014 progress.md 补段**：实现日志表加一行指向本需求；总体状态保持 `COMPLETED`（014 主体不动）。
- **R-4.5.3 issue 006 闭环**：状态改 `resolved`，文末"方向 A 尝试记录"保留（archaeology 价值）；新增"已在 commit `<hash>`（feature/021）修复"指针。

### 4.6 向后兼容

- **R-4.6.1 既有外部接口零变更**：`Conversation.send` / `stream` / `dispatch_system_turn` 签名不动；新增 `trailing_user` 仅协议层内部参数。
- **R-4.6.2 既有事件流格式零变更**：session JSONL 落盘格式不变；老 session 文件兼容。
- **R-4.6.3 既有 trailing_system 路径不退化**：`conversation.py:913` 兜底收尾路径仍走 `trailing_system`，不切；既有该路径相关测试全绿。

---

## 5. 使用约束

- **真实 LLM 调用授权**：AC-5 真跑要调 DeepSeek API，按 [`llm-api-confirm`](../../../.cursor/rules/llm-api-confirm.mdc) rule 跑前获用户明确授权；预估调用量级 6-10 次（v4-flash + v4-pro 各 2-3 次 + 失败重试余量）。
- **承接 014 协同约束**：本期不改 `memory.observe` / `retrieve` 签名、不改 `ConversationFragment` 形状（与 013 协同硬约束一致）；silent turn 仍走 014 设计的"自构 fragment"路径（speaker=agent）。

---

## 6. 验收标准

> 本节 AC 是机制层面 + 行为层面双轨：协议 / 实现的结构性正确（机制）+ 真 LLM 下行为正确（产品体验"主动开口"的方向 B 假设成立）。

- **AC-1 协议层 trailing_user 顺序正确**：`assemble_messages` 单测覆盖完整顺序 `[system?] + extra_context? + history + [new_user_input?] + [trailing_user?] + [trailing_system?]`；`trailing_user` 在 `new_user_input` 之后、`trailing_system` 之前；rendered role 为 `user`；三者同时传不抛异常按顺序拼接。
- **AC-2 三个 context manager 透传**：naive / fifo / summarizing 每个 manager 各一个单测覆盖 `trailing_user` 透传到最终 messages 列表；既有 `trailing_system` 单测不退化。
- **AC-3 dispatch_system_turn 切换正确**：bedtime（`output_visibility="user"`） + silent turn（`output_visibility="memory_only"`）两条路径都用 `trailing_user`；mock LLM 验证最后一条 message role 是 `user`、不是 `system`；session.events 仍只落 `system_trigger` + assistant_message（user）/ `memory_observation`（silent），不出现"trailing_user 消息被落盘"。
- **AC-4 缓解措施落地**：`DEFAULT_BEDTIME_ADDENDUM` / `DEFAULT_IDLE_ADDENDUM` 文案含 `<system_trigger>...</system_trigger>` tag；agent 顶层 system prompt 含 tag 识别说明；单测断言 sources.py 常量 + system prompt 包含对应字符串。
- **AC-5 真跑验证（DeepSeek）**：用 `docs/issues/006-bedtime-prompt-history-hijack/replay.py` 复用同份 session jsonl（line 1-56 截断，末尾 assistant 含两个问句"是日本小女孩 meme... 真人小孩？"），跑两条路径：
  - **bedtime 路径**（v4-flash + v4-pro 各 2-3 次取多数）：LLM **不**续写"日本小女孩 meme"上文，而是按 BedtimeSource 语义主动开口（如"该睡了"语义；具体措辞自由）；允许 1/N 边缘 case 但多数行为成立。
  - **silent turn 路径**（v4-flash + v4-pro 各 2-3 次取多数）：LLM 输出仍记 `memory_observation`、不冒泡到用户；输出文本里不出现"既然你说该睡了 / 既然你提醒我"等回声词（验缓解措施有效）。
- **AC-6 全量回归 + 门禁全绿**：`./scripts/check` 全绿（既有 332 测试 + 本期新增测试，零退化）；`agent` / `agent_bridge` 全套件本期前后 diff 仅新增项、无既有项变红。
- **AC-7 文档闭环**：014 design.md / progress.md 补段已加；issue 006 状态改 resolved、commit 指针就位；本需求 progress.md 总体状态改 COMPLETED。

---

## 7. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-17
- **确认时间**：2026-06-17
- **关联 issue**：[`docs/issues/006-bedtime-prompt-history-hijack/`](../../issues/006-bedtime-prompt-history-hijack/)（本需求落地后闭环）
- **承接需求**：[`014 引擎主循环与桥推送通道`](../014-engine-main-loop-and-bridge-push/)（架构调整，014 design §6 当年路径事后被真 LLM 证伪）
- **业界参考**：Claude Code `<system-reminder>` 模式 + 配套 system prompt 训练（同款缓解模板）
- **下一步**：本文档确认后撰写同目录 [`design.md`](./design.md)（技术方案）
