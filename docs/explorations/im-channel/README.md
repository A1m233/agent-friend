# Exploration · IM 通道接入

> 沉淀"agent-friend 接入即时通讯"方向的多轮讨论与一次落地 spike。本文是当前快照，会随讨论推进追加 / 修订。
>
> 与项目愿景的关系：
> - 对应 [`docs/decisions/0001`](../../decisions/0001-product-vision-and-roadmap/README.md) 的 **M8 · IM 通道**
> - 关联 spike：[`experiments/qq-bot-poc/`](../../../experiments/qq-bot-poc/)（首条接入路线已实测可行）
>
> 当前快照：2026-06-17 · 首轮探索 + spike 落档

---

## 1. 问题边界

agent-friend 的产品形态是**本地常驻桌宠 + 陪伴型 AI**。一旦想让用户"在 IM 里也能跟 TA 聊天"或"在 IM 里收到 TA 的提醒"，就要打通某条 IM 通道。

本轮探索回答两个问题：

1. **接哪个平台**：QQ / 微信 / 飞书 / Telegram / Discord / 钉钉 / 自建 App / 小程序——每条路是什么形态、对 agent-friend 的合身度
2. **怎么接**：技术上的协议端 / SDK / gateway 架构怎么落

—并提前给一些**容易被忽略的硬约束**画上线：

- **Local-first**（项目铁律）：agent 跑在用户本机，不部署后端服务器。这条直接 disqualify 任何"必须公网回调"的接入方式
- **仅"人对人"**（用户本轮明确）：暂不考虑群聊场景。这条直接 disqualify 一批"群聊优先"的合规复杂度
- **跨平台桌面形态**（Tauri 2 · macOS / Win）：决定了端上 IM 客户端能否复用某个生态

---

## 2. 候选平台横向对比

四条候选纵向铺开看，避免一开始就锁死路线。

### 2.1 平台特征矩阵

| 平台 | 注册门槛 | 单聊 Bot 能力 | 国内访问 | 富交互 | local-first 兼容 | 个人主体可用 |
|---|---|---|---|---|---|---|
| **QQ 官方 Bot OpenAPI** | 个人实名 → 几分钟拿凭据 | ✅（创建者专属模式） | ✅ | ⭐⭐⭐⭐ | ✅（WebSocket gateway 出站长连） | ✅ |
| **QQ NapCat（OneBot）** | 本地跑 NapCat 进程 + 扫码登录 | ✅（个人号代理） | ✅ | ⭐⭐⭐⭐ | ✅ | ✅，但灰色 |
| **微信 wechaty 系** | puppet 选项各有坑 | ⚠ 个人号代理路径不稳 | ✅ | ⭐⭐⭐ | ✅ | ⚠ 封号风险高 |
| **飞书自建应用机器人** | 个人可免费建租户 | ✅ 1v1 完整支持 | ✅ | ⭐⭐⭐⭐（飞书卡片） | ⚠ 需 outbound 长连模式 | ✅ |
| **Telegram Bot** | BotFather 1 分钟 | ✅ | ❌ 必须代理 | ⭐⭐⭐⭐⭐（Mini App） | ✅（getUpdates 长轮询） | ✅ |
| **Discord Bot** | Developer Portal | ✅ DM 受限 | ❌ 必须代理 | ⭐⭐⭐⭐⭐ | ✅（Gateway WebSocket） | ✅ |
| **钉钉机器人** | 偏企业 | ⚠ | ✅ | ⭐⭐⭐ | ⚠ | ⚠ |
| **微信公众号 / 客服** | 需公网回调 | ⚠ | ✅ | ⭐⭐⭐ | ❌ | ⚠ |
| ~~小程序~~ | — | — | — | — | ❌ | — |
| ~~自建 IM App~~ | — | — | — | — | — | — |

### 2.2 几个关键 disqualify

- **微信公众号 / 客服 / Web Hook 类**：要求公网 endpoint 接回调 → 违反 local-first → 先砍
- **小程序**：没有"桌宠常驻"概念，关掉就消失，**产品形态完全错配**——如果未来要做"试用 / 获客入口"是另一个话题，但不能替代 IM 接入
- **自建 IM App**：拆开看是两件事——
  - "agent-friend 的移动端"（你跟自己的 AI 在手机上聊）→ 工作量小，可做但跟"接 IM" 不是同一问题
  - "替代 QQ/微信 的独立社交 IM" → 网络效应几乎不可能从零做，强烈不建议

### 2.3 收敛

不去掉的之中 ranking：

1. **国内主用户群体 + local-first** → **QQ** 是最自然的（合规路线 + 灰色路线都有）
2. **次选 + 国内合规体面** → **飞书**（应用机器人能力强，需要长连 Stream 模式来满足 local-first）
3. **国际化预留** → **Telegram**（接入最丝滑，需代理）

**首条接入定为 QQ**。飞书 / Telegram 留作下一阶段评估。

---

## 3. QQ 路线的两条岔路

QQ 内部还有两条根本不同的路：

### 3.1 路线 A · QQ 官方 Bot OpenAPI（合规）

- **Bot 是独立账号**（有自己的 AppID/Secret/openid）
- 用户**主动加 Bot 为好友**后才能私聊
- 走 WebSocket gateway 接收消息，REST API 回消息
- 截图实测：QQ Bot 管理端有"**该机器人仅供创建人使用，暂不支持进入群聊**"的"创建者专属"模式——跟 agent-friend "只人对人"的需求完美契合
- 合规、不会封号；产品化阶段如要扩展到多用户公域，要走 IP 白名单 / URL 域名 ICP 备案 / 公域审核 的完整流程

### 3.2 路线 B · NapCat（OneBot 协议端，灰色）

- **登录用户自己的 QQ 号**（你看到的"加机器人"其实是把你的 QQ 借给本地 agent 代驾）
- 本地跑 NapCat 进程，扫码登录后接管 QQ 会话
- 跟用户朋友的真实 QQ 互动（"agent-friend 替我回消息"形态）
- 灰色，封号风险存在；优势是能直接接入用户已有社交关系

### 3.3 决策

**首条接入选路线 A**：

- 合规、零封号风险，产品化路径清晰
- 开发体验好（官方 SDK 生态完善）
- 截图里"创建者专属"模式 + 仅人对人的产品定位 = **不需要走任何重合规流程**
- NapCat 留作产品化阶段"是否需要替用户社交"决定后再评估，不是首条要走的路

---

## 4. 一个关键的认知刷新：原来"IP 白名单"不挡 dev 模式

QQ Bot 文档原话："**对于新增机器人，平台默认启用 IP 白名单功能**"。一开始解读为"任何 OpenAPI 调用都要白名单"——这对 local-first 是 deal-breaker（每个用户家里的公网 IP 都不一样，没法预先填）。

但仔细读完整句："**只有填写了 IP 白名单才能提审和上线**"——它**只挡"提审 / 上线"的门禁，不挡 dev 模式 WebSocket 连接**。

实测验证（spike `experiments/qq-bot-poc/`）：本地动态家庭 IP 直接 `qq-botpy.Client.run()` 连 gateway，鉴权通过、心跳启动、收发消息全跑通。**之前最担心的 deal-breaker 被实测推翻**——这是路线 A 能成立的最关键发现。

---

## 5. 同品类项目调研：OpenClaw / Hermes

调研目标：参考它们的 gateway 架构，搞清楚"AI agent ↔ IM 通道"这一层别人怎么解。

### 5.1 真身核对

直接打 GitHub API 拉的元数据（`api.github.com/repos/...`）：

| 项目 | 真实身份 | star | 关键事实 |
|---|---|---|---|
| **OpenClaw** | `openclaw/openclaw` · Node.js 个人 AI 助理 | **378k** | OpenAI / GitHub / NVIDIA 是 sponsor。MIT。runtime: Node 22+/24 |
| **Hermes Agent** | `NousResearch/hermes-agent` · Nous Research 出品 | **195k** | "The agent that grows with you"；NVIDIA NemoClaw 提供安全沙箱 |
| **`@tencent-connect/openclaw-qqbot`** | OpenClaw 的 QQ channel plugin · 腾讯官方维护 | 1.5k | 单聊 / 群聊 / 富媒体 / 双 transport / 内联键盘审批 |

调研中走过的弯路：第一轮 WebSearch 一直 400 错误，Bing 返回的疑似 AI 摘要把 OpenClaw 描述成"由 Peter Steinberger 构建"——一查 Steinberger 是 PSPDFKit 的真实开发者但跟 AI agent 无关，**判定那是 Bing AI 摘要的 hallucination，不能信**。后改用 `curl + GitHub API` 直读元数据才拿到真实信息。

> **教训**：搜索引擎的 AI 摘要在低 SEO / 新名词场景下倾向编造，需要交叉验证。GitHub API 直读 metadata 是低成本、高可信的兜底通道。

### 5.2 OpenClaw README 原话：核心架构哲学

> "**OpenClaw is a personal AI assistant you run on your own devices. It answers you on the channels you already use.**"
>
> "**The Gateway is just the control plane — the product is the assistant.**"

### 5.3 plugin 契约（从 `package.json` 实读）

`@tencent-connect/openclaw-qqbot` 的 `package.json` 里直接拿到：

```json
"openclaw": {
  "id": "openclaw-qqbot",
  "extensions": ["./preload.cjs"],
  "channel": {
    "id": "qqbot",
    "label": "QQ Bot"
  }
}
```

加上 plugin 自己 README 的一句点睛：

> "This plugin serves as a **message channel only** — it relays messages between QQ and OpenClaw. Capabilities like image understanding, voice transcription, drawing, etc. depend on the **AI model + skills installed in OpenClaw**, not on this plugin."

### 5.4 沉淀出的可复用原则

直接借鉴：

1. **Gateway = 控制面，不是产品本身**：所有"懂事"的能力（人格、记忆、tool、人设）都在 agent 核心；gateway 只负责协议翻译 + 长连维持
2. **Channel adapter 只翻译，不夹逻辑**：channel 是薄薄的协议翻译层，不感知任何 AI 概念
3. **统一 InboundEvent shape**：`chat_scope` (c2c/group/guild) + `chat_id` + `user_name` + `content` + `message_id` —— 这个抽象让"换一个 IM 平台"等于"换一个 adapter"
4. **长连进程跟 agent 同生共死**：OpenClaw 用 launchd/systemd daemon；agent-friend 复用现有 `BridgeRuntime` lifespan 即可，不另起 daemon

明确**不照抄**：

- 不做 `plugins install` 的 npm-style 注册系统（OpenClaw 是消费级产品，要给非开发者图形化安装；agent-friend 当前阶段直接代码注册足够）
- 不抽独立 daemon 进程（agent_bridge 已经是长连 uvicorn 进程）

---

## 6. SDK 选型：经实测多轮反复

这一节如实记录走过的弯路，因为踩坑过程本身有价值。

### 6.1 第一选：`qq-botpy`（spike 用的）

- 社区流行，PyPI 月下载 **236,134**，869 GitHub star
- 官方 wiki 推荐
- 作者 `veehou@tencent.com`（真实腾讯邮箱）
- spike 跑通验证四条清单全过

### 6.2 调研中发现 `qqbot-agent-sdk`，一度判定"官方推的新 SDK"

调研 OpenClaw 时在 `tencent-connect` 组织下发现 [`qqbot-agent-sdk`](https://github.com/tencent-connect/qqbot-agent-sdk)，README 看起来很现代（typed / 自带 onboard 扫码 / WebSocket Resume / 审批按钮 / 富媒体）——一度判定为"腾讯官方新推、要替代 qq-botpy 的 SDK"。

**这一步是过度解读**：同 org 不等于官方背书。挑战来自用户的反问"5 star 的仓库怎么会是官方推的"——驱动重新查证。

### 6.3 数据查证撤回判断

PyPI + GitHub API 实测：

| 维度 | `qqbot-agent-sdk` | `qq-botpy` |
|---|---|---|
| GitHub star | **5** | 869 |
| 首次发布 | **2026-04-28**（6 周前） | 2022-07-11 |
| 总 release | 2 | 8 |
| 月下载（PyPI） | **96** | **236,134** |
| 作者 | `WideLee`（**单人署名**，非腾讯邮箱） | `veehou@tencent.com` |
| QQ 官方 wiki 提及 | **0** | 多处推荐 |

**结论**：`qqbot-agent-sdk` 是个人贡献者发的、**6 周前才出生的实验性仓库**。"官方推的"这个判断**没有数据支撑**，撤回。

### 6.4 用户怀疑两 SDK 是否复用 → 实测推翻

用户提出"会不会 agent-sdk 是 botpy 的 fork / 大量复用代码"。直接 grep 验证：

```
$ grep -rn "import botpy\|from botpy" .venv/.../qqbot_agent_sdk/
api_client.py:68: "Mirrors the pattern used in qq-botpy for cleaner API path management."
```

**唯一交集是一行注释**。代码层面零复用。

进一步对比文件结构：

| qq-botpy（2022 年代） | qqbot-agent-sdk（2026 年代） |
|---|---|
| 主力业务：**QQ 频道**（guild / channel / forum / reaction，21 个 TypedDict） | 主力业务：**C2C 单聊 / 群聊 / agent 集成** |
| 设计理念：业务对象建模丰富 | 设计理念：事件流 + DTO 统一抽象 |
| 没有 onboard / approval 概念 | onboard / approval / event_parser 是一等公民 |

**结论**：两 SDK 是**两个时代、两条业务线**的独立产物。

### 6.5 关键洞察：成熟度不能跨场景套用

`qq-botpy` 的 23 万月下载量绝大部分冲着**频道场景**——而 agent-friend 关心的是 **C2C 单聊**，不是它的主战场。"成熟度在我们关心的场景里相关性比想象中弱"。

反过来 `qqbot-agent-sdk` 是**专为 C2C + agent 量身做的**，特征更对路（onboard / approval / 统一 InboundEvent shape）。

### 6.6 实测 `start_onboard()` 是否真能跑

`qqbot-agent-sdk` 的卖点之一是"零配置扫码即可拿到 AppID/Secret"。验证 vs vapor 的实测脚本（`experiments/qq-bot-poc/src/onboard_test.py`）：

```
[onboard] ✅ QR URL 已拿到：
  https://q.qq.com/qqbot/openclaw/connect.html?task_id=...
[onboard] ✅ 拿到凭据（已 mask）：
  app_id        = xxxx...xxxx (len=10)
  client_secret = xxxx...xxxx (len=32)
  user_openid   = xxxx...xxxx (len=32)
```

**关键证据**：QR URL 路径里的 `/qqbot/openclaw/connect.html` —— 腾讯**专门为 OpenClaw 集成在 q.qq.com 上铺了 first-party onboard 端点**。`qqbot-agent-sdk` 调用的就是这个端点，握手协议跟腾讯服务端对得上。

agent-friend 用同一个 SDK 等于**复用了腾讯为 OpenClaw 准备的 onboard 通道**。

### 6.7 拍板路线 A：全切 `qqbot-agent-sdk`

权衡：

| 路线 | 描述 | 利 | 弊 |
|---|---|---|---|
| **A. 全切 agent-sdk** | onboard + runtime 都用它 | 场景适配最佳；onboard UX 跟"扫码即用"对齐；统一抽象更干净 | SDK 还年轻、必要时要自己 fork 修 |
| B. 全保 qq-botpy | runtime 用 botpy，没有 onboard | 23 万下载量背书 | "成熟"在 C2C 场景相关性弱；牺牲 onboard UX |
| C. 混合 | onboard 用 agent-sdk，runtime 用 botpy | 风险隔离 | 双 SDK 维护负担；botpy 在 C2C 的优势没那么大 |

**最终选 A**。接受 SDK 还年轻的风险，换取场景适配性 + UX 一致性 + 抽象统一性。兜底措施：

- 在 lockfile 里 pin 死版本，必要时维护 vendoring fork
- 抽象一层 IMProvider 接口隔离 SDK，万一这条路不通可以切回 qq-botpy 而不动业务代码

---

## 7. 对 agent-friend 架构的改造方向

### 7.1 现状盘点（让我惊喜）

`agent_bridge` **已经是 gateway 形态了**，只是当前只接 HTTP inbound：

```
agent_bridge/
├── app.py                    # FastAPI lifespan（与 AgentRuntime 同生共死）
├── assembly.py               # BridgeRuntime 装配
├── protocols/                # ← 协议层概念已经成形
│   ├── openai/               #   现有：OpenAI Chat Completion
│   └── ag_ui/                #   现有：AG-UI
├── push/                     # ← 主动出站推送（M14.5）已经在了
├── routes/                   # meta REST
└── session_bridge.py
```

`protocols/` 子系统、`push/` 主动推送、`BridgeRuntime` lifespan——**正是 OpenClaw / Hermes gateway 该有的所有元素**。

**结论：不需要新建 `im_gateway` 模块，IM 是 agent_bridge 该长进去的"第三类 protocol"**。

### 7.2 落地结构

```
agent_bridge/
└── protocols/
    ├── openai/                # 现有 · HTTP inbound
    ├── ag_ui/                 # 现有 · HTTP inbound
    └── im/                    # 新增 · 长连 outbound
        ├── provider.py        # IMProvider Protocol
        ├── event.py           # InboundEvent（借用 qqbot-agent-sdk 的 shape）
        ├── content.py         # OutboundContent
        ├── router.py          # IMRouter（IM user → agent.Session）
        ├── session_link.py    # 持久化映射
        ├── onboard.py         # 暴露 start_onboard 给前端 GUI
        └── adapters/
            └── qq.py          # 薄翻译层，wrap qqbot-agent-sdk
```

明确**不做**：

- 不抽 plugin install / npm-style 注册（adapter 直接代码注册即可）
- 不另起 daemon 进程（复用 FastAPI lifespan）
- 不污染 `agent/` 核心（IM 概念全部留在 `agent_bridge/protocols/im/`）

### 7.3 部署形态约束

agent-friend 是 local-first，IM 接入只能走 **A 路线 · 每用户自己注册 Bot**：

- 每用户拿自己的 AppID/Secret，自己的 Bot 跟自己绑定
- onboard 扫码绑定后凭据加密存到用户数据目录
- 跟 OpenClaw 模式完全一致（OpenClaw README 原话："**a personal, single-user assistant that feels local, fast, and always-on**"）

另一条 B 路线（产品方持有 Bot + 中转服务，扫码即用）需要部署后端，**违反 local-first**。OpenClaw 这种顶级项目都没走，说明没必要。

---

## 8. 仍待解决 / 留给下阶段

不属于本次探索范围，但未来落地时要回答：

1. **`qqbot-agent-sdk` 长稳定性**：onboard 是 one-shot，但 runtime 长跑（断线重连 / token 续签 / 心跳丢失行为）的稳定性 spike 没覆盖，需在正式集成阶段做长跑测试
2. **多用户并发的合规升级**：截图明确说"该机器人仅供创建人使用"。要扩展到多用户，必须切到正式上线流程（公域 Bot / IP 白名单 / ICP 备案 / 公域审核）——是产品化阶段的另一个 spike
3. **富媒体消息**：spike 只验文本。图片 / 文件 / 卡片消息对桌宠展示能力可能重要
4. **错误码兜底**：43xxx（消息内容审核）/ 11xxx（权限）这类常见错误码的处理逻辑要在 IM adapter 层加上
5. **凭据本地安全存储**：避免明文落盘 / 误提交 git，需要加密 + 路径规范
6. **第二条 IM**：飞书（应用机器人 · 长连 Stream 模式）是国内合规的下一顺位；Telegram 是国际化预留通道
7. **NapCat（路线 B）什么时候考虑**：当且仅当产品决策要走"agent 替用户社交"形态时

---

## 9. 关联

- **决策**：`docs/decisions/0001` M8 IM 通道
- **Spike**：[`experiments/qq-bot-poc/`](../../../experiments/qq-bot-poc/) — 含 echo bot + onboard test + SPIKE-NOTES 决策记录
- **下一步**：进入正式 IM Provider + QQ Adapter 需求文档（`docs/decisions/` / `docs/requirements/`）
