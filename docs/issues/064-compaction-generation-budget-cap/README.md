# 064 · 摘要生成固定封顶 4096 token，未区分生成预算与摘要长度

- **状态**：resolved
- **严重度**：high
- **发现日期**：2026-09-07
- **关联需求**：[113-session-rollout-persistence-and-recovery](../../requirements/113-session-rollout-persistence-and-recovery/requirement.md)

## 现象

Windows 桌面端长会话在 2026-09-07 02:25:22 开始的一轮中显示“正在压缩”，随后该提示消失，没有显示“已压缩”。以下时间均为北京时间，证据来自本机 `agent_bridge.log` 与 `runtime-bridge.log`，不附带私有会话原文。

- 02:26:58.174，`operation=context_compaction`、`call_kind=summary_generation` 的调用报告 completed，耗时 56,132 ms；模型为 `kimi-k2.7-code-highspeed`，输入 105,465 token、输出恰好 4,096 token。
- 02:26:58.831，上下文构建报告 `fallback_reason=empty_summary`、`compacted=false`、`new_compaction=false`、`dropped_count=784`。
- 后续请求使用 `context_strategy=fifo` 继续执行：本次没有生成新摘要，退回移除较旧模型上下文的兜底路径；这不等于删除持久化会话历史。
- 本轮稍后因会话文件写锁超时失败，是 [issue 062](../062-notification-full-load-and-session-lock-timeout/README.md) 所述同类故障，不应与摘要失败合并为一个已确认原因。

## 根因初判

**confirmed / code-supported**：[summarizing.py](../../../agent/src/agent/context/summarizing.py) 的 `_compact` 使用 `max(512, min(4096, int(effective_window * 0.05)))` 计算预算，再以 `settings={"max_tokens": summary_output_budget}` 发起摘要生成。即使上下文窗口很大，摘要调用也最多获得 4096 token 的输出预算；这条计算没有按模型推理行为区分最终摘要长度与生成过程预算。

同一代码在取得 `response.text` 后执行 `strip_analysis(raw).strip()`；为空则进入 `empty_summary`，并退回 FIFO，而没有在该处分辨空正文、纯思考输出或达到长度上限等不同失败原因。

**confirmed / runtime-supported**：本次摘要调用返回后没有可用摘要，且输出计量恰好达到代码中的 4096 上限。运行观测标记 source commit 为 `6b23515e99d81b7241fc48e9c59a9a4b7206b9b4`；安装包带 dirty 标记，本次未对摘要函数执行安装包字节码核验，不能把来源 commit 等同于完整源码身份绑定。

**unconfirmed / observability-gap**：“模型先思考并耗尽 4096 token，尚未生成正文就被截断”是符合现象的假设，尚未取得该摘要响应的原始 `finish_reason`、正文长度及 reasoning 分量。不能仅凭 usage 等于上限就宣称已经证明，也不能把调用 completed 当成摘要业务成功。

展示侧的相关机制是：摘要无新 compaction 时后端发出 `discarded`，前端 [conversationReducer.ts](../../../frontend/src/stores/conversationReducer.ts) 移除对应进度块。这解释了失败原因没有保留在压缩提示中的代码路径；未执行本次真实 UI 验收。

## 影响与判断

长上下文摘要可能耗费真实模型调用后仍无法生成可用结果，随后截断上下文，影响后续任务信息连续性。通用固定输出上限也无法表达不同模型的思考开销与最终摘要目标。用户要求先登记，暂不实施修复。

2026-09-07 查阅的 Codex 官方 [本地压缩实现](https://github.com/openai/codex/blob/main/codex-rs/core/src/compact.rs) 未在该路径额外指定固定 4096 生成上限；其中 20,000 token 限制用于压缩后保留的用户消息。[远端压缩调用](https://github.com/openai/codex/blob/main/codex-rs/core/src/compact_remote_request.rs) 则使用专用压缩接口。这些是方案参考，不证明服务端没有限制，也不能直接代替 AF 对 Kimi 路径的验证；链接为可变 main。

## 可选修复方向

- 分开考虑最终摘要目标长度与模型生成预算，结合模型能力、推理模式和可用上下文确定策略，不只把 4096 换成另一个统一常量。
- 保留摘要调用的停止原因、正文与 reasoning 计量，区分长度截断、空正文、清洗后为空及其他 provider 失败。
- 评估达到预算上限后的有界恢复策略，并保留上下文兜底的可观测原因；不得把不完整摘要当成功结果提交。
- 使用确定性 fake 覆盖纯思考、长度截断、空摘要和正常摘要分支；真实模型对照须另行授权。失败提示的展示改进可按用户体验范围另行确认。

以上是后续候选方向，不是已批准的实现方案。本次未修改产品代码、未新增真实模型调用。

已在 commit `35c878fa3a590f7fb1f0eb8bc9f9608358efc370` 修复；后端、前端与 Rust 检查通过，真实模型效果未单独验证。
