# 028 · 语音通话冷启动与回复延迟过高

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

### 2026-07-05 复测记录

复测路径：Windows 桌面端通过 `.\scripts\dev\run.ps1 --voice` 启动，打开语音通话入口，连续说两轮：

1. “介绍一下，你现在能做什么”
2. “请用一句话回答。”

本轮通话：

- `call_id=893c8a98-07a0-4b63-9411-627db8fa58a5`
- `trace_id=e09172e5-f456-46d2-89b3-6f89ed07bbcb`
- 新 tunnel：`https://outreach-program-showers-showing.trycloudflare.com`
- 关键日志：`%LOCALAPPDATA%/agent-friend/Logs/dev-voice.log`、`%LOCALAPPDATA%/agent-friend/Logs/dev-bridge.log`、`%LOCALAPPDATA%/agent-friend/Logs/tauri.log`

本次先遇到一个 dev 启动问题：`--voice` 复用了 shell 里残留的 `VOICE_BRIDGE_PUBLIC_URL`，但对应 cloudflared quick tunnel 已经退出。表现为前端能入房、采集、推流且有本地音量，火山 `StartVoiceChat` 返回 200，但没有任何 `volc_state_callback` / 用户字幕 / `llm_proxy_inbound`，用户体感是“我说话她不理我”。该问题已在当前修复分支中处理：`--voice` 默认忽略旧 `VOICE_BRIDGE_PUBLIC_URL` 并拉起新的 cloudflared tunnel，仅在显式设置 `AGENT_FRIEND_DEV_REUSE_VOICE_PUBLIC_URL=1` 时复用外部公网 URL。

修复 dev tunnel 后，同一路径两轮都跑通，分段如下：

```text
Cold start:
frontend_request_start -> frontend_preflight_ok       0.771s
frontend_request_start -> frontend_start_call_ok      1.143s
frontend_request_start -> frontend_rtc_join_ok        2.366s
frontend_request_start -> frontend_audio_capture_ok   2.441s
frontend_request_start -> frontend_rtc_publish_ok     2.730s
frontend_request_start -> frontend_start_agent_ok     4.559s
frontend_request_start -> frontend_ready_to_talk      4.560s
frontend_ready_to_talk -> first_local_volume          2.290s

Round 1:
user subtitle final -> llm_proxy_inbound              0.676s
llm_proxy_inbound -> upstream headers / first byte    0.311s
llm_proxy_inbound -> first real text chunk            4.319s
llm_proxy_inbound -> last chunk                       4.577s
llm_proxy_inbound -> bot subtitle final              10.366s
user subtitle final -> bot subtitle final            11.042s

Round 2:
user subtitle final -> llm_proxy_inbound              0.660s
llm_proxy_inbound -> upstream headers / first byte    0.306s
llm_proxy_inbound -> first real text chunk            4.741s
llm_proxy_inbound -> last chunk                       5.058s
llm_proxy_inbound -> bot subtitle final              10.341s
user subtitle final -> bot subtitle final            11.001s
```

与 2026-06-26 的旧证据相比：

- 冷启动这次明显变好：从约 35s 降到约 4.56s，旧证据里的 `publishStream` 21.775s 异常未复现。
- ASR final 到 `llm_proxy_inbound` 仍很快，约 0.66s 到 0.68s，不是主瓶颈。
- LLM 首个真实文本 chunk 约 4.3s 到 4.7s，是当前回复延迟的第一大块。
- LLM 流结束后到 bot 字幕 final 约 5.8s 到 6.0s，是当前回复延迟的第二大块，疑似火山侧 TTS / 播报 / 字幕回调链路耗时。
- `dev-voice.log` / `dev-bridge.log` 对排查是必要证据来源；服务自身 `voice_bridge.log` / `agent_bridge.log` 在本轮仍不是最完整的事后证据。

`agent_bridge.log` / `memory.log` 对应片段显示：

- 第一轮 `jieba` 冷加载约 0.44s。
- memory retrieve 本身不是主瓶颈，但第一轮会被 `jieba` 冷加载放大。
- LiteLLM / DeepSeek v4 flash 主链路在两轮中约 6s 到 8s 量级。
- 火山从 LLM 输出到 `answering` / bot subtitle 还有约 2.5s 到 5.5s 额外耗时。

### 2026-07-05 自动化 A/B smoke 记录

新增 `scripts/voice-latency-lab/` 自动化实验入口后，跑了一次最小真实 LLM 矩阵：

```powershell
.\scripts\voice-latency-lab\run.ps1 matrix --allow-llm
```

实验范围：

- 每个实验组启动独立临时 `agent_bridge` / `voice_bridge`，使用临时数据目录和日志目录，不写入正常桌面会话。
- `POST /voice/calls` 使用 `defer_start=true`，随后脚本投递火山 callback 形态的二进制 envelope fixture，再走真实 `voice_bridge -> agent_bridge -> LLM` CustomLLM 流式请求。
- 本实验不会调用真实火山 `StartVoiceChat`，也不会真实 RTC 入房 / TTS 播报；bot subtitle 是脚本在收到 LLM 流后仿真投递。因此它适合量化 LLM / 代理链路，不适合量化真实火山 TTS / 字幕回调耗时。

artifact root：`%TEMP%/agent-friend-voice-latency-lab/20260705-024119`

```text
scenario       trace_id                                                      first_text  last_chunk  inbound->bot_subtitle
baseline       voice-lab-baseline-1-0bb90321-22e9-40b8-ab42-8cbb75fcc612     4.301s      4.527s      4.827s
short_reply    voice-lab-short_reply-1-43f3dc9c-bcdb-417a-ae42-48c2468ce388  4.322s      4.528s      4.848s
max_tokens_64  voice-lab-max_tokens_64-1-228da552-c6d5-46b1-b1da-83fd9bdfc161 4.203s     4.383s      4.719s
```

初步判断：

- 结构化 callback 解码和日志汇总已可用：每组能按同一 `trace_id` 汇总到 27 条 `voice_latency` 事件，并能看到实验开关状态。
- 单次样本下，`max_tokens=64` 相比 baseline 首文本约快 0.10s、last chunk 约快 0.14s，幅度很小；`short_reply` 无改善。
- 这说明当前最小 prompt / max_tokens 调整不足以解决 4.3s 级 LLM 首文本问题，至少不能作为默认策略直接打开。
- 手动真通话里 “LLM last chunk -> bot subtitle final” 约 5.8s 到 6.0s 的火山 TTS / 回调段，本自动化实验没有覆盖；后续仍需要真通话或火山侧可控 smoke 来量化。

### 2026-07-05 自动化真实 RTC baseline 记录

新增 `scripts/voice-latency-lab/` 的 `live` 子命令后，跑了一次自动真实通话 baseline：

```powershell
.\scripts\voice-latency-lab\run.ps1 live --allow-live --hold-ms 45000 --call-timeout-s 140 --log-wait-ms 5000
```

实验范围：

- 自动启动隔离的 `agent_bridge` / `voice_bridge` / `cloudflared` / 本地 HTTP server。
- 通过 Chromium/Edge + fake microphone WAV 入火山 RTC 房间，真实触发 `StartVoiceChat`、火山 ASR、CustomLLM、火山 TTS / subtitle callback。
- 默认 Windows fake mic 音频由系统 TTS 生成短句并追加长静音；本次无需人工点击或说话。
- 第一次尝试在创建 call 前失败，原因是 Playwright bundled Chromium 未安装；runner 已改为缺 bundled browser 时回退系统 Edge/Chrome。下面结果来自修复后的第二次尝试。

本轮通话：

- `call_id=919c5d54-d39f-49e4-933b-9db5929602e1`
- `session_id=49f7de8a-d70e-4c25-894b-28df2b7681a1`
- `trace_id=voice-live-baseline-1-7dc3ddc8-f992-4386-951a-ad30b4c7e971`
- artifact root：`%TEMP%/agent-friend-voice-latency-lab/20260705-030656-live`

```text
start_call_response -> start_voice_chat_begin          1.275s
start_voice_chat_begin -> start_voice_chat_ok          1.923s
start_voice_chat_ok -> user listening callback         2.335s
user subtitle final -> llm_proxy_inbound               1.422s
llm_proxy_inbound -> upstream headers / first byte     0.333s
llm_proxy_inbound -> agent first text delta            4.091s
llm_proxy_inbound -> first real text chunk             4.424s
llm_proxy_inbound -> last chunk                        4.952s
llm_proxy_inbound -> answering callback                5.735s
llm_proxy_inbound -> first bot subtitle final         11.503s
llm_proxy_inbound -> answerFinish                     16.716s
```

新证据：

- 自动 live 能复现与手动真通话同量级的 LLM 首文本延迟：约 4.4s。
- 真实火山侧 “LLM last chunk -> first bot subtitle final” 本次约 6.55s，和手动真通话约 5.8s 到 6.0s 接近；这进一步确认第二大块在火山 TTS / 播报 / subtitle callback 段。
- `answering` 回调在 LLM last chunk 后约 0.78s 到达，但 first bot subtitle final 还要再约 5.77s；因此后续优化不应只盯 `answering`，还要把 bot subtitle / 真实出声首帧作为指标。
- fake mic 音频识别出的用户 subtitle `text_len=7`，足够触发一轮；后续做 A/B 时可复用同一音频，减少人工差异。

同一自动 live 链路又跑了两组真实 A/B：

```powershell
.\scripts\voice-latency-lab\run.ps1 live --allow-live --scenario short_reply --scenario max_tokens_64 --hold-ms 45000 --call-timeout-s 140 --log-wait-ms 5000
```

artifact root：`%TEMP%/agent-friend-voice-latency-lab/20260705-031014-live`

```text
scenario       trace_id                                                     first_text  last_chunk  bot_subtitle  answerFinish
baseline       voice-live-baseline-1-7dc3ddc8-f992-4386-951a-ad30b4c7e971   4.424s      4.952s      11.503s       16.716s
short_reply    voice-live-short_reply-1-79ea55b5-af0c-463c-ae9e-f9b06e73054f 4.035s      4.653s      15.311s       15.698s
max_tokens_64  voice-live-max_tokens_64-1-3dd1363c-8859-48a0-b754-0e08d5b96000 4.420s    5.131s       8.125s       16.044s
```

观察：

- `short_reply` 对 LLM 首文本有约 0.39s 改善，但 first bot subtitle 反而最慢；单次样本不支持默认开启。
- `max_tokens=64` 没改善 LLM 首文本，first bot subtitle 本次更早（8.125s），但 answerFinish 仍约 16s；说明 TTS / 字幕节奏存在较大波动，不能只看首字幕。
- 三组 `answerFinish` 都在约 15.7s 到 16.7s，说明整轮完成时间没有被当前两个 LLM 实验开关实质缩短。
- 目前更值得优先优化的是火山 TTS / subtitle 首帧和结束节奏，以及 LLM provider 首 token；`short_reply` / `max_tokens` 可以保留为实验开关，不应直接变成默认策略。

补充验证：为自动 live 的 fake microphone 音频增加 8s 前置静音，避免短语音在火山 agent 进入 listening 前播完。

```powershell
.\.venv\Scripts\python.exe scripts\voice-latency-lab\run.py live --allow-live --scenario baseline --speech-text "请讲一个超长睡前故事，至少一千字。" --pre-silence-ms 8000 --hold-ms 180000 --call-timeout-s 330 --log-wait-ms 10000
```

本轮通话：

- `call_id=bec20e6b-1f5e-49d0-b7b8-3805babc5fdd`
- `session_id=016564b4-af07-4389-a3c1-a6e06364f8a7`
- `trace_id=voice-live-baseline-1-413a2708-4e0b-49fd-8d0d-3a40624c5e6d`
- artifact root：`%TEMP%/agent-friend-voice-latency-lab/20260705-115442-live`

```text
start_call_response -> start_voice_chat_begin          1.422s
start_voice_chat_begin -> start_voice_chat_ok          1.928s
start_voice_chat_ok -> user listening callback         7.842s
round 2 user subtitle final -> llm_proxy_inbound       ~0s（日志写入顺序有 25ms 抖动）
llm_proxy_inbound -> first real text chunk             4.170s
llm_proxy_inbound -> last chunk                        4.802s
llm_proxy_inbound -> answering callback                5.139s
llm_proxy_inbound -> first bot subtitle final          5.746s
llm_proxy_inbound -> answerFinish                     14.930s
```

观察：

- 8s 前置静音后，短音频能稳定覆盖到火山 `listening` 之后；上一轮没有任何 callback 的失败可归因于 fake mic 播放时序，而不是产品链路本身。
- Windows TTS 对“请讲一个超长睡前故事，至少一千字。”仍被火山 ASR 拆成两轮：`请讲一个超长睡前故事` 与 `至少一千字`。
- 因为模型实际输出仍是短答，`first text -> last chunk` 只有约 0.63s，本轮仍不能证明“火山 TTS 会在 LLM last chunk 前开播”。
- `answering -> first bot subtitle` 本轮约 0.61s，比前面样本 5s+ 明显更快，进一步说明 TTS / subtitle 首回调存在波动，后续需要多样本和更可控的长文本输入。

进一步用更强的语音指令触发有效长回复：

```powershell
.\.venv\Scripts\python.exe scripts\voice-latency-lab\run.py live --allow-live --scenario baseline --speech-text "不要追问，直接讲一个一千字奇幻睡前故事。" --pre-silence-ms 8000 --hold-ms 210000 --call-timeout-s 360 --log-wait-ms 12000
```

本轮通话：

- `call_id=43aff271-0cba-467e-84cd-172e721b07c7`
- `session_id=3ea9a409-cae9-4989-9025-1526661b9361`
- `trace_id=voice-live-baseline-1-7709cbfa-0ac8-4077-8a6b-bafb7dbc21fc`
- artifact root：`%TEMP%/agent-friend-voice-latency-lab/20260705-120743-live`

session 落盘显示火山 ASR 仍把开头的“不要”拆成了被打断的 round 1，但 round 2 成功进入长回复：

```text
user: 直接讲一个1,000字奇幻睡前故事
assistant: 1801 chars
```

round 2 关键分段：

```text
user subtitle final -> llm_proxy_inbound               0.163s
llm_proxy_inbound -> first real text chunk             4.107s
llm_proxy_inbound -> answering callback                4.977s
llm_proxy_inbound -> first bot subtitle final          7.147s
llm_proxy_inbound -> last chunk                       25.508s
```

结论：

- 火山 `answering` 和首个 bot subtitle 都早于 `llm_proxy_last_chunk`，其中首个 bot subtitle 比 last chunk 早约 18.36s。
- 这证明当前 CustomLLM -> 火山 TTS 链路不是等完整 LLM 回复结束后才开始播报；长回复场景下，流式输出确实能被火山侧提前消费。
- 因此后续优化应优先围绕“更快产出可播报首句 / 语义片段、降低 LLM TTFT/TPOT、避免开头追问导致短答”展开；单纯等整段完成再送 TTS 不是当前链路的事实瓶颈。
- 本轮到 `hold-ms` 截止时仍未收到 `answerFinish`，说明长播报完成时间需要单独用更长 hold 或主动等待 `answerFinish` 的实验方式补测。

### 2026-07-06 本轮收尾记录

本轮在 `fix/028-voice-call-latency-config` 分支继续收敛语音通话延迟问题，主要做了两类工作：

- 体感兜底：语音 UI 状态拆细，避免 10 秒级链路里只显示单一等待状态；同时修复 DeepSeek streaming 异常后前端长期停在"正在播放回答"的问题。
- 指标校准：`scripts/voice-latency-lab live` 增加前端 RTC 远端音频指标，使用 `first remote nonzero volume` 作为"用户真实听到首音频"的近似指标，不再把 bot subtitle / answerFinish 当作首播口径。

本轮也落地了若干配置实验开关和默认值调整：

- ASR/VAD：将 `SilenceTime` 从 300ms 调整为 600ms，保留 `AIVAD=true`，降低自然停顿被过早截断的概率。
- LLM：开启火山 `LLMConfig.Prefill`，允许 ASR 中间结果提前送入 LLM 链路。
- TTS：切换到低延迟语音合成 voice_type 实验配置；但后续真实首音频数据表明，本轮主要瓶颈不在 TTS 首音频。

新增真实首音频指标后，连续 3 次 live baseline 结果如下：

```text
trace_id                                                     first_text  first_audio  bot_subtitle
voice-live-baseline-1-70f03cb1-9feb-4fda-9220-1424312a9de4   4.343s      5.282s       15.389s
voice-live-baseline-2-ad12b303-bc58-47fb-ae25-4ee7810b5dc4   4.207s      5.172s       12.944s
voice-live-baseline-3-6db12dc1-2ff6-489a-af97-11f5544f84f2   4.470s      5.573s       14.221s
```

阶段性结论：

- `LLM inbound -> first text` 稳定在约 4.2s 到 4.5s，是当前最明确的大头。
- `LLM first text -> first remote nonzero volume` 稳定在约 1.0s；从用户真实听到首音频看，TTS/RTC 首播不是本轮最大瓶颈。
- bot subtitle 比真实首音频晚 7.8s 到 10.1s，不能再作为首播延迟指标；后续实验应优先看 `first_remote_audio_volume_ms`。
- 真实首音频仍约 5.2s 到 5.6s，体感上还偏慢，但下一轮优化重点应转向语音专用 prompt、上下文裁剪、工具/记忆负担控制、DeepSeek 低延迟配置或更快模型实验。

本 issue 暂不标 `resolved`：本轮已把冷启动、状态反馈、异常兜底和指标口径阶段性收敛，但"语音首响仍约 5 秒级"的问题还需要下一轮围绕 LLM TTFT 继续优化。

## 根因初判

当前看不是单一问题，而是多个串行延迟叠加：

1. 冷启动链路中，本地 `voiceApi.startCall` / session 创建约 1s，不是主瓶颈。
2. RTC 入房有波动，最近一次 `joinRoom start -> joinRoom ok` 为 4.906s。
3. 最近一次最大异常点是 `startAudioCapture ok -> publishStream audio ok`，耗时 21.775s；历史正常值约 0.03s，疑似 SDK、设备、网络或调用顺序导致的偶发阻塞。
4. 当前前端启动顺序是 `joinRoom -> startAudioCapture -> publishStream -> startAgent`。只要 `publishStream` 阻塞，火山 `StartVoiceChat` 就被整体推迟。
5. `publishStream -> startAgent ok` 约 3.6s 到 6.5s，主要是火山 `StartVoiceChat` OpenAPI 外部耗时。
6. 用户说话后 ASR 到 `voice_bridge` 很快，约 0.5s；慢主要发生在 `voice_bridge` 转发到 `agent_bridge` / LLM / 火山 TTS 这一段。
7. 当前分支已补 `voice_bridge` LLM proxy first byte / first real text / last chunk，以及 `agent_bridge` first real text delta 埋点；剩余缺口主要是真实火山 TTS 首包 / 播报 / 字幕 final 的可控分段。

## 影响与判断

语音通话是核心体验，当前延迟会让用户觉得“接通了但不能自然对话”：

- 35 秒级冷启动会显著削弱拨号成功感。
- 11 秒到 15 秒级回复延迟不符合实时语音通话预期。
- 由于链路跨前端 RTC SDK、voice_bridge、agent_bridge、LLM provider、火山 ASR/TTS，修复需要系统性埋点和分段优化，不适合在当前功能修通收尾中顺手改。

本 issue 独立于早期 issue 019：019 记录 smoke 阶段暴露的综合问题；本 issue 专门跟踪修通后的桌面集成语音链路延迟拆解与优化。

## 可选修复方向

1. 补齐端到端 latency 埋点：
   - 前端：`requestStart`、`startCall`、`joinRoom`、`startAudioCapture`、`publishStream`、`startAgent`、本地音量首帧。
   - voice_bridge：`start_agent` 入站、`StartVoiceChat` 开始/结束、state/subtitle callback 解码后的 round/stage/timestamp（当前分支已补 callback 结构化日志）。
   - llm_proxy：请求入站、上游首字节、首个 SSE chunk、最后 chunk、异常（当前分支已补）。
   - agent_bridge：OpenAI encoder 首 delta、最后 delta、memory retrieve/observe 分段（当前分支已补首个真实文本 delta）。
2. 优化冷启动顺序：评估 `startAgent` 是否可以在 `joinRoom ok` 或 `startAudioCapture ok` 后就启动，不等待 `publishStream` 完成，至少把 `publishStream` 阻塞和火山 `StartVoiceChat` 外部耗时并行化。
3. 做麦克风 preflight：在用户点击拨号前或进入语音窗口时显式完成权限、设备枚举、SDK warmup，避免权限弹窗/设备冷路径隐藏在“正在叫醒她”阶段。
4. voice 模式专用 LLM 策略：缩短 system/context、限制回答长度和 `max_tokens`、减少工具/记忆负担，优先降低首 token 和总输出时长。
5. memory / jieba warmup：在 agent_bridge 启动或语音通话准备阶段预热分词和 memory 检索路径，避免第一轮语音回复吃冷加载。
6. 火山 TTS / callback 延迟定位：在能拿到 LLM first delta 后，对比火山 `answering` / bot subtitle 时间，判断是否需要调整 TTS 参数、字幕模式、VAD/SilenceTime 或 provider。
7. UI 体感兜底：把状态拆得更细，如“入房中 / 麦克风就绪 / 正在连接 AI / 可以说话”，避免所有外部耗时都表现为同一个卡住状态。
