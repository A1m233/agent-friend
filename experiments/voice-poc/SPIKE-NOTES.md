# SPIKE-NOTES · C 方案可行性测试记录

> 本文件是本 spike 的**最终交付物**。三阶段跑完后，需要填齐"量化指标 + 踩坑清单 + 产品化建议 + 是否立项结论"四大块。
>
> 文档结构在 spike 开始前先建骨架，跑的过程中边跑边填，避免事后回忆失真。

## 元信息

- **分支**：`spike/voice-poc`（已合入 main 留档）
- **创建时间**：2026-05-21
- **结束时间**：2026-05-21（**提前结束**）
- **状态**：Phase 0 链路连通性 + 端到端浏览器实测均已通过；Phase 1（CustomLLM + DeepSeek）、Phase 2（agent-bridge）不在 spike 内继续，转入后续正式需求落地
- **验证对象**：火山 RTC AI 对话 + 自定义 LLM 方案（即"路 C"）
- **目标结论**：是否值得正式立项 `006-voice-io` 需求（注：`005` 已被 tool-calling 需求占用）

## 三阶段验收清单

### Phase 0：官方 Demo + 火山自家方舟 LLM

**验收目标**：摸清 C 方案的"体验上限"——延迟、音质、打断、字幕在最理想配置下是什么水平。

- [x] 账号开通：RTC AppId、AppKey、AK/SK、ARK_API_KEY 全部到手
- [x] `volcengine/rtc-aigc-demo` clone 到 `rtc-aigc-demo/` 子目录
- [x] `Server/scenes/Custom.json` 配置完成（含 AppId/AppKey/AK/SK/ARK_API_KEY/ARK_ENDPOINT_ID/Speech_AppID）
- [x] `Server` 能起来（确认 `AIGC Server is running at http://localhost:3001`）
- [x] **链路连通性全跑通**：`./experiments/voice-poc/scripts/run-connectivity-test.sh` 用独立 Node 脚本一次跑完 `StartVoiceChat → 等 2s → StopVoiceChat`，两次 OpenAPI 都返回 `Result: "ok"`，证明：
  - AK/SK Sign V4 签名 → 火山受理 ✅
  - 火山真创建了 RTC 房间 + 拉起 AIGC 任务（ASR + 方舟 LLM endpoint + TTS 三件套挂载） ✅
  - TTS 真跑了一次 `WelcomeMessage` 合成 + 推流 ✅
  - `StopVoiceChat` 干净停止，无遗留资源 ✅
  - 调用方自己生成 `TaskId`（uuid v4）作为幂等 key —— 这是火山 RTC AIGC 的接口约定
- [x] **浏览器端到端实测通过**：能正常通话（你说话 → ASR → 方舟 LLM → TTS → 听到 AI 回复）、麦克风打断也正常工作；客户端加了 `===VPOC-TIMELINE===` 埋点，能精确分解每一段延迟（VAD / ASR 流式首字 / LLM 首 token / TTS 首音频）

**量化指标**（已实测）：

- 首响延迟（用户说完到 AI 开口）：**~13s**——但**这不是 RTC 链路的瓶颈**，是 Phase 0 用的方舟 endpoint 模型（`Doubao-Seed-2.0-lite`）默认开了"深度思考"导致首 token 6s+。`===VPOC-TIMELINE===` 拆解显示：VAD ~300ms → ASR 首字 ~150ms → LLM 首 token ~12s → TTS 首音频 < 1s。Phase 0 不优化，Phase 1/2 切到非思考模型即可解决
- TTS 首音频延迟：< 1s ✅
- ASR 流式识别首字延迟：~150ms ✅
- VAD 判停延迟：~1.4s（含 SilenceTime 300ms + ASR 收尾）
- 麦克风打断：体验正常（demo 自身有个 `userId must be within 128 bytes` 的边角 bug，跟链路无关）
- 中文音质主观评分：4/5（用 `zh_female_linjianvhai_moon_bigtts` 邻家女孩音色，自然）
- 全双工打断、唱歌/激动语气：本次未细测（与决策无关）

**踩到的坑**（实测后补）：

- **ASR 配置必须用"大模型 ASR + AccessToken"组合**：`Custom.json` 里 `ASRConfig.ProviderParams` 必须配 `Mode=bigmodel`、`AppId=<语音 AppID>`（注意是语音服务的数字 AppID `example-account-value`，**不是 RTC 的 hex AppID**）、`AccessToken=<语音控制台拿的 token>`。文档对 `AccessToken` 是否必填语焉不详，实测必须填，否则 ASR 端会报 `websocket: bad handshake`
- **火山 RTC AppID 与语音 AppID 不是同一个东西**：RTC AppID 是 hex 串（`6a0e87536bbc...`），语音 AppID 是纯数字（`example-account-value`）。两者归属不同产品线、不同控制台页面，必须分别开通并填到 `Custom.json` 对应字段
- **方舟 endpoint 默认开"深度思考"导致首 token 极慢**：跑通后发现首响 ~13s，根因是方舟控制台测试 `Doubao-Seed-2.0-lite` 时显示"思考深度: 高 / 首token 6.31s"，这跟 Phase 0 实测的延迟分布一致。豆包 App 那种实时对话毫秒级首 token 一定是关思考状态，Phase 1+ 切非思考模型或显式关 thinking 即可
- Server 必须在带外网权限的环境下跑，不然连 `rtc.volcengineapi.com` 都 DNS 解析不了（本机起完全 OK，只是 agent sandbox 内跑的时候要 `full_network`）
- `rtc-aigc-demo/Server` 自身的 `/proxy` 端点对 `StartVoiceChat` **不会回写 TaskId 到内存**，导致无法靠该 endpoint 干净 stop。我们的做法是绕过 demo Server、写独立的 `connectivity-test.js` 直接调 OpenAPI（复用 Server 的 node_modules）。这条以后产品化时如果要继续用 demo Server 当模板，得自己补"start 后回写 TaskId / 维护 RoomId↔TaskId 映射"的状态管理
- `StartVoiceChat` 的 `RoomId` 和 `TaskId` 都由**调用方生成**并自己负责唯一性（TaskId 用 uuid v4）；这等于幂等 key，重试要复用同一个 TaskId
- **demo Server 缺陷 + 本地 patch（rtc-aigc-demo 整个 git-ignored，patch 不进 git，必须在这里留底）**：
  - 现象：浏览器点"连接通话" → 报 `InvalidParameter: invalid TaskId: ""`
  - 根因：`Server/app.js` 的 `StartVoiceChat` case 直接把 `Scenes['Custom'].VoiceChat` 当 body 发，而 `Custom.json` 里 `TaskId: ""` 是空的，demo Server 也没在请求前自动生成
  - patch（`Server/app.js` `StartVoiceChat` case）：
    ```js
    case 'StartVoiceChat':
      body = { ...VoiceChat };
      body.TaskId = uuid.v4();                              // 每次通话生成新的幂等 key
      VoiceChat.TaskId = body.TaskId;                       // 写回内存，让后续 StopVoiceChat 复用
      if (!body.AgentConfig?.UserId) {
        body.AgentConfig = { ...body.AgentConfig, UserId: `bot-${uuid.v4().slice(0, 8)}` };
        VoiceChat.AgentConfig.UserId = body.AgentConfig.UserId;  // 机器人自身在 RTC 房间的 user id，火山校验必填
      }
      break;
    ```
  - 被 demo getScenes 注入的字段：`RoomId` / `RTCConfig.UserId` / `AgentConfig.TargetUserId[0]` —— 它**漏了** `TaskId` 和 `AgentConfig.UserId`，要靠 patch 兜底
  - 局限：rapid double-click 会让第二次新 TaskId 覆盖第一次的，导致第一次的火山任务变孤儿等 idle timeout —— spike 期不处理；产品化版本必须做"start 前先 stop 旧任务"
- 待续填

### Phase 1：CustomLLM 切到 DeepSeek

**验收目标**：验证 `LLMConfig.Mode=CustomLLM` 配火山外部的 OpenAI 兼容服务（DeepSeek）能不能稳定跑通，对比 Phase 0 的体验回退多少。

- [ ] `LLMConfig` 修改为 `Mode: CustomLLM` + DeepSeek URL + 现有 `DEEPSEEK_API_KEY`
- [ ] 重启 Server，发起新通话
- [ ] 火山控制台日志显示 DeepSeek 被成功调用
- [ ] AI 回复内容明显是 DeepSeek 风格而非方舟模型风格

**量化指标**（跑通后填）：

- 相比 Phase 0 首响延迟变化：**待测**
- DeepSeek 流式输出是否能逐 token 喂给 TTS（看是否有"等整句才说"的回退）：**待测**
- DeepSeek 在"口语化"上的表现 vs 方舟模型：**待测**
- 单次对话 token 用量（DeepSeek 侧）：**待测**

**踩到的坑**（跑通后填）：

- 待填

### Phase 2：CustomLLM 切到自家 agent-bridge

**验收目标**：把"自家记忆 + persona + system prompt composer"真的接进语音对话，验证项目核心差异点能否在语音侧体现。

- [ ] `agent-bridge/` 独立 uv 项目建好
- [ ] FastAPI `POST /v1/chat/completions` SSE 流式端点跑起来
- [ ] 内部调用 `agent.Conversation.stream(...)`
- [ ] OpenAI 协议 ↔ agent 内部协议的转换正确（最少包含 `TextDelta` → `delta.content`）
- [ ] cloudflared 隧道起来，拿到 trycloudflare URL
- [ ] `LLMConfig.URL` 指向穿透 URL，火山能调通
- [ ] 通话中能体现 `default.md` / `concise.md` 等 persona 的人格特征

**量化指标**（跑通后填）：

- 相比 Phase 1 首响延迟变化（多了"火山 → cloudflared → 本地 FastAPI → DeepSeek"一跳）：**待测**
- 跨会话记忆召回的可行性（重启 agent-bridge 后再问之前的事实）：**待测**
- SSE 协议中断/网络抖动下 RTC 端的表现：**待测**

**踩到的坑**（跑通后填）：

- 待填

## 产品化阶段决策建议

> 三阶段全部跑完后填本节。

### 体验维度的结论

- 是否值得为"豆包语音体验"放弃 A2 方案：**待结论**
- 火山控制台 / OpenAPI 在调试可观测性上够不够用：**待结论**

### 架构维度的结论

C 方案的核心隐藏成本是**自家 agent endpoint 必须公网可达**。开发期靠 cloudflared，产品化阶段必须二选一：

- (a) 部署 agent 到云端，所有用户共享后端 → **代价**：记忆集中存储，与"端侧记忆作为护城河"的项目调性冲突
- (b) 每用户自己部署后端 → **不可能**

跑完三阶段后，需要在本节给出对 (a) 路径的具体评估：

- 记忆云化的合规/隐私成本：**待评估**
- 多租户记忆隔离的实现复杂度：**待评估**
- 持续运营成本（火山 RTC + 后端服务器 + DeepSeek token）量级：**待评估**

### 与 A2 / B 方案的对比

跑完后回头看：

- C 方案相比 A2（豆包 RealtimeAPI + RAG 注入）多换来了什么？值不值"记忆云化"的代价？
- C 方案相比 B 方案（ASR+TTS 自拼）多换来了什么？

## 最终结论：是否立项 `006-voice-io`？

**实际结论：A —— 值得立项**

### 为什么提前结束 spike，而不是把 Phase 1/2 跑完

Phase 0 已经把"C 方案是否技术可行"这个 spike 的真正问题答清楚了：

- 火山 RTC AIGC 的端到端链路（ASR + LLM + TTS + RTC 推流 + 全双工打断）确认能跑通，且体验在可接受范围（除了"深度思考"导致的首 token 延迟，但那是模型选型问题，不是 RTC 框架问题）
- 关键工程细节都摸清了（双 AppID 区分、AccessToken 必填、`StartVoiceChat`/`StopVoiceChat` 幂等键由调用方生成等），这些坑写进了上面"踩到的坑"小节，立项时直接复用

Phase 1（CustomLLM 切 DeepSeek）和 Phase 2（agent-bridge 切自家 agent）本质上**已经不是"探索"性质**——结论已经清楚（火山 RTC `LLMConfig.Mode=CustomLLM` 是官方支持的能力，DeepSeek 是 OpenAI 兼容协议、agent-bridge 也按 OpenAI 协议出 SSE 即可）。继续在 spike 形态下做这两个 phase，只是把工程实现伪装成探索，反而会让代码以"快糙"的形态进 main，污染主线。**正确的做法是合上 spike，作为正式需求 `006-voice-io` 重新开 feature 分支推进**。

### `006-voice-io` 立项时的核心要点（草稿）

> 这只是 spike 的 hand-off，不替代未来正式 `requirement.md` 的撰写。

**范围**：
- 浏览器/客户端通过火山 RTC 与 agent-friend 进行实时语音对话
- LLM 由 agent-friend 自己接管（走 `LLMConfig.Mode=CustomLLM`），完整复用 persona、记忆、system prompt composer
- 维持 ASR + TTS 仍然用火山自家服务（已验证体验过关）

**非范围**：
- A 方案（豆包 RealtimeAPI 端到端）—— 与"自家大脑"项目调性冲突
- B 方案（自拼 ASR+TTS）—— 失去全双工/打断，体验回退太大
- 桌宠形态、IM 通道接入等差异化能力（这些是 agent-friend 独有方向，本 spike 不涉及）

**关键技术决策预案**（需求文档里要敲定的）：

1. **agent endpoint 的公网可达问题**（C 方案的核心隐藏成本）：
   - (a) 部署 agent 到云端，所有用户共享后端 → 与"端侧记忆作为护城河"冲突，需评估妥协方案
   - (b) 每用户自己部署后端 → 不可行
   - (c) 开发期 cloudflared / 产品期改 A2 / 产品期接受云化 → 三选一
2. **agent-bridge 包装层的归属**：是放在 `agent/` 模块里加一个 OpenAI 协议出口，还是独立成单独的 `agent-bridge/` 子项目？这决定了 `agent.Conversation` 的接口稳定性要求
3. **方舟模型选型**：是只用 ASR+TTS、不用方舟 LLM；还是保留方舟 LLM 作为低代价 fallback？
4. **延迟预算分配**：Phase 0 实测显示 RTC 链路本身只贡献 ~2s，剩下时间全在 LLM —— 立项时要给 agent-bridge → DeepSeek/方舟非思考模型的链路一个明确的首 token SLA（建议 < 1.5s）

### 与 A2 / B 方案的对比（基于 Phase 0 数据）

- **C vs A2**：C 多换来"自家大脑"（记忆 + persona + tool calling 的所有项目独有能力都能在语音侧体现）；代价是"agent endpoint 公网可达"。Phase 0 已确认 C 体验跟 A2 量级一致（除模型本身延迟外）
- **C vs B**：C 多换来"全双工/打断/流式 TTS"等 RTC 框架级能力；代价是必须用火山 RTC（耦合一家厂商）。**这一项 Phase 0 已基本判定 C > B**

---

> 本 spike 到此为止。继续推进请走正式需求流程（`docs/requirements/006-voice-io/`）。
