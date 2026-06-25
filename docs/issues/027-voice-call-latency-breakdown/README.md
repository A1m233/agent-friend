# 027 路 语音通话冷启动与回复延迟过高

- **状态**：open
- **严重度**：high
- **发现日期**：2026-06-26
- **关联需求**：007-voice-call、029-desktop-voice-call-frontend

## 现象

Windows 真机桌面语音通话已经可以接通，用户说话有本地音量反馈，静音和挂断按钮也可用。但体感上仍有明显延迟：

- 冷启动慢：从点击拨号到真正进入可对话状态，最新一次成功通话耗时约 35 秒。
- 回复慢：用户说完后，AI 首段字幕/语音反馈需要约 11 秒到 15 秒。
- 首次或冷路径可能更慢，历史日志里出现过麦克风/设备链路阻塞 40 秒级。

本次排查使用当前约定日志目录：

- `%LOCALAPPDATA%/agent-friend/Logs/tauri.log`
- `%LOCALAPPDATA%/agent-friend/Logs/voice_bridge.log`
- `%LOCALAPPDATA%/agent-friend/Logs/agent_bridge.log`
- `%LOCALAPPDATA%/agent-friend/Logs/memory.log`

### 冷启动分段

最近一次成功通话 call_id：`a57b042c-32e7-4601-b535-03f508efbc97`。

```text
requestStart -> startCall ok            1.236s
startCall ok -> joinRoom start          0.850s
joinRoom start -> joinRoom ok           4.906s
joinRoom ok -> startAudioCapture ok     0.068s
startAudioCapture ok -> publishStream  21.775s
publishStream -> startAgent ok          6.435s
requestStart -> active                 35.270s
active -> first local volume            2.520s
```

历史几次成功启动对比：

```text
22:42:34  requestStart -> active   6.241s
23:13:32  requestStart -> active  10.345s
00:27:06  requestStart -> active  35.270s
```

另有一次首启链路中，`enableDevices` 到 `joinRoom start` 间隔约 43 秒，疑似麦克风权限弹窗、设备授权或设备枚举冷路径阻塞。

### 回复分段

同一通话内两轮对话：

```text
Round 1:
user ASR final -> LLM proxy inbound      0.520s
LLM proxy inbound -> answering callback  8.324s
LLM proxy inbound -> first bot subtitle 10.800s
user ASR final -> first bot subtitle    11.320s

Round 2:
user ASR final -> LLM proxy inbound      0.459s
LLM proxy inbound -> answering callback  9.508s
LLM proxy inbound -> first bot subtitle 14.959s
user ASR final -> first bot subtitle    15.418s
```

`agent_bridge.log` / `memory.log` 对应片段显示：

- 第一轮 `jieba` 冷加载约 0.44s。
- memory retrieve 本身不是主瓶颈，但第一轮会被 `jieba` 冷加载放大。
- LiteLLM / DeepSeek v4 flash 主链路在两轮中约 6s 到 8s 量级。
- 火山从 LLM 输出到 `answering` / bot subtitle 还有约 2.5s 到 5.5s 额外耗时。

## 根因初判

当前看不是单一问题，而是多个串行延迟叠加：

1. 冷启动链路中，本地 `voiceApi.startCall` / session 创建约 1s，不是主瓶颈。
2. RTC 入房有波动，最近一次 `joinRoom start -> joinRoom ok` 为 4.906s。
3. 最近一次最大异常点是 `startAudioCapture ok -> publishStream audio ok`，耗时 21.775s；历史正常值约 0.03s，疑似 SDK、设备、网络或调用顺序导致的偶发阻塞。
4. 当前前端启动顺序是 `joinRoom -> startAudioCapture -> publishStream -> startAgent`。只要 `publishStream` 阻塞，火山 `StartVoiceChat` 就被整体推迟。
5. `publishStream -> startAgent ok` 约 3.6s 到 6.5s，主要是火山 `StartVoiceChat` OpenAPI 外部耗时。
6. 用户说话后 ASR 到 `voice_bridge` 很快，约 0.5s；慢主要发生在 `voice_bridge` 转发到 `agent_bridge` / LLM / 火山 TTS 这一段。
7. 现有日志只记录 `voice llm proxy inbound` 和 agent_bridge HTTP 200，缺少 first byte / first delta / last chunk 埋点，无法精确区分 DeepSeek 首 token、agent_bridge 编码、voice_bridge SSE 转发、火山 TTS 首包各自耗时。

## 影响与判断

语音通话是核心体验，当前延迟会让用户觉得“接通了但不能自然对话”：

- 35 秒级冷启动会显著削弱拨号成功感。
- 11 秒到 15 秒级回复延迟不符合实时语音通话预期。
- 由于链路跨前端 RTC SDK、voice_bridge、agent_bridge、LLM provider、火山 ASR/TTS，修复需要系统性埋点和分段优化，不适合在当前功能修通收尾中顺手改。

本 issue 独立于早期 issue 019：019 记录 smoke 阶段暴露的综合问题；本 issue 专门跟踪修通后的桌面集成语音链路延迟拆解与优化。

## 可选修复方向

1. 补端到端 latency 埋点：
   - 前端：`requestStart`、`startCall`、`joinRoom`、`startAudioCapture`、`publishStream`、`startAgent`、本地音量首帧。
   - voice_bridge：`start_agent` 入站、`StartVoiceChat` 开始/结束、state/subtitle callback 解码后的 round/stage/timestamp。
   - llm_proxy：请求入站、上游首字节、首个 SSE chunk、最后 chunk、异常。
   - agent_bridge：OpenAI encoder 首 delta、最后 delta、memory retrieve/observe 分段。
2. 优化冷启动顺序：评估 `startAgent` 是否可以在 `joinRoom ok` 或 `startAudioCapture ok` 后就启动，不等待 `publishStream` 完成，至少把 `publishStream` 阻塞和火山 `StartVoiceChat` 外部耗时并行化。
3. 做麦克风 preflight：在用户点击拨号前或进入语音窗口时显式完成权限、设备枚举、SDK warmup，避免权限弹窗/设备冷路径隐藏在“正在叫醒她”阶段。
4. voice 模式专用 LLM 策略：缩短 system/context、限制回答长度和 `max_tokens`、减少工具/记忆负担，优先降低首 token 和总输出时长。
5. memory / jieba warmup：在 agent_bridge 启动或语音通话准备阶段预热分词和 memory 检索路径，避免第一轮语音回复吃冷加载。
6. 火山 TTS / callback 延迟定位：在能拿到 LLM first delta 后，对比火山 `answering` / bot subtitle 时间，判断是否需要调整 TTS 参数、字幕模式、VAD/SilenceTime 或 provider。
7. UI 体感兜底：把状态拆得更细，如“入房中 / 麦克风就绪 / 正在连接 AI / 可以说话”，避免所有外部耗时都表现为同一个卡住状态。
