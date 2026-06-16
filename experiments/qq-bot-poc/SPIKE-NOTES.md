# SPIKE-NOTES · QQ Bot OpenAPI 链路可行性测试记录

> 本文件是本 spike 的**最终交付物**。

## 元信息

- **分支**：`worktree-prancy-waddling-pebble`（在 worktree 内开发，待评审后合入 main）
- **创建时间**：2026-06-16
- **结束时间**：2026-06-16
- **状态**：✅ 全部验证清单通过；SDK 选型经实测对比后定为 `qqbot-agent-sdk`
- **验证对象**：QQ 官方 Bot OpenAPI · 创建者专属（仅创建人可用、不进群聊）模式 · C2C 单聊
- **目标结论**：判断 agent-friend 通过官方 OpenAPI 直连 QQ 是否可行（vs. 退回 NapCat 灰色路线）

## 最终结论

**路线 A（QQ 官方 Bot OpenAPI · 创建者模式）对 agent-friend 成立**，作为首个 IM 接入路线。

理由：
- 完全合规、无封号风险（不像 NapCat 那条灰色路线）
- 个人主体只需实名即可拿凭据，门槛低
- 实测 **本地动态家庭 IP 直连 WebSocket gateway 不被 IP 白名单拦**——这是之前最担心的 deal-breaker，实测推翻
- 跟 agent-friend 的 local-first 架构天然契合：不需要任何公网 endpoint、不需要中转服务器、不需要 ngrok
- onboard 扫码绑定流程实测可用，UX 与你之前问的"扫码即用"完全对得上

## 验证清单逐条核销

| # | 验证点 | 结果 | 证据 |
|---|---|---|---|
| 1 | AppID/Secret 能否从本地动态 IP 直连 WebSocket gateway | ✅ 通过 | `qq-botpy` 控制台 `鉴权中... → 启动成功！→ 心跳维持启动`，未撞 IP 白名单 |
| 2 | C2C 单聊事件 `on_c2c_message_create` 能否触发 | ✅ 通过 | 控制台打印 `[c2c] from openid=demo-user-2 content='你好'` |
| 3 | `post_c2c_message` 能否回复成功 | ✅ 通过 | 控制台 `[c2c] reply sent`；用户 QQ 收到 `echo: 你好` |
| 4 | 全程不依赖任何公网 endpoint | ✅ 通过 | 整个测试在 macOS 本地完成，没起任何 ngrok / 公网回调 |
| 5 | `qqbot-agent-sdk.start_onboard()` 扫码绑定流程 | ✅ 通过 | QR URL = `q.qq.com/qqbot/openclaw/connect.html?task_id=...`，扫码后正确返回 `(app_id, client_secret, user_openid)` |

## 关键发现

1. **IP 白名单只作用于"提审和上线"门禁，不挡 dev 模式 WebSocket 连接**
   wiki 原话"对于新增机器人，平台默认启用 IP 白名单功能，并限制只有填写了 IP 白名单才能**提审和上线**"——这条之前被解读得过于悲观，实测确认 dev / 创建者模式连 gateway 完全不需要 IP 白名单。

2. **"创建者专属、暂不支持进入群聊"是一个比沙箱更轻量的官方 dev 模式**
   wiki 没单独描述这个模式，它只在 Bot 管理端 UI 上呈现。这个模式 + agent-friend "只人对人"的需求 = 绕过了所有合规复杂度（IP 白名单、URL 域名 ICP 备案、公域 / 私域审核、使用范围审核）。

3. **`q.qq.com/qqbot/openclaw/connect.html` 是腾讯专门为 OpenClaw 集成搭的官方 onboard 端点**
   这条路径里 `/openclaw/` 段是关键证据——腾讯服务端**为 OpenClaw 这套 agent 生态铺了 first-party 基础设施**。`qqbot-agent-sdk.start_onboard()` 调用的就是这个端点，握手协议跟腾讯服务端对得上。**这意味着 agent-friend 用同一个 SDK 等于复用了腾讯为 OpenClaw 准备的 onboard 通道**。

4. **OpenClaw / Hermes 跟 agent-friend 是同品类 peer，不是 dependency；但它们的架构哲学值得直接抄**
   从 OpenClaw README 实读到的核心句："**The Gateway is just the control plane — the product is the assistant.**" 它们的 channel/plugin 契约也证实"channel 只做协议翻译，不夹任何 AI 逻辑"——这两条原则直接借鉴。

## SDK 选型决策（经实测多轮反复）

最终选择：**`qqbot-agent-sdk` 全切**（路线 A）。

决策路径（如实记录走过的弯路）：

1. **Spike 初版用 `qq-botpy`**：社区流行（PyPI 月下载 23 万）、869 GitHub star、官方 wiki 推荐。`bot_init → C2C 收发`一次跑通。

2. **调研 OpenClaw / Hermes 时发现 `qqbot-agent-sdk`**：同在 `tencent-connect` org，README 看起来很现代（typed / tested / 自带 onboard 扫码 / WebSocket Resume）。我一度判定它是"官方推的新 SDK"，建议替换 qq-botpy。

3. **用户挑出违和感**：`qqbot-agent-sdk` 只有 **5 star**，跟"官方推"明显不一致。要我用数据支撑或撤回判断。

4. **PyPI + GitHub 元信息实测**：
   - `qqbot-agent-sdk` 首次发布 **2026-04-28**（6 周前），仅 2 个 release，月下载 **96**，作者 `WideLee`（个人署名，非腾讯邮箱）
   - `qq-botpy` 首次发布 2022-07，月下载 **236,134**，作者 `veehou@tencent.com`（真实腾讯邮箱）
   - **我的"官方推的"判断没有证据支撑**——同 org 不等于官方背书，撤回那个说法

5. **实测 `start_onboard()` 是否真能跑**：QR URL 拿到，扫码后真的返回合法凭据。**功能不是 vapor**，且端点确实是 q.qq.com 的官方域。

6. **代码结构对比验证两个 SDK 是否复用**（用户怀疑 agent-sdk 是 botpy 的 fork）：
   - `grep` 验证：`qqbot_agent_sdk` 没 import 任何 `botpy` 模块；唯一交集是一行注释提到"借鉴 qq-botpy 的 API path 风格"
   - **结构对比**：
     - `qq-botpy` 主力业务是 **QQ 频道**（guild / channel / forum / reaction，共 21 个 TypedDict）——2022 年的产物
     - `qqbot-agent-sdk` 主力业务是 **C2C 单聊 / 群聊 / agent 集成 + onboard + approval**——2026 年的产物
   - **结论**：两 SDK 是**两个时代、两条业务线**的独立 SDK，没有代码复用

7. **重新评估**：
   - `qq-botpy` 的 23 万月下载量绝大部分冲着**频道场景**——agent-friend 关心的 C2C 单聊不是它的主战场，"成熟度"在我们关心的场景里相关性比想象中弱
   - `qqbot-agent-sdk` 是**专门为 C2C + agent 量身做的**，特征更对路（onboard / approval / 统一 InboundEvent shape）
   - 但 `qqbot-agent-sdk` 自身的"年轻 + 低 adoption + 单人署名"风险**仍然真实**

8. **最终拍板路线 A**：接受 SDK 还年轻的风险，换取场景适配性和 UX 一致性；正式集成实施期需要做的兜底：
   - 维护一份 `qqbot-agent-sdk` 的 vendoring fork（或在 lockfile 里 pin 死版本），上游卡 bug 时能就地补丁
   - 抽象一层 IMProvider 接口隔离 SDK，万一这条路不通可以切回 `qq-botpy` 而不动业务代码

## 借鉴 OpenClaw / Hermes 的架构原则

直接读两边源码 + package.json 沉淀的可复用原则：

1. **Gateway = 控制面，不是产品本身**：所有"懂事"的能力（人格、记忆、tool、人设）都在 agent 核心；gateway 只负责协议翻译 + 长连维持
2. **channel adapter 只翻译，不夹逻辑**：`@tencent-connect/openclaw-qqbot` 的 README 原话——"This plugin serves as a message channel only"
3. **统一 InboundEvent shape**：`chat_scope` (c2c/group/guild) + `chat_id` + `user_name` + `content` + `message_id`——直接借用 `qqbot-agent-sdk` 的 `InboundEvent` 类型作为内部统一事件模型
4. **长连进程跟 agent 同生共死**：OpenClaw 用 launchd/systemd daemon，agent-friend 复用现有 `BridgeRuntime` lifespan 即可，不需要另起 daemon

## 对 agent-friend 架构改造方向

`agent_bridge` 已经是 gateway 形态（`protocols/` 子系统、`push/` 主动推送、`BridgeRuntime` lifespan）。**不需要新建 `im_gateway` 模块，IM 是 agent_bridge 该长进去的"第三类 protocol"**：

```
agent_bridge/
└── protocols/
    ├── openai/                # 现有
    ├── ag_ui/                 # 现有
    └── im/                    # 新增
        ├── provider.py        # IMProvider Protocol 抽象
        ├── event.py           # InboundEvent（借用 qqbot-agent-sdk 的 shape）
        ├── content.py         # OutboundContent
        ├── router.py          # IMRouter（IM user → agent.Session 映射）
        ├── session_link.py    # 持久化映射
        ├── onboard.py         # 暴露 start_onboard 给前端 GUI
        └── adapters/
            └── qq.py          # 薄翻译层，wrap qqbot-agent-sdk
```

明确**不做**：
- 不抽 plugin install / npm-style 注册（adapter 直接代码注册即可）
- 不另起 daemon 进程（复用 FastAPI lifespan）
- 不污染 `agent/` 核心（IM 概念全部留在 `agent_bridge/protocols/im/`，遵守 `agent` README 第二段"独立于任何第三方框架"原则）

## 仍需进一步验证的问题（不在本 spike 范围）

1. **`qqbot-agent-sdk` 长稳定性 / 重连 / token 续签**：onboard 是一次性的，但 runtime 长跑稳定性没测——正式集成阶段必做长跑测试
2. **多用户并发**：截图明确说"该机器人仅供创建人使用"。如果未来要给多个用户用，必须切到正式上线流程（公域 Bot），那条路 IP 白名单 / ICP 备案就全回来了——产品化阶段的另一个 spike
3. **富媒体消息**：本次只验了文本。图片 / 文件 / 卡片消息等需要单独验，对桌宠展示能力可能重要
4. **接收 / 发送频率上限**：wiki 没列具体 QPS 数值
5. **错误码处理**：43xxx（消息内容审核）/ 11xxx（权限）这类错误码兜底逻辑要在 IM adapter 层加上
6. **`qqbot-agent-sdk` 拿到的凭据能否给 `qq-botpy` 用**：本 spike 没做这一步交叉验证。决定走路线 A 之后，runtime 也用 agent-sdk，不再需要确认这条；但若未来回退混合方案，要先验

## 凭据安全

- 本 spike 的 `.env` 已被根目录 `.gitignore` 兜底忽略
- 调试期间凭据曾一度出现在 `.env.example` 上（开发者主动调整为方便本地复用），**正式合并前必须把 `.env.example` 还原为模板形态**（仅留 `QQ_BOT_APPID=` / `QQ_BOT_SECRET=` 不带值），避免凭据进 git 历史

## 后续行动建议

按优先级：

1. **【必做 · 合并前】** `.env.example` 还原为不含值的模板，防止凭据进 git
2. **【建议 · 合并】** 把当前 worktree 评审 + 合入 main，沉淀这套 spike 脚手架（含 echo bot + onboard test + 这份决策记录）供未来复用
3. **【下一阶段 · 正式需求】** 启动 IM Provider + QQ Adapter 需求（建议编号沿用 `docs/decisions/` 序号），按 feature-delivery skill 走拆解 / 分析 / 设计 / 实现四阶段。范围至少包括：
   - `agent_bridge/protocols/im/` 子模块结构（按上节"对 agent-friend 架构改造方向"落地）
   - IMProvider 抽象 + InboundEvent / OutboundContent shape
   - QQ adapter 基于 `qqbot-agent-sdk` 实现（非 `qq-botpy`）
   - onboard 流程接到前端 GUI（设置面板"接入 QQ Bot" → 拉 QR → 扫码 → 凭据加密落盘）
   - IM user openid ↔ agent Session 映射的持久化策略
   - 凭据本地安全存储（避免明文落盘 / 误提交 git）
   - SDK 风险兜底：vendoring fork / 锁版本 / 必要时切回 qq-botpy 的回退路径
   - 长稳定性 / 重连 / token 续签验证
