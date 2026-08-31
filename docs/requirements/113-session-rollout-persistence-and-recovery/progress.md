# 113 · 会话流水持久化与重启恢复 - 进度

## 总体状态

COMPLETED

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

## AC 验证

- [x] AC-1 Bridge 重启 exact continuation
- [x] AC-2 应用完整退出重开
- [x] AC-3 无工具最终回答
- [x] AC-4 工具事务恢复
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
- [x] AC-19 自动化门禁

## 验证证据

### 确定性验证

- `agent/tests/test_session_rollout.py`：22 个用例覆盖旧会话同步迁移、迁移写失败释放 lease、重复重启保持 migration epoch、禁止业务历史恢复 fallback、exact restart、Memory/model-only 原位、tool 顺序与不重放、accepted-but-unsent user、坏尾、writer lease、credential 字段过滤、archive/delete、unknown schema、identity matrix、模型切换隔离、compaction rewrite 和 witness round-trip。
- `agent/tests/test_dynamic_context_cache_continuity.py`：重启后 provider input 对重启前 final wire 做严格前缀比较。
- `agent/tests/test_prompt_cache_trace.py` 与 `llm_providers/tests/test_prompt_cache_observation.py`：覆盖跨进程 exact witness，以及 `strict_extension + cache_read=0` 与客户端 rebase 的独立解释。
- 2026-09-01 旧会话迁移修正后的最终门禁：backend 2397 passed / 4 skipped；frontend 115 files、683 tests、production build 通过；Rust 224 tests 通过、1 个既有 ignored，release check 通过。frontend CSS pseudo-element/chunk、Rust dead-code 与 Python bilibili atexit 输出均为既有 warning，未导致门禁失败。

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
