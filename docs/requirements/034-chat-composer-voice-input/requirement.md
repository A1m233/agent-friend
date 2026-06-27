# Chat Composer 语音输入

## 状态

CONFIRMED

## 背景

当前桌面端已经有文字对话窗和语音通话能力。语音通话由 007 / 029 / 031 系列需求承接，语义是用户主动发起一段 RTC 通话，并通过 `voice_bridge` 进入 voice channel。

本需求要补的是另一类更轻量的语音能力：在 chat 输入框里把用户说的话转成文字，作为普通文本消息发送。它对应产品路线图里的“语音输入（ASR）”，但不改变对话 session 的通道语义，也不进入语音通话。

用户期望的体感接近常见聊天输入框：点击麦克风后开始录音，按钮变为停止；说话期间尽量实时出字；识别结果留在输入框中，用户可以继续编辑，也可以直接发送。

## 目标

让用户可以在 Chat Composer 内用语音输入文字，并保持发送前可控。

可衡量：

- Composer 中出现语音输入入口；点击后进入录音模式，入口变为停止动作。
- 录音期间优先支持边说边出字，识别出的文本实时呈现在输入框中。
- 停止录音后，最终识别文本保留在输入框里；用户可以继续编辑，也可以直接点击发送。
- 语音输入不创建语音通话，不触发 `channel_change`，不把任何内容写入会话历史；只有用户发送后才走现有文字消息链路。
- 录音 / ASR / 转写结果 / 错误 / 耗时有可关联日志，尤其 `voice_bridge` 侧要进入现有日志文件，方便排查。

## 范围

### 包含

**Chat Composer 语音入口**

- 在对话输入框中增加语音输入按钮，视觉位置参考用户给出的输入框截图。
- 点击语音按钮后进入录音模式；按钮切换为停止 icon / 停止动作。
- 再次点击停止按钮后结束录音，并保留当前转写文本。
- 录音模式下要有清晰状态反馈，避免用户不知道是否正在收音。

**实时语音转文字体验**

- 第一版目标是录音期间进行 ASR，让用户边说边看到文字。
- ASR partial / final 的具体呈现由 `design.md` 决定，但用户可见体验应能区分“正在识别”和“可编辑文本”。
- 如果设计或实测发现流式 ASR 的稳定性、延迟或接入成本不适合第一版，允许在技术方案中回退为“录音停止后一次性 ASR”；回退必须保留同一个 Composer 入口和用户可编辑 / 手动发送语义。

**输入框文本处理**

- 用户已有手打内容时，语音识别文本不能无提示覆盖原文本；插入 / 追加规则由 `design.md` 明确。
- ASR 已产出的文本应尽快进入可发送状态；用户不必等所有后处理完成才可以发送当前可见文本。
- 用户点击发送后，走现有 `useConversationStore.send` 文字发送链路。
- 发送前识别文本仍可编辑；不做自动发送。

**voice_bridge 承接 ASR 能力**

- 语音输入后端能力由 `voice_bridge` 承接，作为语音域的统一边界。
- 本需求新增的是面向语音输入的转写能力，不复用 `/voice/calls` 通话语义。
- 可复用现有火山语音凭证、语音错误模型、日志与运行配置；具体接口形态由 `design.md` 决定。

**麦克风反馈与 UI 复用**

- 录音期间复用或统一现有语音通话页的麦克风反馈体感，如音量反馈、正在收音、静音 / 无输入提示等。
- 相关 UI 应适配 light / dark 主题，遵守前端设计 token 约束。
- 按钮、tooltip、状态提示应使用项目现有 UI 封装和图标体系。

**日志与诊断**

- 前端记录录音开始、停止、取消、音频采集失败、ASR 连接失败、收到 partial / final、发送时机等关键事件。
- `voice_bridge` 记录 ASR 请求进入、上游连接、首个 partial、final、错误、耗时与 trace 信息。
- 日志要能按一次语音输入关联前端与 `voice_bridge`，便于从日志文件定位“没收音 / 没连上 / ASR 无结果 / 前端没写入输入框”等问题。
- 日志不得记录敏感凭证；对音频内容本身不做持久化记录。

**错误与取消**

- 用户可以取消当前录音 / 识别过程；取消后不应把未确认的识别结果强行发送。
- 麦克风授权失败、voice_bridge 不可达、ASR 上游失败、网络中断时，UI 展示用户可理解的提示，并保留用户已有输入。
- 失败后用户可以继续手打或重新发起语音输入。

## 不包含

- **不做语音通话**：不调用 `/voice/calls`，不加入 RTC 房间，不启动火山 AIGC 通话任务。
- **不触发 channel 切换**：不把当前 session 升级为 `voice`，不写 `channel_change`。
- **不做自动发送**：识别文本不会自动作为消息发送，必须用户点击发送或使用现有发送快捷键。
- **不做 always-on / wake-word / push-to-talk**：本期只做用户点击按钮显式开启的语音输入。
- **不做 voice_bridge / cloudflared sidecar 托管**：后端进程托管、动态公网 URL、凭证管理仍属于其他需求。
- **不做语音通话延迟优化**：031 仍独立承接通话链路延迟问题。
- **不做录音持久化、音频历史、声纹识别、多语言识别策略扩展**。
- **不改 agent_bridge / agent 对话协议**：语音输入最终仍表现为普通文本消息。

## 关键信息

- 产品路线图语音输入方向：`docs/decisions/0001-product-vision-and-roadmap/README.md`
- 语音交互显式触发姿态：`docs/decisions/0005-voice-interaction-form/README.md`
- 现有 voice_bridge 语音通话底座：`docs/requirements/007-voice-call/requirement.md`
- 桌面端语音通话前端接入：`docs/requirements/029-desktop-voice-call-frontend/requirement.md`
- 当前 Chat Composer：`frontend/src/pages/chat/components/Composer.tsx`
- 当前 voice 前端状态与麦克风反馈：`frontend/src/stores/voice.ts`、`frontend/src/pages/voice-call/App.tsx`
- 当前 voice_bridge 控制面与 ASR 配置参考：`voice_bridge/src/voice_bridge/routes/control.py`、`voice_bridge/src/voice_bridge/rtc/scenes.py`

## 验收标准

- [x] Chat Composer 中出现语音输入按钮；点击后进入录音模式，按钮切换为停止 icon / 停止动作。
- [x] 录音期间有明确的收音状态反馈，包括至少一种本地麦克风输入反馈或“正在听”的状态提示。
- [x] 录音期间优先能看到 ASR 增量文字；若最终技术方案选择停止后一次性 ASR，`design.md` 必须说明回退原因，并保持同一用户交互入口。
- [x] 停止录音后 final 文本保留在 Composer 内，用户可编辑。
- [x] ASR 已产出的可见文本可以直接通过现有发送动作发送；发送后进入普通文字消息链路。
- [x] 已有手打文本不会被语音输入无提示覆盖；插入 / 追加行为符合 `design.md` 约定。
- [x] 语音输入过程不调用语音通话控制面，不产生 `call_id`，不触发 `channel_change`。
- [x] 取消录音 / 识别不会发送消息；用户已有输入仍保留。
- [x] 麦克风授权失败、voice_bridge 不可达、ASR 失败时展示用户语言错误，且可重新尝试。
- [x] 前端与 `voice_bridge` 的语音输入日志能通过同一次输入的 trace 信息关联；`voice_bridge` 日志进入现有日志文件链路。
- [x] 日志中不记录火山语音 token、LLM key 等敏感凭证；不持久化原始音频内容。
- [x] 核心状态机、文本写入规则、错误分支和日志字段有自动化测试覆盖；真实 ASR 链路可通过手动 smoke 验证。
- [x] `./scripts/check/run.sh` 或对应平台门禁通过。

## 开放问题 / 待设计文档决策

- **ASR 接口形态**：前端与 `voice_bridge` 之间采用 WebSocket、SSE + 上传、HTTP 分片还是其他方式，由 `design.md` 结合火山 ASR 能力和现有架构确定。
- **实时 ASR 回退条件**：什么情况下从边说边出字回退到停止后一次性 ASR，需要在 `design.md` 中给出判断标准。
- **音频格式与采样率**：前端采集格式、是否需要重采样、后端是否转码，由 `design.md` 确认。
- **Composer 写入方式**：当前 `ChatSender` 是不受控组件，程序化写入文本的方式需要单独设计，避免破坏 IME 和发送行为。
- **已有文本插入规则**：转写文本追加到末尾、插入光标位置，还是作为独立片段追加，需要在 `design.md` 中明确。
- **发送中的 ASR 状态**：用户在 ASR 尚未 final 时点击发送，应发送当前可见文本、等待 final，还是阻止发送，需要在 `design.md` 中权衡体验和误识别风险。
- **日志 trace 口径**：前端、voice_bridge、上游 ASR 的 trace id / request id 如何关联，需在技术方案中确定。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-27 | 创建需求文档（CONFIRMED） | - |
