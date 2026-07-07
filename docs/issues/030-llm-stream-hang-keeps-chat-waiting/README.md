# 030 · LLM stream hang keeps chat waiting forever

- **状态**：resolved
- **严重度**：high
- **发现日期**：2026-06-26
- **关联需求**：006-agent-bridge、010-desktop-shell-and-chat-ui

## 现象

桌面对话页里，最新会话发送消息后一直停在 assistant 的等待态（三点 loading），不输出回复。点击 stop 后再次发送，仍然无回答，说明不是单个前端 placeholder 没清理的偶发现象。

2026-06-26 本地排查时的可复现时间线：

```text
22:39:52  memory.retrieve 完成，随后 LiteLLM completion() model=deepseek-v4-flash
22:40:41  memory.retrieve 完成，随后 LiteLLM completion() model=deepseek-v4-flash
22:51:30  stop 后重试，memory.retrieve 完成，随后 LiteLLM completion() model=deepseek-v4-flash
```

对应日志特征：

- `~/Library/Logs/agent-friend/memory.log` 中每轮 `memory.facade retrieve` / `memory.retrieval.strategy search` 都正常结束，且返回 0 candidates。
- `~/Library/Logs/agent-friend/agent_bridge.log` 中每轮只有 `LiteLLM completion() model= deepseek-v4-flash; provider = deepseek`，没有后续 `Wrapper: Completed Call`。
- 同一时间段没有 `AG-UI run ... 失败`，也没有 `RUN_ERROR` 相关日志。
- `~/Library/Application Support/agent-friend/sessions/im:qq:demo-user-2.jsonl` 最后更新时间停在 `2026-06-26T22:09:48+0800`，没有写入 22:39 / 22:40 / 22:51 的新消息。
- `GET http://127.0.0.1:18800/healthz` 仍返回 `{"status":"ok"}`，bridge 主服务没有整体挂死。
- `lsof -iTCP:18800` 显示前端到 bridge 仍有一条 established SSE 连接；stop 后旧请求没有明显无限堆积，但新请求会再次进入同一等待状态。

前端表现上，这些消息是 `conversation.ts` 的乐观插入态：发送时先插入 user message 和空 assistant placeholder。由于后端流没有结束也没有抛错，placeholder 一直保持 streaming。

补充触发线索：发现该问题的上一轮交互里，用户曾在 agent 正在流式回答时直接 stop / 掐掉会话。该行为不一定是唯一根因，但很可能触发或放大了前端 abort、后端流式生成、provider 连接收尾之间的取消语义问题。

## 根因初判

卡点在 memory 之后、DeepSeek / LiteLLM 流式调用这一层。

当前 `Conversation.stream()` 第一轮 LLM 完成后才会落 `user_message` 和 `assistant_message`。因此当 LLM 首轮没有正常完成时，本轮用户消息不会写入 session JSONL，前端只能看到本地临时态。

`llm_providers/src/llm_providers/client.py` 调用：

```python
litellm.completion(..., stream=True, **kwargs)
```

但当前没有设置 `timeout`。本地 LiteLLM `completion()` 签名支持 `timeout` 参数。若 provider 网络、DeepSeek 服务或 LiteLLM stream wrapper 卡在初始化 / 迭代 / 收尾阶段，异常不会按预期映射成 `LLMNetworkError`，AG-UI encoder 也就无法发出 `RUN_ERROR`，前端会一直等待。

另一个关键怀疑点是 stop / disconnect 语义。前端 `stop()` 当前主要是 `AbortController.abort()`，只能保证本地 fetch / SSE 结束；后端 `StreamingResponse` 正在消费同步 generator 时，如果已经阻塞在 provider stream 迭代里，客户端断开不一定能立即打断或关闭 provider response。此时如果再缺少 LLM timeout，就可能出现后端或 SDK 层等待 provider 自行返回的状态。

尚未完全确认的是：本次卡住到底是 DeepSeek provider 临时不可用、网络链路问题，还是 LiteLLM stream wrapper 对已关闭连接的处理问题。但就产品行为而言，缺少 LLM 调用超时与流式兜底已经足以解释“无穷等待”。

## 影响与判断

这是文本对话核心路径问题，严重度高：

- 用户会看到 assistant 永久 loading，且 stop 后再次发送仍可能复现。
- 因为本轮消息未落盘，刷新 / 切换会话后这些前端临时消息会丢失，用户感知更混乱。
- bridge 健康检查仍正常，普通“服务在线”状态无法暴露此类上游 LLM 卡死。
- 如果没有 timeout / run-level failure，前端无法区分“正在生成”与“后端已经卡死”。

暂不立即修复，因为稳妥方案需要同时处理 provider timeout、AG-UI 错误收尾、stop/cancel 语义、日志埋点与前端状态文案，范围超过当前前端界面优化分支里的小改动。

## 可选修复方向

1. 在 `ProviderSpec` 或 bridge settings 中新增 LLM timeout 配置，例如 `DEEPSEEK_TIMEOUT_SECONDS` / `AGENT_LLM_TIMEOUT_SECONDS`，并在 `LLMClient.complete()` 与 `LLMClient.stream()` 调用 `litellm.completion()` 时传入 `timeout`。
2. 超时统一映射成 `LLMNetworkError`，让 `agent_bridge.protocols.ag_ui.encoders.encode_stream()` 能发出 `RUN_ERROR`，前端退出 waiting 态并展示拟人化兜底文案。
3. 为流式调用补更细日志：run id、thread id、model、LLM call start、first chunk、finish reason、exception / timeout、elapsed_ms。避免以后只能从 LiteLLM 的开始/完成日志反推。
4. 明确 stop/cancel 语义：前端 abort fetch 后，后端如果已经进入阻塞式 provider generator，可能无法立即取消；至少应依赖 provider timeout 收敛，并尽量在 client disconnect / `GeneratorExit` 时关闭 provider response，必要时再评估 run registry / cancellation token。
5. 前端增加“长时间等待”兜底：例如超过阈值后提示“这次回复有点卡住了，可以停止后重试”，但这只能改善体感，不能替代后端 timeout。
6. 可选增加 provider 健康 / 熔断：连续 LLM timeout 后在连接状态或 debug 面板里暴露“LLM 上游异常”，不要只显示 bridge 已连接。

---

已在 commit `12b3c1e` 修复。
