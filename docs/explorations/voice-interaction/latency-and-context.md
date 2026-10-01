# 语音首响、TTFT 与上下文：实践调研

记录日期：2026-10-01。证据关系见 [索引](evidence-index.md)。本文记录公开实践和候选思路，不确定产品接口、预算或实现方案。

## 历史证据与当前边界

[007 progress](../../requirements/007-voice-call/progress.md) 记录 2026-06-15 Windows + quick tunnel 的一次成功语音 smoke，模型使用 DeepSeek，后端可见的 ASR 字幕到 answering 状态约 8 秒，回答结束约 20 秒级。

本次仅阅读该记录，没有获取原始日志、当时精确代码和模型版本、prompt token、缓存、推理设置或真实客户端声学时间。HTTP 200 不等于首 token；answering 回调不等于用户听见声音。因此不能把历史 8 秒直接当成当前模型 TTFT，也不能全部归因给上下文长度。

当前源码阅读锚点为 `1aa005d033a763c8a4975d482dd13e8f10267d08`：

- [conversation.py](../../../agent/src/agent/conversation.py) 已有 `context_build_done`、`llm_first_text_delta`、`run_first_text_delta` 等观测点。
- [llm_proxy.py](../../../voice_bridge/src/voice_bridge/routes/llm_proxy.py) 区分首字节和首实际文本。
- [usage.py](../../../llm_providers/src/llm_providers/usage.py)、[prompt_cache_observation.py](../../../llm_providers/src/llm_providers/prompt_cache_observation.py) 提供缓存计数和观测，缺失字段不应算零命中。
- [voice route](../../../agent_bridge/src/agent_bridge/protocols/openai/routes.py) 存在短回复、max_tokens、禁用工具的实验选项；未检查当前运行配置或证明其收益。
- [上下文探索](../context-management/README.md) 是更早的讨论且注明已部分被 009 取代；当前模块见 [context](../../../agent/src/agent/context/)。不把旧文档中的“尚未实现”直接当成当前事实。

## 先区分测量口径

用户停止说话到听见有效回复，受断句 / ASR 收尾、上下文与记忆准备、模型排队和输入处理、生成首句、TTS 与网络、客户端播音共同影响。流水线可以重叠执行，各阶段不能无条件相加。

| 指标 | 起点与终点 | 不能替代什么 |
|---|---|---|
| 模型首事件 | 模型请求到首个任意流式事件 | 可能只是空 delta、工具参数或 reasoning |
| 模型首文本 | 模型请求到首个可显示 / 播报文本 | 不包括此前上下文准备；不保证够 TTS 合成 |
| 首可听声音 | 用户结束到客户端真正开始播音 | “我看看”等承接语未必含有效答案 |
| 首句有效回答 | 用户结束到听见与问题相关的有效内容 | 不表示完整任务完成 |
| 任务完成 | 发起任务到结果完成 | 工具任务允许较长，交互不应因此完全失联 |

Deepgram 官方将首任意 token、首文本、首工具调用和首思考 token 分列，并区分 TTS 与首音频字节（L7）；客户端播放仍需独立观测。

## 可借鉴的实践

### 1. 历史摘要 + 近期原文，摘要移出即时回复路径

Pipecat 的自动摘要支持按 token 或新消息数量触发，后台执行，重建为 system + summary + recent messages，并保留未完成工具调用序列（L2）。文档当前默认启用后的触发点是约 8,000 estimated tokens 或 20 条新消息，功能本身默认关闭。

启发：语音可以有低于模型最大窗口的工作预算。上述数字是框架默认值，不是跨模型 / 中文最优值；字符估算对中文的精度也需要核验。后台摘要还需处理版本落后、并发新输入、失败与缓存影响，不能假定异步就没有成本。

### 2. 优先清理低价值上下文，保留可恢复状态

Anthropic 的公开实践包括压缩、结构化笔记、清理冗余工具结果，并警告激进压缩会丢失细微但重要的上下文（L6）。这是一般 Agent 实践，不是语音 TTFT 的受控实验。

可探索的语音工作集是：当前任务 / 用户约束、相关记忆、近期对话、未完成动作与必要工具。旧资料可保留在持久记录中按需取回；裁剪模型输入不等于删除用户历史。

固定只留最后 N 轮容易丢失“刚才那一项”、纠正、承诺、授权及工具因果链。按阶段选工具也需保留恢复能力，并考虑工具定义变化是否破坏缓存。

### 3. 缓存友好的布局与压缩一起考虑

OpenAI 官方说明缓存复用输入前缀计算，减少回答前的处理时间；会话复用本身不保证命中，具体边界和模式取决于模型（L3）。

推断：每轮重写大摘要、挪动早期消息或动态插入前部内容，可能使短 prompt 失去原有热缓存收益。因此要同时比较总输入、cached / uncached 输入和首文本；不能以 token 变少代替延迟改善。其它 provider 的缓存规则需按实际模型另查，不能照搬 OpenAI 配置。

### 4. 快速对话与慢推理使用不同上下文

LiveKit 官方给出 talker-reasoner 模式：快速模型维持对话，慢模型在后台处理复杂分析，彼此保持独立上下文，避免推理历史进入每次对话（L4）。

适用候选：现有完整 Agent 负责慢任务，语音层处理当前交流并接收结果。代价是结果回流、取消、过期任务、权限与事实一致性；快速层不能在结果未到时编造答案。该模式不保证复杂问题本身更快完成。官方也建议先确认单 Agent 或普通异步工具是否已经足够。

### 5. 提前生成，让断句等待与模型工作重叠

LiveKit 提供 preemptive generation：转写可用时提前开始，尚未最终确认用户轮次；也可提前 TTS。输入发生变化时需要废弃旧尝试，并限制重试和不适合推测的长发言（L5）。

这是缩短关键路径，不是让模型本身 TTFT 变短。它可能增加无效调用，并要求把可取消生成与有副作用的工具 / 持久化提交区分开。当前火山配置有 Prefill 开关，但不能仅因命名相似就认定语义等同或直接开启。

### 6. 按需检索和流式播音都有收益边界

ElevenLabs 的 RAG 文档说明只取相关片段，同时明确检索会增加一段等待（L8）。因此 RAG 不是无成本的 prompt 缩短；也不必每轮为简单闲聊增加检索 / 路由模型。

流式 TTS 可以避免等整段合成结束，但不消除上游模型的首文本等待；地域、网络及合成模型本身也影响首响（L9）。短而完整的首句有利于更早形成可播片段，但仅限制整段回答长度不能保证 TTFT 改善。

## 对“必须更激进裁剪”的当前判断

这是合理候选，尚不能确定为必要条件。OpenAI 的一般延迟指南给出“输入减半可能只改善约 1–5% 延迟”的经验，同时说明巨大上下文是例外（L1）。该数字不是纯 TTFT 定律，不能外推到本项目的模型、长上下文和缓存状态。

目前更值得比较的是：原上下文、稳定缓存布局、摘要与近期原文、低推理 / 快模型配置、对话与慢任务分层。它们分别改变不同因素，不宜同时改动后把所有收益归给裁剪。

## 尚未执行的验证思路

- 固定实际使用的模型、任务输入、工具条件与部署位置；上下文候选单独比较。换模型另成一组。
- 分开短 / 长历史、闲聊 / 记忆追问 / 工具任务、冷 / 热缓存；记录输入和缓存 token、context build、首文本、首可听声音、首句有效回答和完成时间。
- 同时核对连续追问、旧约定、用户纠正、取消与工具完整性；不能用失忆换取漂亮时延。
- 输出分位数和失败 / 超时，而非挑单次最快值；提前生成还需统计废弃调用。
- 本轮没有制定正式实验参数，也没有执行任何真实 LLM、付费通话或性能测试。

## 官方来源

均于 2026-10-01 阅读；动态文档未归档快照，后续实施前需按实际版本复核。

| ID | 来源 | 证据边界 |
|---|---|---|
| L1 | [OpenAI latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization) | 一般工程经验，非本项目 TTFT 测量 |
| L2 | [Pipecat context summarization](https://docs.pipecat.ai/pipecat/fundamentals/context-summarization) | 框架机制和当日默认行为，非最优预算证明 |
| L3 | [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) | 机制依模型而异，不外推其他 provider |
| L4 | [LiveKit subagent delegation](https://docs.livekit.io/agents/logic/patterns/subagent-delegation/) | 公开模式及一致性约束，非项目迁移结论 |
| L5 | [LiveKit turn-taking tuning](https://docs.livekit.io/agents/logic/turns/tuning/) | 提前生成收益不保证，需要实际度量 |
| L6 | [Anthropic effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | 一般 Agent 的压缩与状态管理实践 |
| L7 | [Deepgram latency report](https://developers.deepgram.com/docs/voice-agent-latency-report) | 服务端指标口径，不等同客户端实际播音 |
| L8 | [ElevenLabs RAG](https://elevenlabs.io/docs/eleven-agents/customization/knowledge-base/rag) | 按需知识与检索额外等待的取舍 |
| L9 | [ElevenLabs understanding latency](https://elevenlabs.io/docs/eleven-api/concepts/latency) | 语音全链路与流式输出边界 |
