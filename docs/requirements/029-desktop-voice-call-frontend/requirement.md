# 桌面端语音通话前端接入

## 状态

CONFIRMED

## 背景

007 voice-call 已交付 voice_bridge 控制平面、LLM 入站代理、通话与 session 续接、`channel_change` 机制，并在 2026-06-15 完成 Windows 真机端到端 smoke。现有 smoke 页面验证了从浏览器拨号、加入火山 RTC 房间、发布麦克风音频、启动 AI、挂断释放资源的完整路径，但它仍是开发者验证工具，不是桌面端产品入口。

0005 语音交互形态 ADR 已锁定项目级姿态：语音必须由用户显式触发，V1 走显式拨号，不做 always-on 监听；公网穿透必须由用户显式接受，不能偷偷启动或隐藏前置条件。本需求承接该姿态，把 007 的能力接入 Tauri 桌面端前端，让用户可以从桌宠和对话窗进入一段明确开始、明确结束的语音通话。

本需求只做 V1 前端接入。voice_bridge 与 cloudflared 仍由用户按 007 smoke 跑法自行启动；语音通话依赖的火山凭证、后端进程托管、动态公网 URL 注入、延迟优化和 V2 交互形态均不在本期解决。

## 目标

把 007 smoke 中的拨号流产品化进桌面端前端，并保持用户对麦克风与公网穿透的明确掌控。

可衡量：

- 用户能从 pet ActionBar 显式发起语音通话；未进入通话前，入口保持轻量，只呈现拨号动作。
- 首次启用语音通话前，前端以阻塞式 double check 明示公网穿透前提，用户确认后才继续拨号；拒绝则不调用 voice_bridge、不启用麦克风。
- 通话中有清晰的通话面板体验：能看到当前状态、通话时长、音量反馈、错误提示和挂断入口。视觉体感参考用户提供的通话面板截图：主体聚焦在头像 / 状态文案 / 底部通话 controls，但具体是复用 bubble 窗还是新浮窗由 `design.md` 决定。
- 当前 chat session 进入 voice 通道时，对话窗显示“通话中”状态，输入框停用，避免文字与语音同时往同一 session 写入造成心智冲突。
- 拨号时可传当前 `session_id`，复用 007 已有的 session 续接与 `channel_change`，让语音通话延续当前文字上下文。
- macOS Tauri 打包产物具备麦克风权限说明，系统权限弹窗不会出现空白或不可信描述。

## 范围

### 包含

**RTC SDK 前端集成**：

- 在 Tauri webview / Vite 前端中通过 npm 依赖集成 `@volcengine/rtc`，替代 007 smoke 的 CDN 引入方式。
- 将 `voice_bridge/smoke/index.html` 中已验证的核心拨号路径产品化：`POST /voice/calls`、加入 RTC 房间、发布本地麦克风音频、`POST /voice/calls/{call_id}/start-agent`、`POST /voice/calls/{call_id}/stop`。
- 前端对 RTC 初始化、设备授权、发布音频、离房清理做用户可感知的状态反馈；失败时展示用户语言错误，不暴露 SDK 堆栈或底层异常。

**前端 voice 管理器**：

- 新增前端 voice 状态管理与服务层，封装拨号、挂断、清理、错误、通话时长、音量状态。
- 状态机至少覆盖：未启用 / 等待公网穿透确认 / 拨号中 / 等待 AI 接入 / 通话中 / 挂断中 / 已结束 / 错误。
- 同一时刻只允许一通前端语音通话；重复点击拨号或多窗口触发时需要有明确保护，避免创建多通孤儿通话。
- 页面刷新、窗口关闭或调用失败时尽力挂断并释放本地麦克风采集；不能保证服务端释放时，也要让用户看到清晰的错误与下一步提示。

**pet ActionBar 拨号入口**：

- 在 pet ActionBar 中增加语音拨号按钮，作为 V1 显式触发入口。
- 与既有分页机制、dev 注入按钮、hover 命中区共存；按钮数量变化不能导致分页、箭头、命中区域出现明显抖动或错位。
- 拨号按钮在不可用、拨号中、通话中等状态下有明确视觉状态；通话中再次点击的行为由 `design.md` 决定，但用户必须能从通话 UI 挂断。

**通话期间 UI**：

- 提供通话期间的前端 UI，呈现当前状态、通话时长、本地音量反馈、错误提示和挂断入口。
- 通话 UI 的视觉方向参考用户给出的通话面板截图：头像居中、状态文案居中、底部 controls；未拨出前可以是更轻量的单拨号入口。
- UI 不引入 always-on 或环境监听语义：只有用户主动拨号后才进入通话态，挂断后恢复非监听状态。
- 通话 UI 必须适配 light / dark 主题，视觉 token 走项目 CSS 变量，不硬编码颜色、间距、字号、圆角或阴影。

**chat 窗 channel banner**：

- 当前 chat session 被语音通话占用时，chat 窗显示“通话中”或等价 banner。
- 通话期间禁用文字输入与发送，避免用户误以为可以同时用文字和语音向同一 session 对话。
- 通话结束、channel 降回 text 后，banner 消失，输入恢复。
- 历史消息投影仍聚焦文字与工具事件；本期不要求把语音过程中的实时状态作为消息渲染进 MessageList。

**公网穿透显式同意**：

- 首次启用语音通话前弹出阻塞式 double check，明示：语音通话需要让火山云通过公网 URL 回调本机 voice_bridge，用户需自行启动 voice_bridge 与公网穿透。
- 用户必须明确确认后才继续拨号；关闭弹窗、取消或拒绝都视为不同意，不调用 voice_bridge、不请求麦克风权限。
- 同意状态可见、可关闭；关闭后再次拨号必须重新确认。
- 本期只做前端同意与状态呈现，不托管 cloudflared，不自动生成公网 URL。

**macOS 麦克风权限**：

- Tauri macOS 配置补齐 `NSMicrophoneUsageDescription` 或等价配置，使系统麦克风授权弹窗有清晰说明。
- 如 WebRTC 在 macOS WKWebView 下需要额外 entitlements 或配置，本需求要求在 `design.md` 中验证并落地。

**复用 007 session 续接**：

- 从当前 chat session 发起或关联语音通话时，`POST /voice/calls` 传 `session_id`，沿用 007 的 `channel_change` 机制升级为 voice。
- 挂断后沿 007 机制降回 text；前端可观察并更新 chat banner / 输入禁用状态。

## 不包含

- **不托管 voice_bridge / cloudflared 进程**：Tauri sidecar、Python 进程打包、cloudflared 自动启动、崩溃恢复、动态公网 URL 注入均归后续独立需求。
- **不做凭证管理 UI**：不在 settings 中新增“语音通话”分类，不录入或管理 `VOLC_*`，后续基于 028 设置中心扩展。
- **不做 wake-word / push-to-talk / V2 形态**：V1 只做显式拨号；V2 候选方向按 0005 的后续 spike 评估。
- **不做 always-on 监听**：不允许后台持续占用麦克风，不允许用户未拨号时采集或上传音频。
- **不做延迟优化**：007 progress 记录的“用户说完 → AI 开始回答约 8 秒”单独立项，本期只把现有链路接入前端。
- **不更换 RTC vendor / 不重写 voice_bridge 契约**：本期消费 007 已有 HTTP 契约，除前端接入所需的兼容性问题外，不重构后端控制面。
- **不做通话录音、音频持久化、声纹识别、多设备同步**。
- **不做 IM 语音接入**：022 后续扩展另立需求，本期只接桌面端 pet / chat surface。

## 关键信息

- 项目级姿态：`docs/decisions/0005-voice-interaction-form/README.md`
- 前序语音底座：`docs/requirements/007-voice-call/requirement.md`、`design.md`、`progress.md`
- smoke 原型：`voice_bridge/smoke/index.html`
- voice_bridge 控制面契约：`voice_bridge/src/voice_bridge/routes/control.py`
- ActionBar 接入点：`frontend/src/pages/pet/ActionBar.tsx`
- chat 输入与历史投影：`frontend/src/pages/chat/components/Composer.tsx`、`frontend/src/stores/sessionProjection.ts`
- Tauri 多窗口与 macOS 配置：`frontend/src-tauri/tauri.conf.json`

## 验收标准

- [ ] `@volcengine/rtc` 作为 npm 依赖接入前端，并能通过 Vite / Tauri build；不再依赖 smoke 页 CDN 方式。
- [ ] pet ActionBar 出现语音拨号按钮；与现有按钮、分页箭头、dev 注入按钮共存，无布局抖动或命中区错位。
- [ ] 首次拨号前出现阻塞式公网穿透 double check；用户取消时不调用 `POST /voice/calls`，不请求麦克风权限。
- [ ] 用户确认公网穿透前提后，前端调用 `POST /voice/calls`，拿到凭证后加入 RTC 房间、发布本地麦克风音频，并调用 `POST /voice/calls/{call_id}/start-agent`。
- [ ] 通话中 UI 展示状态、通话时长、本地音量反馈和挂断入口；状态文案与 controls 体感参考通话面板截图，未拨出前保持轻量拨号入口。
- [ ] 点击挂断后，前端调用 `POST /voice/calls/{call_id}/stop`，离开 RTC 房间，停止本地音频采集，UI 回到非通话态。
- [ ] 发生拨号失败、RTC 初始化失败、麦克风授权失败、voice_bridge 不可达等错误时，UI 展示用户语言错误，并释放已获得的本地资源。
- [ ] 当前 chat session 进入 voice 通道时，chat 窗显示通话中 banner，Composer 禁用；通话结束降回 text 后输入恢复。
- [ ] 从已有 chat session 发起通话时，`POST /voice/calls` 携带当前 `session_id`，后端产生的 `channel_change` 能被前端用于更新通话态。
- [ ] 同一时刻前端只允许一通语音通话；重复点击或多入口触发不会产生多通活跃 call。
- [ ] macOS 打包配置包含麦克风权限说明；如需要 entitlements，已补齐并记录在 `design.md`。
- [ ] `design.md` 正面处理 WKWebView WebRTC 兼容性、`@volcengine/rtc` Vite 打包兼容性、ActionBar 分页共存三项风险，并给出验证策略或前置 spike。
- [ ] 核心 voice store / 状态机 / ActionBar 分页影响有自动化测试覆盖；纯 WebRTC 真链路允许通过手动 smoke 验证。
- [ ] `./scripts/check/run.sh` 全过。

## 开放问题 / 待设计文档决策

- **通话 UI 落位**：复用 bubble 窗、在 pet 附近新建浮窗、还是在既有 chat 窗内承载；需要兼顾参考图体感、窗口焦点、透明窗口和跨平台行为。
- **WKWebView WebRTC 兼容性验证**：macOS Tauri webview 是否支持 `@volcengine/rtc` 所需的 getUserMedia / WebRTC 能力，是否需要最小 spike 先验证。
- **`@volcengine/rtc` Vite 打包策略**：直接 ESM import 是否可行，是否需要动态 import、worker / wasm / UMD 兼容处理。
- **公网穿透同意状态存放**：本期是跟随 028 设置中心写入 Tauri 配置，还是先做 voice store 内部状态；需要保证“可见、可关、拒绝即阻断”。
- **chat 窗如何观察 channel**：通过 session detail 事件流投影、voice store 广播、还是新增前端同步事件；需避免 chat / pet 多窗口状态漂移。
- **通话异常清理策略**：窗口关闭、刷新、RTC 加入成功但 start-agent 失败、stop 失败等边界下，前端如何最大努力释放资源并提示用户。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-25 | 创建需求文档（CONFIRMED） | - |
