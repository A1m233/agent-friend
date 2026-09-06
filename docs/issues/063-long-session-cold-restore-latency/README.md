# 063 · 长会话冷恢复重复处理完整历史，导致首轮准备耗时过长

- **状态**：open
- **严重度**：high
- **发现日期**：2026-09-07
- **关联需求**：[113-session-rollout-persistence-and-recovery](../../requirements/113-session-rollout-persistence-and-recovery/requirement.md)、[118-long-session-query-and-cancellation](../../requirements/118-long-session-query-and-cancellation/requirement.md)

## 现象

Windows 已安装桌面端的一条长会话发送消息后，运行超过一分钟仍无输出。现场日志显示（UTC+08:00）：

| 时间 | 阶段 | 耗时 |
| --- | --- | --- |
| 00:57:51.920 | scheduler_start | 排队 2 ms |
| 00:57:51.922 → 00:58:52.227 | conversation_prepare | 60,305 ms |
| 00:58:56 左右 → 00:59:20.254 | memory_retrieve | 23,940 ms |
| 00:59:21.826 | LiteLLM completion 入口 | 距运行开始约 90 秒 |

最后一项只表示进入调用封装，不等于 HTTP 请求已发出或模型已开始生成。本 issue 聚焦前面的本地会话准备；记忆检索和后续模型调用延迟不计入其根因结论。

本轮开始前，会话 JSONL 为 356,638,959 字节。准备阶段同一 worker 两次完整加载该文件，读取与解析合计约 8.25 秒；锁等待分别仅 0.259 ms、0.291 ms，不支持把这两次加载解释为长时间等待文件锁。

## 根因初判

主要瓶颈是冷恢复逐提交点重复处理累计完整 transcript，而非只处理增量：

- [AG-UI 准备入口](../../../agent_bridge/src/agent_bridge/protocols/ag_ui/routes.py) 先打开会话，再绑定持久会话。
- [SessionManager](../../../agent/src/agent/sessions/manager.py) 在会话无驻留 runtime state 时进入 `_restore_runtime_state`；事务恢复再次通过 [recovery.py](../../../agent/src/agent/sessions/recovery.py) 加载会话，之后执行 `restore_rollout`。
- [restore_rollout](../../../agent/src/agent/sessions/rollout.py) 从头回放。每个 `rollout_commit` 都对截至该点的完整 transcript 重新序列化并计算哈希，再深拷贝累计消息。append 链不断增长时，这部分工作随各历史前缀总量增长，而不只是随最终历史大小增长。

### 只读验证与版本绑定

安装包来源 commit 为 `6b23515e99d81b7241fc48e9c59a9a4b7206b9b4`，标记 dirty；仅凭 commit 不能证明整个安装包等同于仓库。已从实际安装包提取 Python 字节码，对照检查源码，确认 `_prepare`、`start_conversation`、`_restore_runtime_state`、`_reconcile_session_once`、`restore_rollout` 及所检查的消息、witness 反序列化函数一致。绑定仅覆盖这些函数。

以现场开始前的 356,638,959 字节文件前缀作为固定输入，边界为完整 JSONL 行，在独立进程中调用实际 `restore_rollout`，不运行模型、不写用户数据：

| 检查 | 结果 |
| --- | --- |
| 固定输入解析 | 3.767 秒 |
| 历史提交点 | 643 个：615 append、28 rewrite |
| 完整恢复 | 29.422 秒，disposition=exact |
| 完整 transcript 哈希 | 643 次，累计 11.069 秒 |
| 消息级哈希 | 37,579 次，累计 1.609 秒 |
| 实验中仅将 rollout 模块的 deepcopy 替为原值返回 | 恢复降至 13.261 秒；最终 commit 状态和 disposition 相同 |

该对照确认恢复层深拷贝造成显著耗时，但不证明直接移除拷贝可作为生产修复：别名、后续修改与不可变性契约尚未验证。另一次使用较晚会话快照的完整恢复测得 36.057 秒，其中哈希 13.779 秒、外层深拷贝 20.444 秒；不同快照与负载下的数字不能相加为现场时间。

固定输入中，`rollout_commit` 占 184.72 MB，`model_transcript_item` 占 137.20 MB，`rollout_request_prepared` 占 25.23 MB，内部恢复记录共约 347 MB。提交点重复保存的 active event 标识、epoch manifest 和 final-wire witness 是文件体积的主要贡献项，进一步放大读入与解析成本。

**结论边界**：重复全量校验、深拷贝及其成本已由代码、安装包函数绑定与对照实验确认（code-supported、reproduction-supported）；现场准备阶段 60.305 秒由日志确认（runtime-supported）。它们是本次主要瓶颈的解释得到 strongly supported，但现场缺少内部逐阶段计时，尚不能把全部 60.305 秒精确归账；不得将剩余耗时直接归给 CPU 竞争、文件锁或某个未测阶段。

## 影响与判断

影响长会话在进程重启后、内存运行状态尚未驻留时的首次准备。runtime state 驻留后不会每轮执行整套恢复，但入口文件加载仍需单独考虑。问题归属会话持久化与恢复编排，延迟发生在模型调用之前。

影响核心对话首响应体验。用户本次明确选择先登记、后续修复；当前未修改产品行为，未判定具体需求 AC 违反情况。

## 可选修复方向

- 在保留提交链和损坏恢复校验的前提下，减少各中间提交点的累计消息深拷贝，并评估完整 transcript 重复哈希的替代方案。
- 复用已加载会话，明确一致性与锁语义，消除冷恢复中的二次全量加载。
- 评估内部恢复记录中的重复快照体积，保持已有会话读取与迁移兼容性。
- 为冷准备增加阶段耗时证据，并用固定长会话回放验证正确性与性能；无须调用真实 LLM。
