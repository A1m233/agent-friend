# 可打断语音与自有 Agent：供应商调研

记录日期：2026-10-01。证据关系见 [索引](evidence-index.md)。以下是文档与代码调研，不是音质、稳定性、价格或时延实测排名。

## 需求背景

用户希望保留 agent-friend 自己选择的模型及 Agent 能力，同时获得持续收音、播放期间插话、及时停止播音并继续对话的体验。这里的全双工指交互链路可以同时收发；不能据此推导语音情绪、韵律等信息会直接进入文本 LLM。

## 现有代码

阅读版本：`1aa005d033a763c8a4975d482dd13e8f10267d08`。

- [scenes.py](../../../voice_bridge/src/voice_bridge/rtc/scenes.py)：`LLMConfig.Mode=CustomLLM`，ASR/TTS Provider 配置为 volcano。
- [llm_proxy.py](../../../voice_bridge/src/voice_bridge/routes/llm_proxy.py)：转发至 agent_bridge 的 `/v1/chat/completions`，附加自己的 session 标识。
- [assembly.py](../../../voice_bridge/src/voice_bridge/assembly.py)：运行时直接装配 `VolcRtcClient` / `VolcAsrProvider`。
- [rtcClient.ts](../../../frontend/src/services/voice/rtcClient.ts)：有 `RtcClient` 接口，但实现导入 `@volcengine/rtc`。
- [callbacks.py](../../../voice_bridge/src/voice_bridge/callbacks.py)：解码火山回调。
- [OpenAI route](../../../agent_bridge/src/agent_bridge/protocols/openai/routes.py)：持久会话以自己的历史为权威，忽略来访请求里的历史。

结论：模型入口已经隔离；更换供应商需要评估 RTC、控制面、事件、鉴权和部署拓扑。不能只换 URL。文本框语音输入的 ASR 与持续语音通话是不同路径，不应混算覆盖范围。

## 候选对比

| 方案 | 官方能力 | 对项目的初步适配判断 | 尚待验证 |
|---|---|---|---|
| 声网 Conversational AI | 自定义 OpenAI Chat Completions/SSE；语音和手动打断；语义断句（P1） | 托管替代方向，形态接近当前 CustomLLM | 云端访问本地 Agent 的方式、打断传播、字幕 / 历史同步、总费用 |
| 腾讯 TRTC AI 实时对话 | 自定义 APIUrl、流式响应、第三方 TTS；自动 / 手动打断（P2） | 与声网一起比较；HistoryMode=1 可让其发送的历史跟随已播放内容 | 该历史不会自动覆盖我们的持久会话，仍需做语义映射 |
| 阿里云 IMS AI 实时互动 | OpenAI 规范自定义模型、智能打断、自定义 TTS（P3） | 国内托管备选 | IMS 与 RTC / 百炼其他产品入口不能混淆；地域、开通和计费单独核验 |
| LiveKit Agents | 自定义模型端点 / 节点、轮次检测与打断、云端 / 自行部署（P4） | 更适合掌握编排；需要维护运行环境和接入层 | 中文端到端效果、部署位置、Tauri 兼容性、云端与本地功能差异 |
| Deepgram Voice Agent API | 自定义 LLM endpoint、语音事件与浏览器中断播放（P5） | 海外服务候选 | 中文 STT/TTS 组合、国内链路、客户端音频缓冲处理 |
| Pipecat | 可组合的语音流水线；打断时取消生成、清理合成与未播放音频（P6） | 框架候选，可作为解耦机制参考；不是一站式单一语音供应商 | 传输 / 识别 / 合成组合和完整部署成本 |

LiveKit 的当前轮次检测文档明确列出中文（P7）；这不构成中文真实产品效果验收。

## 待比较方向

此前讨论倾向：托管方向先看声网与腾讯；自主编排方向先看 LiveKit，Pipecat 作补充。火山可保留为对照。该顺序按接入形态和控制权判断，未代表采购、迁移或性能优劣决定。

托管 CustomLLM 回调通常需要云端能够访问模型端点。声网官方示例明确要求公网 HTTPS（P8）。如果 Agent 位于用户电脑，更换供应商不一定消除隧道；自运行 worker 的出站连接拓扑则需要另行验证。

## 后续体验核对点

- 中文停顿、附和、改口、中英混说、外放回声、背景说话。
- 打断至真实停音、旧生成取消、新输入完整性、连续插话。
- 长工具任务中的继续对话，以及停止说话与取消任务的不同语义。
- 已生成但尚未播放文本在自有会话中的记录方式。
- 端到端首响与 P95、失败恢复、部署和完整账单，而非单个 TTS 推理广告指标。

## 官方来源

均于 2026-10-01 阅读；页面可能更新，未保存网页快照。

| ID | 来源 |
|---|---|
| P1 | [声网自定义模型](https://doc.shengwang.cn/doc/convoai/restful/user-guides/custom-llm)；[打断](https://doc.shengwang.cn/doc/convoai/restful/user-guides/interrupt-agent) |
| P2 | [腾讯 StartAIConversation](https://cloud.tencent.com/document/product/647/108514)；[智能打断](https://www.tencentcloud.com/document/product/647/65319?lang=zh) |
| P3 | [阿里云 IMS 产品能力](https://www.alibabacloud.com/help/en/ims/user-guide/real-time-conversational-ai-overview)；[中文 FAQ 自定义模型边界](https://help.aliyun.com/zh/ims/user-guide/ai-real-time-interactive-faq) |
| P4 | [LiveKit 模型端点](https://docs.livekit.io/agents/models/llm/openai-compatible-llms)；[节点](https://docs.livekit.io/agents/logic/nodes/)；[打断](https://docs.livekit.io/agents/logic/turns/)；[部署](https://docs.livekit.io/deploy/agents/) |
| P5 | [Deepgram 模型](https://developers.deepgram.com/docs/voice-agent-llm-models)；[浏览器集成](https://developers.deepgram.com/docs/browser-agent-javascript) |
| P6 | [Pipecat 打断机制](https://docs.pipecat.ai/pipecat/fundamentals/interruptions) |
| P7 | [LiveKit turn detector](https://docs.livekit.io/agents/logic/turns/turn-detector/) |
| P8 | [声网自定义模型网关实践](https://www.shengwang.cn/blog/blogdetail/shengwang-conversational-ai-custom-llm/) |
