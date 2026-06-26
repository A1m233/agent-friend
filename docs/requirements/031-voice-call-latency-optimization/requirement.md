# 031 · voice-call-latency-optimization

## 状态

CONFIRMED

## 背景

007 voice-call 已交付 voice_bridge 控制平面、火山 RTC AIGC + CustomLLM 接入、session 续接与 `channel_change` 机制，并在 Windows 真机跑通语音端到端 smoke。029 desktop voice-call frontend 已把这条链路接入 Tauri 桌面端：用户可以从 pet / chat 显式拨号，加入 RTC 房间，发布麦克风音频，启动 AI，静音和挂断也可用。

但 029 收尾 smoke 暴露出新的核心体验问题：通话已经能接通，但冷启动和回复延迟过高。已登记 [issue 028](../../issues/028-voice-call-latency-breakdown/) 跟踪该问题。

当前一次成功通话的观测显示：

- 从点击拨号到前端进入 active 状态约 35 秒。
- 用户说完后，到 AI 首段字幕 / 语音反馈约 11 秒到 15 秒。
- 历史冷路径里出现过麦克风 / 设备链路 40 秒级阻塞。

这不是单点 bug，而是跨桌面前端、RTC SDK、voice_bridge、agent_bridge、memory、LLM provider、火山 ASR/TTS 的端到端体验问题。本需求承接 007 / 029 的已交付能力，专门把语音通话从“能接通”推进到“延迟可解释、可优化、体感接近自然对话”。

## 目标

本需求的目标是降低桌面端语音通话冷启动与首段回复延迟，并建立足够细的观测能力，让后续语音体验优化不再依赖人工翻多份日志猜测。

可衡量：

- 冷启动链路可以拆出稳定的分段耗时：拨号请求、voice_bridge 创建 / 绑定 call、RTC 入房、麦克风采集、发布音频、启动火山 AI、本地首个非零音量、前端可对话状态。
- 回复链路可以拆出稳定的分段耗时：用户 ASR final、voice_bridge LLM proxy 入站、agent_bridge 接收请求、首个真实文本 delta、voice_bridge 首个向火山转发的文本 chunk、火山 answering / bot subtitle 回调。
- 冷启动中确定可以并行或前置的等待不再串行阻塞，尤其避免单个 RTC publish 阶段异常阻塞把火山 AI 启动整体推迟。
- 语音首轮中的可预热冷路径被前置或显式 warmup，避免把一次性加载成本藏在用户首句回复里。
- 对所有可能削弱回答能力的 voice 专用策略，先提供可观测的实验与回退，而不是默认牺牲记忆、工具或人格表达。
- 用户界面能呈现更细的通话准备状态，减少“卡在一个模糊状态里”的体感。

## 范围

### 包含

**端到端 latency 观测**

- 补齐桌面前端、voice_bridge、agent_bridge / agent、memory、火山回调之间的关键时间点日志。
- 日志需能按 `call_id` / `session_id` / 单轮对话关联，支持一次通话内区分冷启动分段和每轮回复分段。
- 观测重点必须区分“HTTP / SSE 已响应”和“用户真正看见字幕或听见声音”，避免把空 role chunk 或代理层 200 错当成体感首响。
- 保留现有 `%LOCALAPPDATA%/agent-friend/Logs/` 等日志约定，不新增与项目路径规则冲突的落盘位置。

**冷启动优化**

- 复核当前前端拨号顺序，降低 `joinRoom`、`startAudioCapture`、`publishStream`、`startAgent` 之间不必要的串行等待。
- 在不破坏 007 session 绑定和 029 显式拨号语义的前提下，允许把火山 AI 启动与本地 RTC 发布链路并行化或更早触发。
- 对麦克风权限、设备枚举、RTC SDK 初始化等冷路径进行 preflight / warmup 评估和落地，减少首次拨号时隐藏的设备阻塞。
- 对外部 SDK / 网络波动导致的不可控等待，至少要做到可识别、可提示、可恢复；不承诺完全消灭所有外部阻塞。

**回复延迟定位与低风险优化**

- 在 agent_bridge / agent 层记录首个真实 `TextDelta` 到达时间，区分 provider 首 token、agent 装配、memory retrieve、上下文组装等阶段。
- 在 voice_bridge LLM proxy 层记录上游首字节、首个可转发文本 chunk、最后 chunk 与异常，确认代理层是否引入额外等待。
- 在能获得火山状态 / 字幕回调的前提下，记录火山 `answering` / bot subtitle 等体感回调时间，定位 LLM 文本到 TTS / 字幕之间的额外等待。
- 预热 memory / jieba 等已知首轮冷加载路径；该优化不得改变记忆召回语义。

**voice 模式策略实验**

- 允许设计 voice 模式的低延迟实验策略，如首句更快输出、回答节奏提示、适度输出长度约束、上下文负载观测等。
- 任何可能削弱回答能力的策略必须有显式开关、对照日志和回退路径，不能默认关闭 memory、默认禁用工具、默认大幅裁剪 persona / system prompt。
- 若实验策略被证明降低体感延迟且不明显损伤回答质量，可以在 design / progress 中记录采用条件；否则只保留观测结果，不强行合入默认路径。

**前端状态与诊断体验**

- 通话准备状态需要从单一“拨号 / 启动中”拆成更贴近真实链路的阶段，如创建通话、RTC 入房、麦克风就绪、连接 AI、可以说话。
- 当某阶段异常变慢或失败时，UI 展示用户语言提示，并保留足够诊断信息供开发者定位。
- 不改变 0005 已锁定的显式拨号姿态：语音仍由用户主动开始，挂断后释放麦克风，不引入后台监听。

**issue 028 闭环**

- 本需求验收完成后，回到 [issue 028](../../issues/028-voice-call-latency-breakdown/) 记录修复 / 缓解指向。
- 如果 031 只能定位而未能达到可接受体感，需要在 issue 028 或新 issue 中明确剩余瓶颈和下一步范围。

### 不包含

- **不更换 RTC / ASR / TTS vendor**：本期仍基于 007 已选的火山 RTC AIGC + CustomLLM 链路。
- **不做 voice_bridge / cloudflared sidecar 托管**：Python 进程打包、cloudflared 自动管理、动态公网 URL 注入属于后续独立需求。
- **不改变语音交互形态**：不做 always-on 监听、wake-word、push-to-talk 或 V2 语音形态。
- **不做凭证管理 UI**：不在 settings 中新增火山凭证录入 / 管理能力。
- **不把 voice 策略优化做成能力削减**：不默认禁用 memory、工具、persona 或长期上下文；如果需要探索，只能走可回退实验。
- **不承诺外部服务恒定低延迟**：火山 RTC、TTS、公网穿透、LLM provider 的网络抖动无法由本期完全控制；本期目标是降低可控串行等待，并把不可控等待测清楚。
- **不重构 agent_bridge / agent 的公开协议**：OpenAI / AG-UI 对外协议保持兼容；新增观测和策略应为加性扩展。
- **不做通话录音、音频持久化、声纹识别、多设备同步**。

## 关键信息

- 问题登记：[issue 028](../../issues/028-voice-call-latency-breakdown/)
- 前序语音底座：[007 voice-call](../007-voice-call/requirement.md)、[007 design](../007-voice-call/design.md)、[007 progress](../007-voice-call/progress.md)
- 桌面端前端接入：[029 desktop voice-call frontend](../029-desktop-voice-call-frontend/requirement.md)、[029 design](../029-desktop-voice-call-frontend/design.md)、[029 progress](../029-desktop-voice-call-frontend/progress.md)
- 语音交互形态决策：[0005 voice interaction form](../../decisions/0005-voice-interaction-form/README.md)
- 当前前端拨号状态机：`frontend/src/stores/voice.ts`
- 当前 RTC SDK 适配层：`frontend/src/services/voice/rtcClient.ts`
- 当前 voice_bridge 控制面：`voice_bridge/src/voice_bridge/routes/control.py`
- 当前 voice_bridge LLM proxy：`voice_bridge/src/voice_bridge/routes/llm_proxy.py`
- 当前 OpenAI SSE 编码：`agent_bridge/src/agent_bridge/protocols/openai/encoders.py`
- 当前 agent 记忆召回入口：`agent/src/agent/conversation.py`

## 验收标准

- [ ] 一次桌面端语音通话的冷启动日志能按同一 `call_id` 串起，并至少包含：用户拨号、`POST /voice/calls` 请求 / 返回、RTC join start / ok、audio capture ok、publish start / ok、`start-agent` 请求 / 返回、火山 AI 启动请求 / 返回、本地首个非零音量、前端进入可对话状态。
- [ ] 一轮用户语音回复的日志能按 `call_id` 和轮次串起，并至少包含：用户 ASR final、voice_bridge LLM proxy inbound、agent_bridge inbound、agent 首个真实 `TextDelta`、voice_bridge 首个向火山转发的真实文本 chunk、agent 最后 chunk、火山 answering 或 bot subtitle 回调。
- [ ] 日志中明确区分空 SSE role chunk、首个真实文本 delta、首个可见字幕 / 可听语音，避免把代理层响应时间误读为用户体感首响。
- [ ] 冷启动顺序中已确认可以并行或前置的部分完成调整；若某阶段仍必须串行，`design.md` 说明原因和风险。
- [ ] 麦克风 / RTC / memory / jieba 等冷路径至少有一项 preflight 或 warmup 落地，并有测试或 smoke 证据证明不会改变既有语义。
- [ ] voice 模式策略若涉及回答长度、工具、记忆或上下文裁剪，必须默认关闭或受显式开关控制，并提供对照日志；没有对照证据前不得作为默认行为合入。
- [ ] 前端通话 UI 展示更细的启动阶段和失败阶段；用户看到的是可理解状态，不是单一长时间“启动中”。
- [ ] 自动化可断言的状态机、日志字段、warmup / preflight 行为有测试覆盖；真实火山 RTC / TTS 端到端延迟通过 Windows 真机手动 smoke 记录验证。
- [ ] 本期改动不破坏 007 / 029 已交付能力：语音仍需显式拨号，通话仍复用 session / `channel_change`，挂断仍释放本地音频采集并降回 text。
- [ ] `./scripts/check/run.sh` 或对应平台门禁通过；若必须在 Windows 验证，记录实际运行的 `run.ps1` 门禁结果。
- [ ] 验收完成后更新 issue 028 状态或追加缓解记录，说明 031 的结果、剩余瓶颈和后续是否需要新 issue / 新需求。

## 开放问题 / 待设计文档决策

- **冷启动并行边界**：`start-agent` 最早能提前到哪个阶段，才能同时保证火山 AI 能正常入房、session 绑定已完成、失败清理仍可靠。
- **preflight 触发时机**：在打开 voice-call 小窗、用户确认公网穿透、还是点击拨号后进行麦克风 / RTC warmup，才能兼顾延迟、隐私提示和显式触发语义。
- **轮次关联方式**：如何在不解析或篡改火山请求 body 的前提下，为 LLM proxy、agent_bridge、火山回调建立稳定的 round id / trace id。
- **首个真实文本 chunk 判定**：OpenAI SSE 空 role chunk、空 content、tool delta、错误 chunk 与真实可朗读文本如何分类，避免观测误判。
- **火山回调可用性**：当前状态 / 字幕回调能否稳定作为 answering / 首字幕时间依据；如果不可用，是否需要用其他可观测点替代。
- **voice 策略实验边界**：哪些低延迟策略可作为默认安全优化，哪些必须保留为手动开关或只记录实验结果。
- **延迟目标阈值**：冷启动和首段回复的目标值需要结合真实 Windows smoke 基线确定；`design.md` 应先用当前基线给出合理目标，而不是凭空承诺。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-26 | 创建需求文档（CONFIRMED） | - |
