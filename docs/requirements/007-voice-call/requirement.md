# 007 · voice-call

> agent 当大脑的语音通话能力

把 spike 已验证的 C 方案（火山 RTC AIGC + CustomLLM）正式产品化为 agent-friend 的语音通话能力，并把语音控制能力抽成顶层 voice_bridge 模块，作为未来多 surface 接入的统一底座。

## 状态

<!-- DRAFT | CONFIRMED -->
CONFIRMED

---

## 1. 背景与价值

### 1.1 现状

[`experiments/voice-poc`](../../../experiments/voice-poc/SPIKE-NOTES.md) 已在 spike 中验证 **C 方案**（火山 RTC AIGC + 自定义 LLM）端到端跑通：浏览器实测可正常通话、ASR / 流式 TTS / 全双工打断 / 流式 LLM 都过关；spike 末尾留下"是否值得正式立项"结论 = **A 值得立项**。

[`006 agent-bridge`](../006-agent-bridge/requirement.md) 已交付：`POST /v1/chat/completions`（OpenAI，默认无状态）+ `POST /ag-ui/run`（AG-UI，有状态）+ 扩展位 header `X-Agent-Friend-Session-Id`，默认 `127.0.0.1:18800`。火山 RTC `LLMConfig.Mode=CustomLLM` 是 OpenAI 协议客户端，**天然能作为 agent_bridge 的下游**——这意味着接入 agent 当语音大脑的所有协议层基础已经就位。

### 1.2 痛点

spike 的形态有 3 个问题让它无法直接转正：

- **Node demo Server**（`rtc-aigc-demo/Server`）是火山官方 demo + 私下打的 patch；demo 整个被 git-ignored，patch 不进 git；按 [`0002 §3.2`](../../decisions/0002-incubation-tech-stack/README.md) 锁定的 Python 主线，工程上不能 fork demo 做产品化
- **Phase 0 LLM 走的是火山方舟**（`Mode=ArkV3`），用的是火山的大脑而不是 agent-friend 自己的；**记忆 / persona / system prompt composer 都没接入**，跟 [`0001`](../../decisions/0001-product-vision-and-roadmap/README.md) "记忆是第一护城河"原则冲突
- **没有任何模块层抽象**：spike 是一坨 demo，下次有桌宠 / IM / 客户端等 surface 想做语音，要么抄一份 demo 要么重做控制平面——违背 [`0001 §2.2`](../../decisions/0001-product-vision-and-roadmap/README.md) "感官通道层"应当独立的架构

### 1.3 为什么独立立项

把"语音通话"作为独立需求做（而不是塞进 [`006 agent-bridge`](../006-agent-bridge/requirement.md) 里"顺手"或者塞进未来的桌宠需求里）：

- voice 控制平面（开/停通话、火山 OpenAPI 签名、scenes 组装、RTC token 签发）跟 agent_bridge 的"对外暴露 agent 大脑"是两个完全不同的职责，不应混在一个进程里
- 通话 ↔ session 桥接（session_id 注入 / channel 字段升降级）这套机制一旦敲定，所有未来 surface（桌宠 / IM / 客户端）想做语音都直接调 voice_bridge——成为感官通道层的统一入口
- 独立立项后，本期闭环讨论 voice_bridge 的对外契约（HTTP 接口形态 / 通话状态机 / 错误模型），未来下游需求只在已有底座上接入，scope 大幅收窄

### 1.4 与未来需求的衔接

本期不实现的能力扩展方向（Welcome Message 动态生成、voice_type 与 persona 关联、ASR/TTS 模块抽象、产品级前端、云化部署等）作为兄弟需求单独立项。本期对外契约（voice_bridge HTTP 接口 / 通话状态机 / 错误模型 / channel 语义）一旦敲定就是**长期稳定的承诺**——下游需求只能"扩展、不替换"。

---

## 2. 本期范围（In Scope）

| 模块                        | 目标                                                                                                          | 优先级 |
| --------------------------- | ------------------------------------------------------------------------------------------------------------- | ------ |
| voice_bridge 控制平面       | 对前端/桌宠/IM 暴露 HTTP（开/停通话、查通话状态）；内部封装火山 OpenAPI Sign V4 + scenes 组装 + RTC token 签发 | P0     |
| voice_bridge LLM 入站代理   | 接收火山 RTC `CustomLLM` 回调，按 `call_id` 反查注入 `X-Agent-Friend-Session-Id`，本机回环转发到 agent_bridge   | P0     |
| 通话 ↔ session 桥接         | 拨打通话时通过 agent_bridge 创建新 session（带 channel=voice）或绑已有 session_id 续上                          | P0     |
| 引擎层 channel 扩展         | session 加 channel 元字段（运行期可变）+ `channel_change` 事件 + `SystemPromptComposer` 加 ChannelSection      | P0     |
| 跨进程错误模型              | 沿 [`006 §4.5`](../006-agent-bridge/requirement.md) 拟人化兜底优先精神，定义 voice 通道下的错误呈现             | P0     |
| 启动 / 穿透 / 调试入口      | `scripts/voice/run.{sh,ps1}` + `scripts/voice/tunnel.sh` 样例 + `voice_bridge/smoke/` 最小客户端（仅 smoke 用） | P1     |

> P0 = 本期必须交付；P1 = 本期需要有，但允许极简。

---

## 3. 非目标（Out of Scope）

以下能力**本期明确不做**，避免范围蔓延：

- **真实火山 RTC 端到端 AC** —— 浏览器 smoke 在用户个人 Windows 上独立跑，**不进 AC 列表**；本期所有正式 AC 都用 mock RTC + mock LLM 跑通
- **ASR / TTS 模块抽象**（路线图 [M6 / M7](../../decisions/0001-product-vision-and-roadmap/README.md)） —— 本期 ASR/TTS 仍由火山 RTC AIGC 黑盒接管；真要换厂商时再做
- **Welcome Message 动态生成** —— spike 配的固定欢迎语沿用，按记忆/persona 动态生成留给后续
- **voice_type 与 persona 关联** —— 本期所有通话用 spike 同一个 voice_type（`zh_female_linjianvhai_moon_bigtts`）；按 persona 选音色留给后续
- **产品级前端 / 桌宠形态接入 voice** —— Phase 1 桌宠期统一处理；本期 `voice_bridge/smoke/` 仅满足"在 Windows 上跑通端到端验证"
- **云化部署 / 多租户 / 鉴权 / 速率限制 / 请求审计** —— 沿 [`006 §3`](../006-agent-bridge/requirement.md) 同精神不做；voice_bridge 仅 bind 本机
- **公网穿透工具内置 / 自动启动 cloudflared** —— 仅给 `scripts/voice/tunnel.sh` 样例，开发者自行替换为 ngrok / 其他
- **通话录音 / 音频文件持久化** —— 不做（合规 + scope）
- **同 session 通话进行中实时切 channel** —— 通话状态机由火山 RTC 管，跟 session 状态机正交；本期只在拨打/挂断时升降级 channel
- **声纹 / 用户身份识别 / 多设备通话状态同步** —— 不做
- **持久化通话状态** —— `call_id ↔ session_id` 仅在 voice_bridge 进程内存；进程重启即丢，由 surface 重新拨打

---

## 4. 核心需求详述

### 4.1 voice_bridge 控制平面

**目标**：对外暴露最小可用的 HTTP 接口，让 surface（前端 / 桌宠 / IM）能拨打/挂断通话、查询通话状态；内部封装与火山 RTC 的所有控制面交互。

需求点（用户/系统视角）：

- **R-4.1.1 拨打通话**：surface 通过 HTTP 发起拨打，body 含可选的 `session_id`（续上已有 session）、可选的 `persona` / `model`（新建 session 时使用，缺省走默认）；voice_bridge **异步**返回 `call_id` + RTC 房间加入凭证（`room_id` / `user_id` / `token` 等），surface 拿到凭证后可立即接入 RTC 房间。具体 endpoint 路径、body schema、token 形态由 `design.md` 决定。
- **R-4.1.2 查通话状态**：surface 可通过 HTTP 拉取指定 `call_id` 的当前状态。状态机至少含：`pending`（已调火山 OpenAPI、AI 还没进房）/ `ai_joined`（AI 已进房可对话）/ `talking`（用户已进房 + 说话中，本期可与 ai_joined 合并为同一状态）/ `stopped`（已挂断）/ `error`（不可恢复错误）。
- **R-4.1.3 挂断通话**：surface 通过 HTTP 挂断指定 `call_id`，voice_bridge 调火山 `StopVoiceChat` 释放资源；幂等——重复挂断不报错。
- **R-4.1.4 内部封装**：火山 OpenAPI Sign V4 签名、scenes 配置组装（继承 spike 配置形态）、RTC token 签发等，所有"火山特有"的事都在 voice_bridge 内部，**不向 surface 泄漏**。surface 只看见"打电话 / 挂电话"两个动作。
- **R-4.1.5 复用 spike 已有凭证**：本期直接消费 `.env` 中已有的 `VOLC_*` 变量（[.env.example](../../../.env.example) §"语音方案 spike" 段），不新增配置项。
- **R-4.1.6 仅 bind 本机**：voice_bridge 默认 `127.0.0.1:18900`，避免无意暴露到内网；公网穿透是开发者显式动作（启 cloudflared），不内置。

> 具体 endpoint 路径（如 `POST /voice/calls`）、状态机的内部表示、token 字段命名等由 `design.md` 决定。

### 4.2 voice_bridge LLM 入站代理

**目标**：作为火山 RTC `CustomLLM` 模式下的 **OpenAI 协议入口**——RTC 云端把对话请求打到 voice_bridge 的某个 endpoint，voice_bridge 注入 session 归属信息后转发到 agent_bridge。

需求点：

- **R-4.2.1 OpenAI 协议入口**：voice_bridge 暴露一个可被火山 RTC `LLMConfig.URL` 指向的 endpoint，**完整兼容 OpenAI ChatCompletion 协议**（流式 + 非流式都要支持）。请求路径里携带 `call_id`，让 voice_bridge 知道这次请求归属哪通通话。
- **R-4.2.2 session_id 注入**：voice_bridge 按 `call_id` 反查 `session_id`（拨打通话时已建立映射），注入 `X-Agent-Friend-Session-Id` header 后转发到 agent_bridge；agent_bridge 据此把对话写入对应 session（[`006 §4.2`](../006-agent-bridge/requirement.md) "OpenAI 可扩展字段升级"语义）。
- **R-4.2.3 流式必须支持**：voice_bridge 不允许"憋整句再返回"——SSE 流到达后立即转发，自身不引入可感知的额外延迟。这是 RTC 全双工/打断体验的硬约束。
- **R-4.2.4 完整透传**：除了注入 session_id header，voice_bridge **不**修改请求 body / 不修改响应内容；本期不做"按通话状态注入额外 system prompt"等扩展（这些是预留给未来的事，本期不做）。
- **R-4.2.5 agent_bridge 协议纯净不被污染**：voice_bridge 的存在不要求 agent_bridge 在协议上做任何让步——agent_bridge OpenAI 出口的 [`006 R-4.1.4`](../006-agent-bridge/requirement.md) "协议合规优先"承诺继续成立；voice_bridge 自己消化掉所有"通话相关的有状态语义"。

> 具体 endpoint 路径（如 `POST /voice/llm/{call_id}/v1/chat/completions`）、转发实现细节由 `design.md` 决定。

### 4.3 通话 ↔ session 桥接

**目标**：让"语音通话"成为 agent-friend session 的合法承载方式，记忆和 persona 在通话中正常发挥作用。

需求点：

- **R-4.3.1 拨打时绑 session**：voice_bridge 拨打通话时，调 agent_bridge `POST /v1/sessions` 创建新 session（body 带 `channel: "voice"`）；如果 surface 在拨打 body 里指定了 `session_id`，voice_bridge 把通话绑到该 session 而不是新建。
- **R-4.3.2 复用 session 时升级 channel**：当通话绑到一个 channel 当前不是 `voice` 的已有 session（如用户文字聊到一半切语音继续），voice_bridge 在 session 上发一个 `channel_change` 事件升级为 `voice`；此事件落 session jsonl，与 [`002`](../002-engine-session-management/requirement.md) 既有 `persona_change` / `model_change` 事件同模式。
- **R-4.3.3 挂断时降级 channel**：通话挂断时 voice_bridge 在 session 上发一个 `channel_change` 事件降级为 `text`，让该 session 后续用文字续聊时回到正常文字语义。
- **R-4.3.4 共享 sessions 存储**：voice_bridge 不直接写 session 文件——所有 session 操作都通过 agent_bridge HTTP（[`006 R-4.3.2`](../006-agent-bridge/requirement.md) 已经为 sessions 跨进程并发集成 portalocker，本期复用）。
- **R-4.3.5 失败处理**：拨打时如果创建/打开 session 失败，voice_bridge **不**调火山 OpenAPI（避免有 RTC 任务但没 session 归属的孤儿状态），直接给 surface 返回错误。

### 4.4 引擎层 channel 扩展

**目标**：让 agent 大脑能感知"当前是语音对话还是文字对话"，从而生成符合通道语义的回复（语音通道下"短句、口语化、避免 markdown"等）。

需求点：

- **R-4.4.1 session 加 channel 元字段**：[`002`](../002-engine-session-management/requirement.md) 既有的 session 模型加一个 `current_channel` 派生属性，反向扫 `channel_change` 事件（同 `current_persona` / `current_model` 模式）；老 session（无 `channel_change` 事件、`session_meta.payload` 也没 `initial_channel` 字段）默认 fallback 到 `text`，**完全向后兼容**。
- **R-4.4.2 session_meta 加 initial_channel**：新建 session 时，`session_meta.payload` 加 `initial_channel: "voice" | "text"` 字段（缺省 `text`）；agent_bridge `POST /v1/sessions` body 加 `channel` 字段透传。
- **R-4.4.3 channel_change 事件**：新增 `channel_change` 事件类型（`payload: {"to": "voice" | "text"}`），agent_bridge 暴露相应的 endpoint（具体形态 design 定）让 voice_bridge 能调用；事件类型扩展是**纯加性变更**，跟 [005](../005-engine-tool-calling-and-web-search/requirement.md) 加 tool 事件同款，**SCHEMA_VERSION 不递增**。
- **R-4.4.4 SystemPromptComposer 加 ChannelSection**：[`004`](../004-engine-system-prompt-composer/requirement.md) 既有的 section 机制加一个 `ChannelSection`，从 `session.current_channel` 读取当前通道，输出对应的 prompt 片段（`text` 通道下 section 输出空字符串，避免污染既有文字对话；`voice` 通道下输出"你正在通过语音和用户对话，请用短句、避免 markdown 格式、避免 list / table 等不适合朗读的结构"等指令）。具体 section 措辞由 `design.md` 决定。
- **R-4.4.5 channel 维度限定**：本期 `channel` 字段只承载"用户与 AI 的交流模态"维度，值域为 `voice` | `text`；surface（im / 桌宠 / web 等）是另一个正交维度，本期**不**塞进 channel。
- **R-4.4.6 公开 API 不破坏**：[`005`](../005-engine-tool-calling-and-web-search/requirement.md) / [`006`](../006-agent-bridge/requirement.md) 已经在守的 `Conversation.stream()` / `agent` 包公开 API 不动；channel 信息通过 session 元数据流转，不进入这些公开签名。

### 4.5 跨进程错误模型

**目标**：把 voice_bridge 这一层的错误以"用户能感知 + 拟人化"的形态呈现，跟 [`006 §4.5`](../006-agent-bridge/requirement.md) 在 voice 通道下保持一致体验。

需求点：

- **R-4.5.1 拟人化兜底优先**：可恢复错误（火山 OpenAPI 限流 / agent_bridge 流式中断 / 网络瞬断 / LLM 限流等）按 [`005 R-4.1.4`](../005-engine-tool-calling-and-web-search/requirement.md) / [`001 R-4.1.4`](../001-foundation-chat-and-memory/requirement.md) 思路，在已经接通的通话里 AI 用拟人语音继续（"网络好像卡了一下，你刚刚说什么？"等）；voice_bridge 不直接结束通话或暴露技术错误。
- **R-4.5.2 不可恢复错误才上报**：仅在不可恢复错误（火山 AK/SK 错 / 配置严重错误 / agent_bridge 整个不可达 / RTC 房间创建失败等）时，控制平面 HTTP 接口才返回 4xx/5xx 错误给 surface。
- **R-4.5.3 错误信息不暴露技术内幕**：surface 可见的 error message 必须是用户语言（"通话服务暂时不可用，请稍后再试"），不暴露 Python 异常类名 / 火山 OpenAPI 错误码 / AK 片段等。
- **R-4.5.4 不污染会话历史**：拟人兜底说出的话**该落 session 仍照常落**（与 [005 R-4.1.4](../005-engine-tool-calling-and-web-search/requirement.md) 一致）；纯技术错误（HTTP 状态、堆栈）记日志但不进 session 事件流。
- **R-4.5.5 通话与会话语义解耦**：通话失败/结束**不**自动删除关联的 session——session 是用户的资产（[`0001`](../../decisions/0001-product-vision-and-roadmap/README.md) "记忆是第一护城河"），只通话状态结束。

### 4.6 启动 / 穿透 / 调试入口

**目标**：让开发者能用 `scripts/` 一行起 voice_bridge，并提供本期 smoke 验证所需的最小客户端。

需求点：

- **R-4.6.1 启动脚本双端**：`scripts/voice/run.{sh,ps1}`（mac/linux + windows），按 [`cross-platform-dev`](../../../.cursor/rules/cross-platform-dev.mdc) 规范双端覆盖；登记到 [`scripts/README.md`](../../../scripts/README.md)。
- **R-4.6.2 公网穿透样例**：`scripts/voice/tunnel.sh` 提供 cloudflared 启动样例（仅 mac/linux，按 cross-platform-dev §"双端覆盖的例外"——开发者用 windows 跑端到端 smoke 时如果用 ngrok 等其他工具，自行参考样例）。`tunnel.sh` 头部注释要明确"产品化部署不要用此脚本"。
- **R-4.6.3 smoke 客户端**：`voice_bridge/smoke/` 下提供一个最小 HTML+JS 文件（用火山 RTC Web SDK CDN），调用 voice_bridge 的拨打/挂断接口、加入 RTC 房间、显示通话状态。**仅供开发者在合规环境跑端到端 smoke 用**，不是产品级前端、不进 AC 列表、README 顶部要写明"非产品代码"。
- **R-4.6.4 spike web 不动**：[`experiments/voice-poc/rtc-aigc-demo/Web/`](../../../experiments/voice-poc/rtc-aigc-demo/Web/) 保持原状，不为本期做任何改造；本期 `voice_bridge/smoke/` 是独立的最小客户端，与 spike web 互不依赖。

---

## 5. 关键体验原则

引用 [`0001 §1.3`](../../decisions/0001-product-vision-and-roadmap/README.md) 三条核心原则，本期具象化为：

1. **像真人，不像工具**（与 §4.4 R-4.4.4 / §4.5 R-4.5.1 / R-4.5.3 直接对应）
   - `ChannelSection` 让 AI 在语音通道下说话风格自然贴近真人（短句、口语化）
   - 错误兜底沿 [005 R-4.1.4](../005-engine-tool-calling-and-web-search/requirement.md) 拟人化精神，通话过程中的可恢复错误用语音继续，不暴露技术细节
   - surface 拿到的 error message 是用户语言，不含 Python 异常名 / 火山 API 错误码

2. **记忆是第一护城河**（与 §4.3 / §4.4 直接对应）
   - 通话默认绑 session，**不像 spike Phase 0 那样用纯无状态 LLM**——用户语音里聊的事下次文字对话能记得
   - `channel` 元字段本身也是隐性记忆（"AI 知道我们之前是打电话还是打字"）
   - 通话失败/结束**不**自动删除 session（R-4.5.5）

3. **底层可替换，上层稳定**（与 §4.2 / §4.3 / §4.4 R-4.4.6 直接对应）
   - voice_bridge 这一层把"火山 RTC 厂商耦合"封死，未来 surface 接入只看 voice_bridge 的 HTTP；将来要换 RTC 厂商，只换 voice_bridge 内部，对外契约和上层不动
   - agent_bridge / agent 公开 API 不动；channel 扩展通过 session 元数据流转，不进入 `Conversation.stream()` 公开签名
   - voice_bridge 的对外契约（HTTP 接口 / 通话状态机 / 错误模型 / channel 语义）一旦敲定就是长期承诺，未来下游需求只能扩展不替换

---

## 6. 验收标准

> 本期 AC **不**包含真实火山 RTC 端到端验收——那需要真实账号 + 公网穿透 + 浏览器，跟 AC 的"mock + 可重复"原则冲突。本期所有 AC 都能在公司电脑上**纯本地 + mock**跑完。
>
> 浏览器端到端 smoke 在 §6.10 单独标注，作为合并 main 前的部署冒烟项，不进 AC 列表。

- **AC-1 控制平面拨打通话**：用 `respx` 或等价工具 mock 火山 OpenAPI，向 voice_bridge 拨打通话接口发请求，返回 `call_id` + RTC 加入凭证；mock 端校验 voice_bridge 发出的 `StartVoiceChat` 请求是合法 V4 签名（必含 `X-Date` / `Authorization` 等 V4 必填头）。
- **AC-2 控制平面挂断通话**：拨打成功后调挂断接口，mock 端校验 voice_bridge 发出的 `StopVoiceChat` 用同一个 `TaskId` 调过；重复挂断不报错（幂等）。
- **AC-3 通话状态机**：拨打→ai_joined（mock 模拟 AI 进房回调）→stopped 状态流转可观察；不可恢复错误进入 `error` 状态。
- **AC-4 LLM 入站代理 session 注入**：发起一通 mock 通话拿到 `call_id`，向 voice_bridge LLM endpoint 打 OpenAI 流式请求，启 mock agent_bridge（或真 agent_bridge + mock 上游 LLM）校验：（a）voice_bridge 转发的请求带正确的 `X-Agent-Friend-Session-Id` header；（b）SSE 流原样回到调用方；（c）voice_bridge 自身不修改 body / 不修改 SSE 内容。
- **AC-5 channel 字段贯穿**：voice_bridge 拨打通话（不传 session_id）时，agent_bridge 落出来的 session jsonl 首行 `session_meta.payload.initial_channel == "voice"`；该 session 在通过 `agent-cli` in-process 模式打开时，`current_channel == "voice"`、system prompt 含 ChannelSection 输出的语音通道指令。
- **AC-6 channel 互切（语音 ↔ 文字）**：
  - 用 `agent-cli` in-process 模式创建一个文字 session（channel 默认 `text`）；voice_bridge 拨打通话时传入该 session_id，**不**新建 session，而是在该 session 上发 `channel_change` 事件升级为 voice；session jsonl 落了一条 `channel_change` 事件；`current_channel == "voice"`；system prompt 切换到语音通道指令
  - 挂断后 voice_bridge 发 `channel_change` 事件降级回 `text`；该 session 后续在 `agent-cli` 文字模式下打开，`current_channel == "text"`、system prompt 回到正常文字
- **AC-7 跨进程错误兜底**：mock 火山 OpenAPI 限流（返回错误码），voice_bridge 在已接通的通话里通过 LLM proxy 让 AI 用拟人话术继续；mock LLM 流过程中断（agent_bridge 侧），voice_bridge 不暴露技术细节给 surface。
- **AC-8 既有不退化**：[`001 ~ 006`](../) 已交付的所有 AC 全部继续可用，agent-cli in-process 模式不受影响；老 session 文件（无 `initial_channel` 字段、无 `channel_change` 事件）仍然能正常读取，`current_channel` 默认 fallback 到 `text`。
- **AC-9 §3.19 / spike 不违反**：本期落地后 `data/` 下没有新增持久化子目录；`experiments/voice-poc/rtc-aigc-demo/` 没有新增改动；spike 留下的 `.env` 中 `VOLC_*` 变量被复用，不新增同维度配置。
- **AC-10 启动脚本 + smoke 客户端就位**：`scripts/voice/run.{sh,ps1}` 双端可启动；`scripts/voice/tunnel.sh` 在 mac/linux 上能跑出 cloudflared 公网 URL；`voice_bridge/smoke/` 包含可访问的 HTML 客户端。

### 6.10 浏览器端到端 smoke（不进 AC，独立确认）

由开发者在自己的 Windows 电脑（合规允许公网穿透的环境）上跑：

1. clone `feature/007-voice-call` 分支
2. 启 agent_bridge（`scripts/bridge/run.ps1`）
3. 启 voice_bridge（`scripts/voice/run.ps1`）
4. 启 cloudflared（`scripts/voice/tunnel.sh` 的 windows 版或开发者自行起隧道）
5. 把 cloudflared URL 配进 voice_bridge 配置（具体怎么配 design 决定）
6. 浏览器打开 `voice_bridge/smoke/index.html`（或 `http://127.0.0.1:18900/smoke/`），点拨打通话
7. 验证：能听到 AI 说话、AI 回复体现 persona / 之前文字对话的记忆、挂断能干净结束

smoke 通过 + 公司电脑 AC 全跑通 + 用户验收 → 合并 main。

---

## 7. 开放问题 / 待技术文档决策

> [`0001`](../../decisions/0001-product-vision-and-roadmap/README.md) / [`0002`](../../decisions/0002-incubation-tech-stack/README.md) 中已锁定的项目级技术栈与决策不重复。
> 以下问题属于**本需求的实现策略**，**不在本需求文档中拍板**，将在同目录 `design.md` 中讨论与决策：

- **Q-1 voice_bridge HTTP 接口具体形态**：拨打/挂断/查状态的 endpoint 路径、HTTP 方法、body schema、token 字段命名（如 `room_token` vs `rtc_token`）
- **Q-2 火山 OpenAPI Sign V4 实现选型**：手写 HMAC-SHA256 vs 用 `volcengine-python-sdk` vs 抄 spike 的 Node 实现翻译成 Python——哪种依赖最轻、维护性最好
- **Q-3 通话状态机内部表示**：状态保存形态（dataclass / pydantic / 仅 dict）；`pending → ai_joined` 的触发是 voice_bridge 主动轮询还是火山有回调通知
- **Q-4 call_id ↔ session_id 注册表实现**：纯内存 dict + asyncio.Lock？进程重启的容错策略（spike 的"等 idle timeout"还是主动 stop 所有未完成通话）
- **Q-5 LLM 入站代理 endpoint 路径形态**：`/voice/llm/{call_id}/v1/chat/completions`（call_id 作 path 参数）vs `/voice/llm/v1/chat/completions` + query string `?call_id=xxx`——哪种与火山 RTC `LLMConfig.URL` 配置兼容性最好
- **Q-6 ChannelSection 输出措辞**：voice 通道下具体输出什么 prompt 片段；section 在 SystemPromptComposer 的拼装顺序（在 persona section 之前还是之后）
- **Q-7 channel_change 事件的 agent_bridge HTTP 端点**：是新增 `POST /v1/sessions/{id}/channel` 还是复用既有的 session 切换接口模式（[006 §4.4 R-4.4.2](../006-agent-bridge/requirement.md) persona / model 切换那套）
- **Q-8 voice_bridge 复用 spike scenes 配置的方式**：是把 spike 的 `Custom.json` 完整 inline 到 Python 代码里（`@dataclass` / dict literal），还是把 scenes 作为单独 JSON 文件读？只用其中关键字段，避免 spike scenes 里的硬编码（如 spike 的 `EnableConversationStateCallback` / `Mode=ArkV3` 等）污染产品代码
- **Q-9 错误码命名 / 拟人话术清单**：voice_bridge 各种错误（火山限流 / agent_bridge 不可达 / 房间创建失败 / 会话创建失败等）映射到什么 HTTP 状态码 / error code；LLM proxy 中转过程中的 AI 拟人兜底由谁说（agent 通过 prompt fallback 还是 voice_bridge 在 LLM 流前面塞预制 TTS 文本）
- **Q-10 smoke 客户端的 RTC SDK 接入方式**：Web SDK CDN 链接 / 版本锁定；smoke 客户端是否需要显示 `===VPOC-TIMELINE===` 等延迟埋点（参考 spike）
- **Q-11 voice_bridge 与 agent / agent_bridge 的依赖方向**：作为顶层独立 uv 项目，依赖 agent_bridge 是 path dep + workspace 还是只走 HTTP？需不需要 import agent 公开类型（如 `Channel` 字面量）做静态校验

---

## 8. 变更记录

| 日期       | 变更内容        | 影响范围 |
| ---------- | --------------- | -------- |
| 2026-05-25 | 初版确认通过    | 全文     |

---

## 文档元信息

- **创建时间**：2026-05-25
- **确认时间**：2026-05-25
- **下一步**：撰写同目录的 `design.md`（技术方案）
