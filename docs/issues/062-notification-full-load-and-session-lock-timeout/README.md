# 062 · 通知完整读取绕过查询优化，长会话仍发生写锁超时

- **状态**：open
- **严重度**：high
- **发现日期**：2026-09-07
- **关联需求**：[118-long-session-query-and-cancellation](../../requirements/118-long-session-query-and-cancellation/)

## 现象

长会话的新一轮已接收并执行，但保存工具结果时抛出 `SessionPersistError`，运行以 `failed / executor_error` 结束。用户要求先登记，暂不修复。

本次运行 `483d6048-aa71-462a-a185-619383d22073`，会话 `3f2685e3-b594-5b6e-8176-21e436904e1c`。以下时间均为北京时间：

- 01:11:06 开始运行，01:11:40 用户消息已写入会话。
- 01:13:33.484，写入未取得文件锁，实际等待 10094.692 ms。
- 01:13:46.503，再次写入未取得锁，实际等待 12076.125 ms。
- 同期同一进程 `24392` 的多个线程完整读取同一会话；文件约 365 MB，线程 `45436`、`27584` 分别报告持锁 15977.427 ms、19168.122 ms。
- 01:13:49 报错栈经过 `_persist_tool_call_results → _append_tool_call_result_event → rollout.commit → append_events`；01:14:18 运行终态为失败。

运行身份为 packaged / production、产品版本 `0.3.0`、build_ref `099d29925e52b42b`、source_commit `6b23515e99d81b7241fc48e9c59a9a4b7206b9b4`。该提交正是需求 118 的合入，不能归因为未更新安装包。调查工作树 HEAD `8f294358953802af203dd4fd025c7bda0fc2287e`，相关存储与通知文件相对运行提交无差异。

证据来自本机 `%LOCALAPPDATA%/agent-friend/Logs/agent_bridge.log`、其 `.1` 轮转文件及 `runtime-bridge.log`，用户消息来自 `%APPDATA%/agent-friend/sessions/<session_id>.jsonl`。日志会轮转；以上摘录保留必要标识和数值，不复制整份私有会话。该运行发生在用户最初截图之后，不能据此认定截图中输入框文字回退的原因。

## 根因初判

**confirmed / runtime-supported**：本次执行被会话文件写锁超时中断，失败边界是工具结果到会话持久化；不是模型拒绝执行。读持锁日志和写超时发生于同一进程、同一会话。

**confirmed / code-supported**：存在绕过需求 118 查询优化的通知读取路径：

- `frontend/src/stores/notifications.ts`：通知订阅按 `RETRY_MS=5000` 定时刷新快照；前端有单窗口进行中请求合并。
- `agent_bridge/src/agent_bridge/notifications/service.py`：`snapshot → _revalidate → _valid` 为检查会话存在性调用 `self.sessions.load(fact.session_id)`；符合校验条件的多条通知没有按会话合并读取。
- `notifications/generation.py` 的生成有效性检查也可经 `_generation_valid → _valid` 触发相同读取；它不是固定每 250 ms 独立发起读取的定时器。
- `agent/src/agent/sessions/store.py`：`load()` 直接进入 `_read_records`，不经过 `query()` 的缓存与构建合并。虽然 JSON 解析已移至锁外，完整字节读取、拼接和中间块释放仍在共享读锁范围内。

**unconfirmed / observability-gap**：通知路径是明确的重复读取来源，但现有 file observation 只有线程号，没有请求标识或调用方栈；不能把上述两个慢读取线程唯一归属到通知请求，也不能确认通知是本次锁竞争的唯一来源。持锁从通常数百毫秒膨胀至十几秒的具体原因仍待区分：并发解析与调度、内存压力或 I/O 等均未获得决定性证据。日志记录时间在解析结束后，不能直接用输出时间减 hold_ms 推算持锁区间。

同期通知快照与通知 worker 还出现 `sqlite3.OperationalError: database is locked`；这是通知数据库的独立错误证据，不与 JSONL 文件锁混为同一把锁或已确认因果链。

## 影响与判断

影响长会话核心执行可靠性：工具可能已执行但结果无法完整持久化，后续重试需要先核实实际状态。通知校验在会话越大时开销越高，可能与执行写入竞争。

需求 118 已有 320 MiB 合成查询、缓存收益验证，但不能据此证明真实通知轮询与工具写入并发安全；本案例暴露了相邻调用链与组合验证缺口。不修改原需求验收历史，不将此前通过的查询验证扩大解释为全链路无锁竞争。

## 可选修复方向

- 对仅判断存在性的通知校验评估使用轻量 `exists()`，保留会话删除与通知失效语义；确需消息内容的路径评估查询投影。
- 核对通知快照、生成校验及其他直接 `load()` 调用方，避免同会话重复全量读取。
- 以只读调用方标识或线程采样补齐实际慢读取归属，区分持锁读取与锁外解析的耗时来源。
- 增加大体积会话下通知刷新、多个待校验通知与工具结果写入并发的确定性验证；不要仅调大写锁超时。

以上为后续候选，不是已批准的实现方案。本次未改产品代码、未重放真实模型，也未关闭问题。
