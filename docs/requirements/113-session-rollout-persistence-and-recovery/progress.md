# 113 · 会话流水持久化与重启恢复 - 进度

## 总体状态

COMPLETED（late recovered result 幂等归位、Kimi 取消时限门禁修复、三阶段项目门禁与真实旧会话 dev E2E 均已完成并经用户验收）

## 任务列表

- [x] 增加 rollout 事件、Message/context/epoch/witness 序列化与 reader/recorder
- [x] 增加 session JSONL 批量 fsync append、坏尾恢复和 session writer lease
- [x] 在 SessionManager 中接入跨进程恢复、legacy/degraded 语义和 runtime diagnostics
- [x] 在 Conversation 的 provider、assistant、tool 和 compaction 边界提交 rollout
- [x] 接入 final-wire exact witness、cache epoch 兼容判断和显式模型切换隔离
- [x] 覆盖 archive/hard delete、批量删除和 credential 字段隔离
- [x] 补齐确定性单元/集成测试并更新旧 restart 预期
- [x] 运行定向测试与 `scripts/check`
- [x] 完成需求 109 真实长会话 smoke
- [x] 旧会话首次建立可写 runtime 时同步提交 rollout migration checkpoint
- [x] 迁移后重复重启仅从 rollout 恢复同一基线与 epoch
- [x] 重跑受影响测试与项目门禁
- [x] 让业务事件投影按 `(run_id, event_tool_call_id)` 归位 late recovered result，并去除重复/孤立 result
- [x] 让 canonical `SessionModelHistory` 对同一 result UUID 执行 move 而不是重复 insert
- [x] 覆盖真实两类旧会话形态、edit/resend、legacy migration 与 history reconcile 回归
- [x] 重跑受影响测试与 `scripts/check`
- [x] 取消或异常退出 tool batch 时，为所有已进入模型历史的 call 幂等补齐 interrupted/unknown result
- [x] 持久化 model-visible turn-aborted fact，并在下一轮 provider 前执行 call/result 完整性门禁
- [x] 覆盖同进程取消后续聊、Bridge 重启后续聊、无重复执行与本地 fail-closed
- [x] 重跑受影响测试与 `scripts/check`
- [x] 确认命令部分执行证据的需求边界与技术方案
- [x] 实现 session-owned 有界 evidence snapshot 与原子持久化
- [x] 在命令取消、终态和 Bridge 重启路径恢复同一 interrupted result
- [x] 扩展 `recover_tool_result` 的当前会话 evidence 引用恢复
- [x] 补齐取消竞态、输出截断、大输出引用、损坏降级和无重放测试
- [x] 重跑受影响测试与项目门禁

## AC 验证

- [x] AC-1 Bridge 重启 exact continuation
- [x] AC-2 应用完整退出重开
- [x] AC-3 无工具最终回答
- [x] AC-4 工具事务恢复（部分执行证据修订后重新验证）
- [x] AC-5 Model-only item 保真
- [x] AC-6 Memory 追加语义
- [x] AC-7 损坏尾部降级
- [x] AC-8 Cache identity 不兼容
- [x] AC-9 全局默认模型隔离
- [x] AC-10 显式会话模型切换
- [x] AC-11 并发单写者
- [x] AC-12 会话生命周期
- [x] AC-13 凭据隔离
- [x] AC-14 运行时权威不变
- [x] AC-15 Schema 与旧会话迁移
- [x] AC-16 Compaction checkpoint
- [x] AC-17 需求 109 长会话 smoke
- [x] AC-18 客户端与 provider 结论分离
- [x] AC-19 自动化门禁（部分执行证据修订后重新验证）
- [x] AC-20 取消后继续会话（证据投影后重新验证）
- [x] AC-21 部分工具输出持久化

### 本次 fix 回归

- [x] AC-4 工具事务恢复：late recovered result 只出现一次且紧邻原 call
- [x] AC-15 旧会话迁移：业务事件投影在迁移前已满足 provider tool pairing
- [x] AC-19 自动化门禁：专项回归与三阶段 `scripts/check` 全绿

## 验证证据

原需求交付证据保留如下；本次修订的新增证据将在实现和复验完成后追加，不覆盖既有验证历史。

### 确定性验证

- 2026-09-02 late-result fix 新增 6 个确定性用例：覆盖单 call 延迟结果归位、同事务重复结果去重、同一轮多 call 按请求顺序归位、跨 run 复用 provider call id、canonical history 同 UUID 幂等 move，以及 edit/resend 前缀重建；专项 35 passed，关联 session/recovery/runtime/tool-calling 组合 147 passed。
- 对本机 71 个既有 session 做只读业务投影扫描，全部通过 provider tool-history 校验；当前长会话的 edit/resend 前缀重建得到 1,858 条合法 model messages。扫描只读取 session，未改写原始 JSONL。
- 本次未调用真实 LLM/API：修复对象是本地确定性的 event pairing、投影顺序、UUID 幂等性和 edit/resend 前缀构造，fake provider 与真实旧 session 足以直接验证协议结构。
- 2026-09-02 门禁中的 Kimi 取消时限用例最初两次全量分别以 1.61s、1.78s 失败；缩减到前一条 direct-transport 测试 + 该用例即可稳定复现 1.66s。根因是测试从 `client.stream()` 调用前计时，把本地 `httpx.AsyncClient/OpenAI` 构造时间误计入 response-header wait 的取消预算。计时锚点改为 fake 请求实际开始后，相邻复现 2 passed、整个 Kimi 文件 57 passed；取消异常、async task `CancelledError` 和 client close 断言均保留。
- 2026-09-02 最终三阶段门禁：backend lint 与 605 个 source files typecheck 通过，pytest 2,421 passed / 4 skipped；frontend 115 files / 688 tests 与 production build 通过；Rust 224 tests、1 个既有设备 smoke ignored 与 release check 通过。zip duplicate、frontend CSS `::highlight`/chunk、Rust dead-code 与 Python bilibili atexit 均为既有 warning，未导致门禁失败。
- 部分执行证据修订新增 11 个确定性用例：覆盖原子快照替换、内容 hash/事务归属校验、session 配额、有界预览与丢弃量、命令运行时输出捕获、同进程取消续聊、正式结果与证据清理竞态、Bridge 重启恢复、坏证据降级、当前会话 `evidence_ref` 恢复和 hard delete。
- 定向组合验证：156 passed，覆盖 command runtime、conversation/runtime dispatch、startup reconciliation、result recovery、session lifecycle 及 evidence store。
- 本次修订全量门禁（2026-09-01）：backend lint/typecheck 通过，pytest 2415 passed / 4 skipped；frontend 115 files、685 tests 与 production build 通过；Rust 224 tests 通过、1 个设备级 smoke 按约定 ignored，release check 通过。zip duplicate、frontend CSS `::highlight`/chunk、Rust dead-code 与 Python bilibili atexit 均为既有 warning，未导致门禁失败。
- 本次没有调用真实 LLM/API：验收对象是本地命令输出 checkpoint、事务闭合、持久化顺序、跨进程恢复和引用授权，fake provider/runtime 可完整断言；真实 provider 不会增加这些确定性边界的证据。
- 本次修订新增 7 个确定性用例：覆盖单工具与多工具 batch 取消、结果投影中途取消、同进程直接续聊、Bridge 重启后续聊、terminal run 启动恢复、已有 host-recovered result 回灌、不可修复历史本地 fail-closed；全部使用 fake provider 与受控阻塞工具，证明原工具不重复执行且不会向 provider 发送孤立 call。
- 本次修订项目门禁（2026-09-01）：backend lint/typecheck 通过，pytest 2404 passed / 4 skipped；frontend 115 files、685 tests 与 production build 通过；Rust 224 tests 通过、1 个设备级 smoke 按约定 ignored，release check 通过。zip duplicate、frontend CSS `::highlight`/chunk、Rust dead-code 与 Python bilibili atexit 均为既有 warning，未导致门禁失败。
- 本次未调用真实 LLM/API：验收对象是确定性的本地协议配对、持久化顺序、取消竞态与重启恢复，fake provider 能完整断言发出前 messages；真实 provider 只会重复验证同一结构约束并产生无必要 token 消耗。
- `agent/tests/test_session_rollout.py`：23 个用例覆盖旧会话同步迁移、迁移写失败释放 lease、重复重启保持 migration epoch、禁止业务历史恢复 fallback、exact restart、Memory/model-only 原位、tool 顺序与不重放、取消结果与 turn-aborted fact 跨进程恢复、accepted-but-unsent user、坏尾、writer lease、credential 字段过滤、archive/delete、unknown schema、identity matrix、模型切换隔离、compaction rewrite 和 witness round-trip。
- `agent/tests/test_dynamic_context_cache_continuity.py`：重启后 provider input 对重启前 final wire 做严格前缀比较。
- `agent/tests/test_prompt_cache_trace.py` 与 `llm_providers/tests/test_prompt_cache_observation.py`：覆盖跨进程 exact witness，以及 `strict_extension + cache_read=0` 与客户端 rebase 的独立解释。
- 2026-09-01 同步 `origin/main@79b7e59` 后的最终门禁：backend 2397 passed / 4 skipped；frontend 115 files、685 tests、production build 通过；Rust 224 tests 通过、1 个既有 ignored，release check 通过。frontend CSS pseudo-element/chunk、Rust dead-code 与 Python bilibili atexit 输出均为既有 warning，未导致门禁失败。

### 真实旧会话 dev E2E（2026-09-02）

- 运行身份：Tauri dev lease 与 desktop owner 均绑定 `fix/tool-history-repair-idempotency` 当前 worktree，Bridge PID 24304；启动时间晚于本次源码变更，`AGENT_FRIEND_DATA_DIR` 指向独立临时根。
- case：从正式数据只读复制 `b259be2f-6cd7-48c8-8432-2a331732a0fd`，源与副本初始 63,456 bytes 且 SHA-256 一致。原始 active 流水包含旧 run 的 late recovered result 落在后续 user/`turn_rewrite` 之后，直接命中本次修复边界。
- 真实桌面交互：通过 WebView2 CDP 打开该会话，点击最后一条用户消息的“编辑并重发”，替换为固定无工具请求；`POST /edit-resend-latest` 返回 HTTP 200 SSE，真实 `kimi/kimi-k2.7-code-highspeed` 唯一回复“历史恢复验证通过”，UI 完成态可见。
- provider / runtime：共 1 次真实 LLM 请求，26,443 prompt tokens + 61 completion tokens，`tool_calls=0`；run `853c2d54-5cc4-4e41-9189-1b07b46ae7e3` 在 10,222ms 后以 `completed/end_turn` 终结。
- 持久化交叉核对：副本新增第二条 `turn_rewrite`、assistant、rollout commit 与 completed `run_terminal`；最终 business history 与 edit-prefix 各 14 条 model messages，provider tool-history 校验通过且 UUID 唯一。正式源文件仍为 63,456 bytes、mtime `2026-09-02 01:33:55`，未被写入；dev runtime 已停止且端口全部释放。

### 真实长会话 smoke

- 历史 provenance：`HEAD=d60537f` + 当前未提交 feature overlay；模型 `deepseek/deepseek-v4-flash`，显式切换目标 `kimi/kimi-k2.6`；本机隔离临时副本；安全源引用 `24f9616a08f5`（83,633 字符、80 条 model message、13 个完整 turn、50 个 tool event、0 附件）。
- 原始内容无关报告：[artifacts/rollout-recovery-smoke-2026-09-01.json](./artifacts/rollout-recovery-smoke-2026-09-01.json)，SHA-256 `6A3D1CF529F1B1EBF540FEC4777A69A4877C44A9CFDE3AE4ED95AE03B12710C2`。
- 首次 pilot 使用了错误编码的 safe ref，在任何 LLM 请求前失败；没有 token 消耗或 artifact。正式运行共 5 次真实请求，总输入 165,803 token。

| 场景 | 客户端 final wire | recovery / epoch | provider cache_read / input_total |
|---|---|---|---:|
| baseline | unavailable | `legacy_bootstrap`（迁移语义修正前）/ `8657021f-d525-4e51-942e-406065ced6ee` | 4,608 / 33,436 |
| 无重启连续会话 | `strict_extension` | 同一 legacy bootstrap epoch | 33,408 / 33,532 |
| Bridge 重启恢复 | `strict_extension` | `exact`，同一 epoch | 33,536 / 33,655 |
| 应用重启恢复 | `strict_extension` | `exact`，同一 epoch | 33,792 / 33,831 |
| 显式模型切换 | `rebase`，first divergence=`model-route` | 新 epoch `c1ca4d28-69c6-45e8-95f5-43914fceb2c2`，reason=`model_route_changed` | unavailable / 31,349 |

解释边界：三段 continuation 的 provider cache hit 与客户端 strict-extension 同时出现，但二者仍是独立结论。baseline 使用克隆 session 的原 cache key，4,608 个 cache-read token 可能受 provider 既存缓存影响，不能作为本需求的因果证据；本需求的恢复结论来自 final-wire witness 的 strict-extension。该 smoke 完成后，旧会话 baseline 从内存 `legacy_bootstrap` 修正为落盘 `legacy_migrated`；迁移不改变后续 provider wire，新增语义由 22 个确定性 rollout 用例覆盖，本次未重复产生真实 LLM 调用。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-31 | requirement 与 design 已确认，进入实现 | 基于 `origin/main@d60537f` 的 `feature/113-session-rollout-persistence-and-recovery` |
| 2026-09-01 | 完成 rollout reader/recorder、fsync commit、writer lease、runtime 恢复与全部确定性覆盖 | 定向测试和三阶段项目门禁通过 |
| 2026-09-01 | 完成 5-call 真实长会话 smoke | Bridge/app restart 均 strict-extension + exact；模型切换按预期 rebase/new epoch |
| 2026-09-01 | 用户澄清旧会话迁移边界，回溯 requirement/design | 旧会话必须先落成 rollout checkpoint；原 `legacy_bootstrap` 内存 fallback 不满足唯一持久化事实源 |
| 2026-09-01 | 完成旧会话同步 migration checkpoint 与产品 active-event 投影隔离 | 迁移失败不建立 runtime；重复重启保持同一 degraded epoch；业务-only suffix 不进入恢复 history；完整三阶段门禁通过 |
| 2026-09-01 | 用户确认验收并授权一路合入主干、推送远端 | AC、三阶段门禁与真实长会话 smoke 证据齐备，工作流状态更新为 `COMPLETED` |
| 2026-09-01 | 变基到 `origin/main@79b7e59` 并完成最终复验 | backend 2397 / frontend 685 / Rust 224 全部通过；公开 Guide 已更新并发布 immutable `agent-friend-guide-r26` |
| 2026-09-01 | 真实取消 bad case 证明 AC-4 未覆盖 terminal run 中的孤立 tool call；用户确认在需求 113 内重开修订，不创建新需求 | 基于最新 `origin/main` 创建 `fix/tool-transaction-cancellation-integrity`；进入实现前先补齐需求、方案与回归边界 |
| 2026-09-01 | 完成取消终结、turn-aborted model fact、启动恢复与 provider 前完整性门禁 | 新增 7 个回归用例；backend 2404、frontend 685、Rust 224 及 production/release build 全部通过，待用户验收后收口 |
| 2026-09-01 | 用户确认命令部分执行证据的需求与技术方案 | 采用 session-owned 原子快照、有界预览 + `evidence_ref`、同进程/跨 Bridge 共用 renderer；尚未开始生产代码实现 |
| 2026-09-01 | 完成命令部分执行证据持久化、取消/重启恢复、大输出引用与生命周期清理 | 新增 11 个确定性用例；backend 2415、frontend 685、Rust 224 及 production/release build 全部通过，待用户验收后收口 |
| 2026-09-01 | 用户确认验收并要求合入主干、推送远端 | AC-21 与关联回归、三阶段门禁证据齐备，工作流状态更新为 `COMPLETED` |
| 2026-09-02 | 两个真实旧会话暴露 late recovered result 仍停留在事件尾部；edit/resend 会从业务事件重建并绕过已修复 rollout | 基于 `origin/main@da65188` 创建 `fix/tool-history-repair-idempotency`；用户确认继续实现，按需求 113 既有事务完整性方案补幂等归位与回归 |
| 2026-09-02 | 完成业务投影归位、重复/孤立结果清理、canonical history UUID 幂等 move 与 edit/resend 回归 | 专项、真实旧 session 扫描、frontend、Rust 均通过；backend 全量被本次 diff 外的 Kimi 取消时限顺序依赖阻塞，待决定是否另行修复该门禁问题 |
| 2026-09-02 | 用户授权修复 Kimi 门禁顺序依赖；完成测试计时锚点纠正与完整复验 | Kimi 相邻缩减 2 passed、文件级 57 passed；backend 2,421 / frontend 688 / Rust 224 全部通过，进入用户验收 |
| 2026-09-02 | 用户要求用 dev 跑真实 case；完成隔离旧会话的桌面 edit/resend E2E | 真实 Kimi 1 call、HTTP 200 SSE、UI 固定回复、run completed、落盘与 provider pairing 均通过；正式数据未变化，dev 已停止 |
| 2026-09-02 | 用户确认验收并要求合入 `main`、推送远端及构建本地测试包 | 本次需求 113 follow-up 状态更新为 `COMPLETED`，进入 Guide 复核、Git 合入与 package-release 流程 |
