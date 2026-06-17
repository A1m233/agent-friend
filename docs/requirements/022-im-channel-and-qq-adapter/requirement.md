# 022 · IM 通道接入 + 首条 QQ Adapter（im channel & qq adapter）

> IM Channel & QQ Adapter
>
> 把 agent-friend 从"只在桌面跟它聊"扩展到"在 QQ 里也能跟它聊"。本期把 IM 通道作为 `agent_bridge` 的第三类 protocol 接入，首条 adapter 落地 QQ 官方 Bot OpenAPI · 创建者专属模式，同时把 IMProvider 抽象留好——后续接飞书 / Telegram / NapCat 等同类只动 adapter，业务零改动。承接 [决策 0001](../../decisions/0001-product-vision-and-roadmap/README.md) M8。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 1. 背景

### 1.1 现状

- [决策 0001](../../decisions/0001-product-vision-and-roadmap/README.md) §3 把"IM 通道接入"列为 M8 能力模块；§2.1 用户视角中明确"不在电脑前的时候，可以通过常用的 IM 平台继续和它聊"。
- [探索 · IM 通道接入](../../explorations/im-channel/README.md) 已沉淀首轮多平台横向对比，收敛出**首条 = QQ 官方 Bot OpenAPI · 创建者模式**（合规零封号、个人主体可用、不需要公网 endpoint，符合 local-first 铁律）。
- [Spike `experiments/qq-bot-poc/`](../../../experiments/qq-bot-poc/SPIKE-NOTES.md) 五条验证清单全过：本地动态家庭 IP 直连 WebSocket gateway 不被 IP 白名单拦、`on_c2c_message_create` 收发文本可用、`qqbot-agent-sdk.start_onboard()` 扫码绑定流程跑通；SDK 选型经实测对比后定为 `qqbot-agent-sdk`（而非 `qq-botpy`）。
- `agent_bridge` 当前已是 gateway 形态（`protocols/openai/` + `protocols/ag_ui/` + `push/` + `BridgeRuntime` lifespan），IM 接入就是"长出第三类 protocol"，不需要新建模块、不另起 daemon。
- `agent` 当前记忆模型 `memory` 是按 `owner_user_id` 沉淀（v1 固定为 `DEFAULT_OWNER_USER_ID`），通过 `agent.runtime` 的 PostTurn hook 在每轮对话结束时立刻 `memory_feed.project_turn → memory.observe`——**跨 session 的记忆共享已经天然存在**，本期不需要改 `agent/` 核心。
- `agent_bridge.SessionBridge` 已经实现 persistent + auto-create 模式（AG-UI 用 `thread_id` 作为 `session_id`），IM 直接复用这条路即可——把 `session_id` 由 IM user 唯一稳定决定，session 落盘 / open / 复用全部 free。

### 1.2 这次要做什么

按探索 §7.2 + spike 后续行动建议落地：

- **`agent_bridge/protocols/im/` 第三类 protocol**：IMProvider 抽象 + 统一 `InboundEvent` / `OutboundContent` shape + 轻量 IMRouter（inbound → 计算 session_id → 走 `SessionBridge` persistent 模式 → 回写 outbound）。
- **首条 adapter = QQ**：基于 `qqbot-agent-sdk`，创建者专属模式，c2c 单聊文字收发，走 agent 主链路。
- **Actionbar 接入入口**：在操作栏新增"接入 IM"按钮 → 弹面板 → 选择 IM 类型（目前选项仅 QQ）→ 扫码绑定；面板同时展示已绑定的 IM 列表（类型 + 脱敏 id）。
- **凭据本地安全存储**：加密落用户数据目录（密钥从机器绑定信息 derive），跨平台一致，不依赖 OS keychain。
- **链路鲁棒性兜底**：断线自动重连 + QQ 常见错误码（43xxx 内容审核 / 11xxx 权限）优雅退化（不崩、日志清晰）。

### 1.3 跨通道"同一个它"的兑现路径

愿景层（决策 0001 §1.3）要求"IM 上的它和桌面上的它必须是同一个"。本期落地路径：

- **Session 维度独立**：IM 自己一个 session（key 由 IM user 唯一稳定决定），跟桌宠 session 并列，**对话上下文各走各的**。
- **记忆 + 人格维度共享**：通过现有 `owner_user_id` 全局 + 现有 PostTurn hook 立即 observe → **IM 里说过的事，回桌宠能记起来；桌宠里说过的事，回 IM 能记起来**。
- 用户视角的"同一个它" = 同人格 + 共享记忆 + 各自连贯的多轮上下文。

### 1.4 扩展性诉求（用户明确强调）

本期首条 IM 是 QQ，但**不锁定**。两条扩展轴留好但不实装：

- **IM 平台扩展**：加新平台 = 新增 `adapters/<x>.py` 实现 IMProvider，Router / Onboard / 凭据存储零改动。下一顺位评估：飞书（应用机器人 · 长连 Stream 模式）/ Telegram（getUpdates 长轮询）/ NapCat（路线 B 灰色，当且仅当产品决策走"agent 替用户社交"形态时考虑）。
- **会话归属策略**：本期 `session_id_for(im_event)` 实装 = `f"im:qq:{openid}"`（IM-per-user 永久独立 session）。**未来若切到"IM 跟桌宠共享 session"或"按时间/主题分会话"**，改这一个计算函数即可，业务代码零改动。**这一条不抽专门的 strategy 接口**——一个函数 + 一行调用就是 OpenClaw / 项目 `coding-design` "不提前实现完整插件系统"精神的落点。

### 1.5 与现有架构的衔接

| 现有能力 | 本期复用方式 |
|---|---|
| `agent_bridge.SessionBridge` persistent 模式（AG-UI 用 thread_id 当 session_id） | IM 同款复用：`session_id = f"im:qq:{openid}"` 当成 thread_id |
| `agent.SessionManager` + `JsonlSessionStore` | 不改动；IM session 自动落盘成 `data/sessions/im:qq:{openid}.jsonl` |
| `agent.memory` + PostTurn hook | 不改动；IM session 的每轮结束自动喂入记忆库，与桌宠 session 共享 `owner_user_id` |
| `agent_bridge` lifespan / `BridgeRuntime` | IMProvider 长连进程挂在 lifespan 内，与 bridge 同生共死，不另起 daemon |

### 1.6 跨平台定位

agent-friend 跨平台桌面应用（macOS + Windows + Linux），Windows 是 first-class 平台。本期：

- **后端**（`agent_bridge`）：纯 Python，跨平台无差异。
- **凭据加密存储**：用户数据目录路径用 `platformdirs` 等跨平台抽象拿，密钥派生用机器绑定信息（用户名 + 主机名 hash 等）走 stdlib，**不依赖 OS keychain** 以避免 macOS Keychain / Win Credential Manager 双端 plumbing 负担。
- **Actionbar 按钮 + 面板**：前端 UI，沿 015/016/017 现有跨平台 webview 路径，无新平台分支。
- **AC 验证范围**：macOS 端到端必过；Win / Linux 沿 015/016 同款约定——真跑验证留下个需求，CI 三平台 build / typecheck / lint / 单测全绿。

---

## 2. 本期范围（In Scope）

| 模块 | 目标 |
| --- | --- |
| **IM protocol 子模块** | `agent_bridge/protocols/im/` 新增，包含 IMProvider 抽象、统一 `InboundEvent` / `OutboundContent` shape、轻量 IMRouter（inbound → session_id 计算 → `SessionBridge` persistent → 回写 outbound）、Onboard 入口、凭据存储 |
| **QQ adapter（首条）** | `adapters/qq.py` 基于 `qqbot-agent-sdk`；wrap `start_onboard()`（扫码拿凭据）+ runtime（WebSocket gateway 长连 + c2c 收发文本）+ 断线重连 + 43xxx/11xxx 错误码兜底 |
| **Actionbar 接入入口** | 操作栏新增"接入 IM"按钮；点击弹面板，面板内容：① 已绑定 IM 列表（类型 + 脱敏 id）；② 接入新 IM 入口（目前选项仅 QQ）→ 扫码流程 |
| **后端绑定状态接口** | `agent_bridge` 暴露 GET 接口返回已绑定 IM 列表（类型 + 脱敏 id），供前端面板渲染 |
| **凭据加密本地存储** | 凭据加密落用户数据目录；密钥从机器绑定信息 derive（用户名 + 主机名 hash 等）；跨平台一致，不依赖 OS keychain；换机时凭据失效、用户重新 onboard（用户视角可接受） |
| **会话挂载** | IM inbound → 计算 `session_id = f"im:qq:{openid}"` → 走 `SessionBridge` persistent 模式 → session 落盘 / open / 复用全部复用现有 `SessionManager` + `JsonlSessionStore` |
| **跨通道"同一个它"** | IM session 与桌宠 session 共享 `owner_user_id` → 通过 PostTurn hook 立即 observe → 记忆跨通道共享，无需改 `agent/` 核心 |
| **链路鲁棒性** | 断线自动重连（IMProvider 公共契约，QQ adapter 自己实现）；QQ 常见错误码（43xxx 内容审核 / 11xxx 权限）日志清晰、不崩 |
| **smoke 级 AC 验证** | 本机非破坏性 smoke 测试方案（具体形态由 design 决定）；不要求 CI 跑 |

---

## 3. 非目标（Out of Scope）

以下本期**明确不做**，避免范围蔓延：

- **第二条 IM 平台**：飞书 / Telegram / NapCat / 钉钉等。**IMProvider 抽象留好但不实装、不验证**——抽象正确性靠"QQ adapter 长得像一个干净的实现 + 未来真要加第二条时改动量小"间接验证，不在本期写 mock adapter 反向证明。
- **富媒体消息**：图片 / 卡片 / 语音 / 文件。本期只走文本。
- **流式分片回复**：QQ 端整条回。原因：QQ c2c 没有打字态、分片只是切碎、且会与 agent 主链路事件流产生不必要耦合；Phase 2 设计不为流式留路。
- **多用户合规升级**：公域 Bot / IP 白名单 / ICP 备案 / 公域审核全套。本期沿用 spike 验证的"创建者专属"模式（仅 Bot 创建人可用、不进群聊）。
- **桌宠 UI 跟 IM 联动**：IM 消息触发桌宠表情 / 弹气泡 / 状态机切换等。本期 IM 通道与桌宠通道在 UI 层互不感知。
- **长稳定性专项**：24h+ 跑测、token 续签压力测试、长连断线频率边界等。本期只验"基础重连"。
- **OS 原生 keychain 接入**：macOS Keychain / Win Credential Manager。两端 plumbing 负担与本期 UX 收益不匹配，留作未来安全强化时机评估。
- **多 Bot / 多账号**：本期单一用户绑定单一 QQ Bot，不支持同时绑定多个 QQ Bot 或同时绑定 QQ + 其他 IM 并行收发。

---

## 4. 验收标准（Acceptance Criteria）

| # | 验收点 |
|---|---|
| **AC-1** | 用户在桌面 actionbar 点"接入 IM" → 弹面板 → 选择 QQ → 扫码完成绑定；面板状态变为"已绑定 QQ Bot openid={脱敏}" |
| **AC-2** | 凭据加密写入用户数据目录；明文不出现在配置文件 / 日志 / git 任何位置 |
| **AC-3** | 用户私聊已绑定的 QQ Bot 发文字消息，agent 主链路处理后，回信送达 QQ 私聊 |
| **AC-4** | **跨通道"同一个它"**：在 QQ 私聊里告诉 Bot "我的猫叫小白"，回到桌宠询问"我的猫叫什么"，通过记忆召回能答出"小白"（双向同样验证：桌宠说过的事，QQ 里能记起来） |
| **AC-5** | IM 自身多轮连贯（5-10 轮量级），上下文不丢 |
| **AC-6** | 重启 `agent_bridge` 后，用户继续在 QQ 里发消息，IM session 上下文不丢（session_id 稳定 + JsonlSessionStore 落盘自动满足，无需新建持久化层） |
| **AC-7** | 网络抖动 / 临时断连后，QQ adapter 自动重连成功，无需用户介入；重连期间收到的消息按 QQ Bot OpenAPI 的官方行为处理（不保证补投） |
| **AC-8** | QQ 常见错误码（43xxx 内容审核 / 11xxx 权限）出现时，进程不崩、日志能定位具体错误、用户在 actionbar 面板可看到当前 IM 通道状态（active / degraded / error，具体形态由 design 决定） |
| **AC-9** | smoke 测试在本机非破坏性跑通（具体形态由 design 决定；不要求 CI 跑） |
| **AC-10** | 三平台（macOS / Win / Linux）`./scripts/check` 全绿（lint + typecheck + test）；macOS 端到端 AC-1~AC-9 真跑过；Win / Linux 真跑验证留下个需求 |

---

## 5. 关键信息

- **决策**：[`docs/decisions/0001`](../../decisions/0001-product-vision-and-roadmap/README.md) §3 M8 IM 通道接入
- **探索**：[`docs/explorations/im-channel/README.md`](../../explorations/im-channel/README.md) — 多平台横向对比、QQ 两条岔路对比、SDK 选型经实测多轮反复、对 agent-friend 架构改造方向
- **Spike**：[`experiments/qq-bot-poc/SPIKE-NOTES.md`](../../../experiments/qq-bot-poc/SPIKE-NOTES.md) — 5 条验证清单全过、SDK 选型经实测对比定为 `qqbot-agent-sdk`、`q.qq.com/qqbot/openclaw/connect.html` 是腾讯为 OpenClaw 铺的官方 onboard 端点
- **SDK**：`qqbot-agent-sdk`（PyPI），创建者专属模式，c2c 单聊 / onboard / 统一 `InboundEvent` shape
- **同品类参考**：OpenClaw / Hermes 的 gateway 控制面架构哲学（"The Gateway is just the control plane — the product is the assistant"），不照抄 plugin install 注册系统
- **依赖的 agent 现状**：`agent_bridge.SessionBridge` persistent 模式、`agent.SessionManager` + `JsonlSessionStore` 落盘、`agent.memory` + PostTurn hook 跨 session 共享记忆——本期全部复用，不动 `agent/` 核心

---

## 6. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-17 | 初稿确认 | — |
