# 113 · 会话流水持久化与重启恢复 - 技术方案

## 状态

CONFIRMED

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

### 当前权威与持久化边界

- `SessionModelHistory` 保存当前 Bridge 进程内真正采用的规范化 `Message` 序列，是运行时 model transcript 权威。
- `JsonlSessionStore` 已用一个 session JSONL 保存产品会话事件，包括用户与 assistant 消息、tool call/result、reasoning state、compaction、模型和 persona 变更等。
- 日期、surface、权限、runtime capability、Memory recall 和一部分 turn context 只存在于进程内；Skill 事件也不足以恢复所有当时实际采用的模型正文和位置。
- `SessionCacheEpoch`、`SessionModelContext` 和精确 final-wire witness 都是进程内状态。新进程只能从业务消息建立 `process_resume_degraded` baseline。
- 当前工具恢复器不会重新执行原工具：它只探测、重新连接、补交已有结果或写入 interrupted/unknown 结果。需求实现必须保留该边界。

### 当前请求提交顺序

一次普通用户轮当前按以下顺序推进：

1. 持久化用户业务事件及 Skill 选择事件；
2. 召回 Memory、生成动态 model context，并组装候选 model history；
3. 发起 provider stream；
4. provider 返回首个有效事件后，将候选 history、model context 与 cache epoch 提交到进程内状态；
5. 完成 assistant 或 tool call 后写业务事件并追加到 `SessionModelHistory`；
6. 工具执行前写 execution checkpoint，完成后写 tool result 并追加到 model history。

新增 rollout 必须贴合以上边界，不能让“已组装”误等于“provider 已接受”，也不能在恢复时把尚未完成的 turn 当作终态。

## 方案设计

### 总体结构

继续使用现有 session JSONL 作为同一份本地会话流水文件，在其中增加 rollout 专用事件。产品事件与 rollout 事件处于相同安全、删除和保留边界，但形成两个职责明确的投影：

- `Session` 从产品事件投影 UI、业务历史、Memory source 与会话 metadata；
- rollout reader 从已完整提交的 rollout 事件重建 `SessionModelHistory`、`SessionModelContext`、`SessionCacheEpoch` 和 final-wire witness；
- 仅当旧会话完全没有 rollout commit 时，migration builder 才能读取一次 `business_llm_messages`，并在 writer lease 内同步提交 `legacy_migration` checkpoint；提交后该业务投影不再参与跨进程 model history 恢复；
- 恢复完成后，后续 provider input 仍由现有 `SessionModelHistory → context manager → provider renderer` 链路生成。

rollout 不保存完整 provider request body。final-wire witness 只保存不可逆摘要、item role/domain 和 envelope identity，用于下一次请求比较。

```mermaid
flowchart LR
    E[现有 session JSONL] --> B[产品事件投影]
    E --> R[rollout reader]
    B --> S[Session]
    R --> H[SessionModelHistory]
    R --> C[SessionModelContext]
    R --> P[SessionCacheEpoch]
    R --> W[final-wire witness]
    H --> A[现有 context assembly]
    C --> A
    P --> A
    A --> F[provider final wire]
    W -. strict-extension 验证 .-> F
```

### 身份定义

| 身份 | 语义 | 生命周期 |
|---|---|---|
| `session_id` | 用户可见产品会话的稳定身份，也是会话文件和 provider cache key 的现有绑定 | 创建会话时生成，hard delete 时消失 |
| `rollout_id` | 该 session 内持久化 model transcript 流水的稳定身份 | 首次建立 rollout 时生成；损坏降级和 compaction 不改变 |
| `segment_id` | 一段可由前一完整 commit 确定性应用的 rollout 写入段 | transcript rewrite、尾部损坏续写或 legacy migration 时新建 |
| `cache_epoch_id` | cache identity 兼容、允许比较 strict-extension 的世代身份 | model/provider/persona/system/tools/settings/compaction 等不兼容变化时更新 |

`cache_epoch_id` 与数值 `generation` 同时保留：ID 用于跨进程稳定关联，generation 用于排序和现有诊断兼容。

### Rollout 事件

在现有 `Event` schema 中增加以下纯加性类型：

| 事件 | 作用 | 是否能单独成为恢复点 |
|---|---|---|
| `rollout_segment` | 声明 `rollout_id`、`segment_id`、父 commit、schema 版本和开段原因 | 否 |
| `model_transcript_item` | 保存一条规范化 `Message` 快照及 ordinal/source 关联 | 否 |
| `rollout_request_prepared` | provider 调用前的 write-ahead 标记，引用候选 transcript delta 与 cache manifest | 否 |
| `rollout_commit` | 原子声明一组 item 已成为可恢复 history，并保存 boundary、hash、context、epoch 与可用 witness | 是 |

每个 `model_transcript_item` 保存：

- `role`、`content`、`uuid`、UTC timestamp；
- 会影响 `Message.to_openai()` 或恢复语义的 metadata；
- attachment/reference 的规范化投影；
- `prompt_cache_domain`、model-only/synthetic 标记；
- 可确定时的 `source_event_uuids`；
- 所属 segment、ordinal 和内容摘要。

`rollout_commit` 不重复保存全部正文，只引用本次追加或 replacement 所需的 item 事件，并记录最终 item count 与 transcript hash。正常 append 只写 suffix；compaction、edit/resend 或其它 rewrite 新建 segment 并逐条写 replacement history。

### Epoch manifest

每个 `rollout_request_prepared` 与 `rollout_commit` 都携带版本化 epoch manifest：

- `cache_epoch_id`、generation、reason；
- model route snapshot：产品 model ID、provider ID、provider model、client/transport identity、API base；
- persona ID 与当时完整 system prompt snapshot；
-规范化 tool schema/capability snapshot；
- cache-relevant request settings；
- transcript schema/version；
- compaction checkpoint identity和 history generation；
-上述字段各自 fingerprint 及 manifest 总 fingerprint。

manifest 只从白名单字段构造。API Key、Authorization、Cookie、访问令牌、宿主权限凭据和客户端对象不会进入 manifest。静态内容恢复时使用保存的 canonical snapshot；当前资源只用于 fingerprint 兼容校验，不用于改写旧 transcript。

### 消息与状态序列化

- `Message` 使用明确字段序列化，不整体序列化任意运行时对象。
- metadata 做递归键过滤，凭据型字段直接拒绝或移除；正文作为当时实际 model transcript 保存，不做会破坏 exact continuation 的启发式改写。
- `SessionModelContext` 保存初始化 contributor 集合及 calendar、surface、channel、permission version、runtime capability 和 capability catalog 的最后快照，避免重启后无条件重复注入 snapshot。
- `SessionCacheEpoch` 增加稳定 epoch ID 和 manifest 恢复能力。
- final-wire witness 保存完整的不可逆 witness 字段，不保存 request body。恢复后 tracker 可以做 exact-item 比较，但 witness 仍不能生成 history。

### 提交时序

```mermaid
sequenceDiagram
    participant U as 产品事件
    participant R as Rollout recorder
    participant P as Provider
    participant H as SessionModelHistory
    participant T as Tool runtime

    U->>U: append user / skill events
    R->>R: append candidate item delta
    R->>R: fsync rollout_request_prepared
    R->>P: 发起 provider 请求
    P-->>H: 首个有效响应，commit candidate history
    R->>R: fsync rollout_commit(provider_input_accepted)
    P-->>U: 完成 assistant/tool call 业务事件
    H->>H: append canonical assistant
    R->>R: fsync rollout_commit(assistant_completed/tool_call_received)
    alt tool call
        U->>U: fsync tool_execution_checkpoint
        U->>T: 执行工具
        T-->>U: fsync tool_call_result
        H->>H: append tool result
        R->>R: fsync rollout_commit(tool_result_completed)
    end
```

具体规则：

- provider 调用前必须完成 `rollout_request_prepared` 的 flush+fsync。它只是 write-ahead 事实，不是可恢复 commit。
- provider 首个有效事件出现后，先按现有语义提交进程内 candidate，再写 `provider_input_accepted` commit。
- completed assistant 在业务事件与 `SessionModelHistory` 均成功后写 commit。无后续 tool call 的 commit 标记 `turn_terminal=true`。
- tool call 的 canonical assistant/tool call item 必须在工具执行前 commit；execution checkpoint 仍是“工具可能已产生副作用”的权威判断。
- tool result 加入 history 后写独立 commit。后续最终 assistant terminal commit 才使整个 turn 成为 exact-restorable 终态。
- partial assistant 不作为完整 assistant item commit；恢复时保留产品事件，但只恢复到最近完整 rollout commit，并进入 interrupted epoch。

### 恢复算法

1. 在 session 生命周期开始时取得 writer lease；失败则拒绝成为该 session 的可写 runtime。
2. 逐行读取 JSONL，验证 UTF-8、事件 schema、segment/commit 链、item ordinal、count 和 transcript hash。
3. 仅应用完整 `rollout_commit`；孤立 item、prepared request 和半截 commit 全部忽略。
4. 最新 commit 为 `turn_terminal`、cache manifest 可读、业务 model-relevant 事件没有未覆盖 suffix 且文件没有未处理损坏时，恢复为 exact candidate。
5. 最新 commit 是 provider/tool 中间边界或有未覆盖业务 suffix 时，恢复到最后安全 transcript 后进入 `interrupted_turn` 新 epoch；不自动重新提交用户请求或重新执行工具。
6. 无 rollout 的老会话先构造 `legacy_migration_pending` 候选：从现有 `business_llm_messages` 提取仍可恢复的规范化历史，并建立原因固定为 `legacy_migration` 的新 epoch。该候选还不是可运行的持久化事实。
7. `SessionManager` 在持有 writer lease 时立即写入完整 migration segment 与 terminal checkpoint；checkpoint 明确标记 `exact_eligible=false`、原因 `legacy_migration`。写入失败则释放 lease 并拒绝建立 runtime。
8. 成功迁移后 runtime disposition 为 `legacy_migrated`。重复重启直接从 migration checkpoint 重建同一 `SessionModelHistory` 和 epoch，不再次读取业务消息，也不再次推进 epoch；完成一轮新的 provider terminal commit 后，后续重启才可以成为 exact candidate。
9. 完整恢复后把 item 重建为新的 `SessionModelHistory`；后续 assembly 不读取 final wire 或业务事件重拼已恢复 transcript。
10. 已出现未知/损坏 rollout 且没有任何有效 commit 时不进入 legacy migration：原产品事件仍可只读投影，但 `SessionManager` 拒绝建立 writable runtime，防止业务历史变成隐式恢复 fallback。

恢复 disposition 使用稳定值：`fresh`、`exact`、`legacy_migration_pending`（仅内部迁移候选）、`legacy_migrated`、`interrupted`、`truncated_tail`、`incompatible_schema`、`corrupt_committed_history`、`writer_conflict`。

### 损坏与写入一致性

- `JsonlSessionStore` 新增批量 append：同一文件锁内写入多行，最后 flush+`fsync`；普通单事件 append 复用该路径。
- append 前检查文件是否以换行结束。若上次崩溃留下半行，先补换行，使坏尾成为独立记录，不覆盖原字节。
- reader 可以忽略最后完整 commit 之后的坏行。首次续写先追加带 `starts_after_corruption=true` 的新 segment，并建立 `truncated_tail` epoch。
- 已有恢复 segment 后，后续读取允许跨过原坏行，但只能从明确的新 segment 继续。
- committed chain 中间损坏、hash 不符、base commit 不连续或未知破坏性 schema 不允许跳过后宣称 exact；运行时降级到安全业务 baseline 或明确阻止写入。
- 日志只记录行号、字节数、hash、disposition 和原因，不复制损坏正文。

### 并发与 writer 所有权

- `SessionManager` 为每个 resident session 只创建一份 runtime state 和 recorder，多个窗口复用。
- `JsonlSessionStore` 为每个 session 提供独立 OS advisory writer lease 文件；lease 生命周期覆盖 resident runtime，而不是单次 append。
- 第二个 Bridge 可以只读打开会话，但在 `start_conversation` 获取 lease 时得到明确 `SessionWriterConflictError`。
- 现有同 session run ownership/串行化继续负责 provider 并发；rollout recorder 在单 writer 内保持同步追加，不引入第二套调度器。
- hard delete 前释放本进程 lease；archive 不释放或删除 rollout。

### 工具副作用恢复

工具执行恢复继续以现有业务事件为准：

- 没有 `tool_execution_checkpoint`：表示宿主尚未承诺开始执行，可以由正常当前 turn 逻辑处理，但重启恢复不自动提交旧 turn。
- 有 checkpoint、无 result：只允许 adapter probe、resident reattach、补写已知结果或 interrupted/unknown；禁止调用原工具。
- 有 result：恢复其 canonical tool item，不再执行。
- rollout 只持久化模型看到的 call/result 顺序，不取代 checkpoint 的副作用裁决职责。

### 删除、保留与安全边界

- rollout 与产品事件共处同一 session JSONL，所以 archive 天然保留，hard delete 与批量删除天然同时删除正文和 rollout。
- writer lease 文件不含正文；hard delete 会在安全释放后清理当前 session 的 lease 文件和现有 command workspace/metadata。
- 不新增自动过期策略；沿用 session 生命周期。
- 默认诊断只暴露 ID、generation、reason、fingerprint、item count、hash 和恢复 disposition。

### Prompt Cache 验证

- terminal commit 保存上一轮 final-wire witness 和 content-free cache diagnostics。
- 恢复时将 witness 注入 `PromptCacheTraceTracker` 的跨进程 exact anchor。
- 下一次 provider request 由 provider transport 产生新的 final-wire witness，并与恢复 witness 比较。
- `strict_extension` 只证明客户端 final wire 延续；provider `cache_read/input_total` 独立报告。
- cache manifest 不兼容时先推进 epoch，并禁止将偶然 cache hit 报告为 exact continuation。

## 涉及文件

| 文件路径 | 改动类型 | 说明 |
|---|---|---|
| `agent/src/agent/sessions/rollout.py` | 新增 | rollout schema、序列化、recorder、reader 与恢复结果 |
| `agent/src/agent/sessions/events.py` | 修改 | 增加 rollout 事件类型 |
| `agent/src/agent/sessions/store.py` | 修改 | 批量 fsync append、坏尾读取、writer lease 与冲突错误 |
| `agent/src/agent/sessions/session.py` | 修改 | 承载只读加载诊断，确保 rollout 事件不进入业务投影 |
| `agent/src/agent/sessions/model_history.py` | 修改 | 从持久化 item 恢复并忽略 rollout 自身事件 UUID |
| `agent/src/agent/sessions/model_context.py` | 修改 | snapshot 序列化与恢复 |
| `agent/src/agent/sessions/cache_epoch.py` | 修改 | 稳定 epoch ID、manifest 恢复和兼容判断 |
| `agent/src/agent/sessions/manager.py` | 修改 | writer lease、rollout restore、runtime state 共享和删除清理 |
| `agent/src/agent/conversation.py` | 修改 | prepared/accepted/assistant/tool/terminal 提交接点 |
| `agent/src/agent/prompt_cache_trace.py` | 修改 | 恢复精确 final-wire witness anchor |
| `agent/tests/test_session_rollout.py` | 新增 | 持久化、恢复、损坏、epoch、writer、删除与凭据隔离测试 |
| 既有 conversation/cache 测试 | 修改 | 将旧的 restart degraded 预期更新为 exact，并补回归覆盖 |

## 影响分析

### 上下游影响

- Bridge 和桌面应用无需新增独立存储路径；已有 session API 继续返回相同业务投影。
- 现有 session 文件增加内部事件，旧版本读取这些新事件会因未知类型失败，因此 schema 兼容边界由本需求后的 reader 负责；不支持用旧 Bridge 写入新 rollout 文件。
- 会话列表扫描必须忽略 rollout 事件，仍只按用户/assistant 业务事件计算更新时间。
- compaction、edit/resend、provider transform rebase 和显式模型/persona 切换会触发新的 segment 或 epoch，但不创建新 session。

### 风险点

- **正文重复**：model transcript item 与产品事件可能包含相同文本，但只有前者能无损恢复实际模型顺序；通过 suffix delta 与 source UUID 关联控制重复量。
- **落盘延迟**：provider 前增加 fsync。先保持同步硬门槛确保语义正确，验证后再决定是否需要平台级 group commit 优化。
- **旧版本回退**：新事件是旧 Bridge 不认识的类型，不提供 downgrade 写兼容；升级后如需回退只能使用升级前备份。
- **旧会话迁移边界**：历史上未持久化的 model-only 内容无法补造，因此 migration checkpoint 是显式 degraded/new-epoch 基线而非旧 provider 前缀的精确复制；其价值是从迁移点开始消除业务历史恢复 fallback。
- **任意正文中的秘密**：不能在不改变模型前缀的情况下对用户正文做启发式脱敏；本方案保证宿主 credential 不通过 manifest/meta/runtime 对象进入 rollout，并维持现有“不要把凭据注入模型正文”的上游边界。
- **多进程锁可靠性**：依赖项目已有 `portalocker` 的 OS advisory lock；网络文件系统的异常语义不作为本期承诺。
- **中断恢复**：不自动重放旧 turn，可能需要用户显式重试，但优先保证不重复 assistant 和有副作用工具。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|---|---|---|
| 2026-08-31 | 首次确认：同一 session JSONL 内追加 delta rollout；prepared/commit/terminal 边界；跨进程 exact witness；非破坏性坏尾续写 | 是 |
| 2026-09-01 | 旧会话改为 writer lease 内同步提交 migration checkpoint；提交后 rollout 成为 model transcript 唯一恢复来源，迁移基线保持 degraded 直到产生新的 terminal provider commit | 是 |
