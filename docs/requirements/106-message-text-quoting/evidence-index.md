# 消息文本引用与跨通道统一引用 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：桌面引用展示、精确片段定位、QQ Bot 引用事件能力、当前仓库消息与 IM 路径，以及统一引用边界的决策依据。
- 本索引不记录：完整用户会话原文、竞品内部实现推测、尚未执行的真实 QQ payload、后续实现测试或视觉验收结果。
- 最近对账：2026-08-27，已与确认需求、[design.md](./design.md)、产品基线 `cdc0b547db8147c9ef95e4bf42f7f2641d2b79c9`、用户提供的 Codex 截图、Codex 官方源码、shadcn 官方资料、锁定 QQ SDK 和当前桌面 / IM 代码对账。

## 当前综合判断

引用应当是带来源关系的统一会话事实，而不是复制进输入框的普通 blockquote。桌面端支持用户与 Agent 消息的精确片段和多引用，但输入区把全部引用收束为一个 Codex 式聚合入口，再用 shadcn Popover 展开交互列表。引用的来源 event、文本块和字符范围只用于持久化、回跳和高亮；模型上下文只接收引用快照、来源角色和可用状态。QQ 本期只产生一个完整消息引用，adapter 负责从平台元素或持久别名索引恢复它，随后复用桌面端同一持久化和上下文投影；原文无法恢复时显式降级。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
|---|---|---|---|---|---|---|---|---|
| E-001 | 2026-08-26 | 本需求最终采用哪些产品边界 | 决策 | 用户在当前任务中确认；[requirement.md](./requirement.md) | 桌面端必须支持多引用、点击回到原文和纯引用发送；QQ 原生引用也进入同一统一引用能力。 | confirmed | active | [requirement.md](./requirement.md) |
| E-002 | 2026-08-26 | 精确片段引用与回到原文是否有成熟产品先例 | 外部资料 | Telegram 官方 Replies 2.0 与 Evolution，2023-2024 | Telegram 支持选择消息特定片段进行引用，点击引用定位到原消息的准确位置；长引用可折叠。 | confirmed | active | https://telegram.org/blog/reply-revolution/ms?setln=en；https://telegram.org/evolution |
| E-003 | 2026-08-26 | 引用内容应采用什么基础视觉语法 | 外部资料 | GitHub Docs 与 Slack Help，2026-08-26 读取 | GitHub 和 Slack 均用左侧竖线与弱化文本表达 blockquote；Slack 在输入阶段提供格式预览。该模式适合作为视觉语法，但两者的普通 blockquote 不证明来源关系。 | strongly-supported | active | https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#quoting-text；https://slack.com/help/articles/202288908-Format-your-messages-in-Slack |
| E-004 | 2026-08-26 | 当前会话是否已有稳定消息定位基础 | 代码与既有需求 | 产品基线 `cdc0b547db8147c9ef95e4bf42f7f2641d2b79c9`；002 Session 需求；前端 ChatMessage / MessageList | 002 已明确每条消息具有唯一标识以支持未来引用 / fork；当前前端领域消息有 `id`，渲染节点也暴露 `data-message-id`。 | confirmed | active | [002 requirement](../002-engine-session-management/requirement.md)；`../../../frontend/src/types/chat.ts`；`../../../frontend/src/pages/chat/components/MessageList.tsx` |
| E-005 | 2026-08-26 | 当前桌面端引用能力缺在哪里 | 代码 | 产品基线 `cdc0b547db8147c9ef95e4bf42f7f2641d2b79c9`；MessageActionBar、Composer | 消息动作条目前只有复制和用户消息编辑；Composer 只接收正文与附件，没有结构化引用草稿或选区引用入口。 | confirmed | active | `../../../frontend/src/pages/chat/components/MessageActionBar.tsx`；`../../../frontend/src/pages/chat/components/Composer.tsx`；`../../../frontend/src/stores/conversation.ts` |
| E-006 | 2026-08-26 | QQ Bot 事件与当前 SDK 是否能表达客户端原生引用 | 代码与上游源码 | 本仓库锁定 `qqbot-agent-sdk >=1.2.2`；Tencent `qqbot-agent-sdk` main，2026-08-26 | SDK 定义 `MSG_TYPE_QUOTE=103`；`MsgElement` 说明平台把引用原文和附件放在 `msg_elements[0]`；`InboundEvent` 保留 `message_type`、`msg_elements` 和 raw payload。 | confirmed | active | `../../../uv.lock`；https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/dto.py；https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/event_parser.py |
| E-007 | 2026-08-26 | QQ 引用只靠事件内原文是否足够稳定 | 外部资料与上游代码 | Tencent `openclaw-qqbot` README / changelog / quote pipeline，2026-08-26 | 腾讯维护的插件已经支持把 QQ 客户端引用注入 AI 上下文；其演进显示部分事件需要解析 `ref_msg_idx/msg_idx` 并维护入站 / 出站引用索引，不能假设所有事件总带完整原文。 | strongly-supported | active | https://github.com/tencent-connect/openclaw-qqbot/blob/main/README.zh.md；https://github.com/tencent-connect/openclaw-qqbot/blob/main/CHANGELOG.zh.md；https://github.com/tencent-connect/openclaw-qqbot/blob/main/src/gateway/qqbot-gateway.ts |
| E-008 | 2026-08-26 | agent-friend 当前是否已经把 QQ 引用交给 Agent | 代码 | 产品基线 `cdc0b547db8147c9ef95e4bf42f7f2641d2b79c9`；IM event / resolver / router | agent-friend 直接复用 SDK `InboundEvent`，附件、媒体和语音路径会查看 `msg_elements`；普通 Router 仍只把 `event.content` 作为新用户输入，没有规范化引用原文。 | confirmed | active | `../../../agent_bridge/src/agent_bridge/protocols/im/event.py`；`../../../agent_bridge/src/agent_bridge/protocols/im/inbound_resolver.py`；`../../../agent_bridge/src/agent_bridge/protocols/im/router.py`；`../../../agent_bridge/src/agent_bridge/protocols/im/attachments.py` |
| E-009 | 2026-08-26 | 桌面与 QQ 是否应共享同一引用语义 | 决策 | 用户确认；E-004 至 E-008 | 通道差异只存在于引用提取和平台可用能力；引用进入会话后的来源语义、持久化、历史展示和 Agent 上下文投影必须统一。 | confirmed | active | [requirement.md](./requirement.md) |
| E-010 | 2026-08-27 | Codex 当前如何在输入区收束多个引用 | 用户提供的产品截图 | 当前任务所附 Codex Desktop 截图，2026-08-27 人工观察 | 截图显示输入区只保留一个“4 个已选文本”聚合入口与清空操作，hover 面板在入口上方集中列出多条引用摘要；该证据支持聚合展示，不证明闭源 Desktop 的内部组件或协议实现。 | confirmed | active | 截图来自当前任务临时附件；仅保留观察摘要，不归档含会话摘录的原图 |
| E-011 | 2026-08-27 | Codex 如何向模型传递文本元素的位置 | 官方源码 | `openai/codex` commit `bde9db1375667c50dcc0c2b52532a4e2672571c2` | `UserInput::Text.text_elements` 是当前输入文本中的 UI 字节范围；协议明确它只供 UI 使用。核心把 `UserInput::Text` 转成模型 `InputText` 时只保留 `text`，不把范围传给模型。 | confirmed | active | https://github.com/openai/codex/blob/bde9db1375667c50dcc0c2b52532a4e2672571c2/codex-rs/protocol/src/user_input.rs#L15-L24；https://github.com/openai/codex/blob/bde9db1375667c50dcc0c2b52532a4e2672571c2/codex-rs/protocol/src/models.rs#L1942-L1955 |
| E-012 | 2026-08-27 | Codex 的模型可见附加上下文与普通用户消息如何分界 | 官方源码与测试 | 同一 Codex commit 的 app-server turn protocol 与 additional context tests | `additionalContext` 用 `value + untrusted/application kind` 表达模型可见附加上下文，并与正常 UserMessage item 分开；公共字段不包含来源消息 ID 或字符范围。可据此确认“位置留给 UI、模型只看受信任级别明确的内容”这一协议边界，但不能反推 Desktop 截图中的引用一定由该字段实现。 | strongly-supported | active | https://github.com/openai/codex/blob/bde9db1375667c50dcc0c2b52532a4e2672571c2/codex-rs/app-server-protocol/src/protocol/v2/turn.rs#L98-L113；https://github.com/openai/codex/blob/bde9db1375667c50dcc0c2b52532a4e2672571c2/codex-rs/core/tests/suite/additional_context.rs#L24-L114 |
| E-013 | 2026-08-27 | 聚合引用浮层使用哪个组件体系 | 用户决策与官方组件资料 | 用户确认；shadcn Popover 与 Radix Popover 官方资料，2026-08-27 | 引用列表包含逐条删除、历史跳转、键盘和触摸操作，应使用可交互、可受控的 shadcn Popover；hover 由产品层受控状态补充。TDesign 只用于对话核心和不得不做的兼容。 | confirmed | active | [design.md](./design.md)；https://ui.shadcn.com/docs/components/popover；https://www.radix-ui.com/primitives/docs/components/popover |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
|---|---|---|---|
| 当前 QQ 私聊与群聊在引用用户消息、Bot 消息时分别推送哪些字段 | 事件可能直接带 `msg_elements` 原文，也可能主要提供 `message_scene.ext` 中的引用索引；不同客户端或事件版本可能不同 | 技术方案冻结兼容顺序后，用真实 QQ Bot 分别采集 C2C / group、引用用户 / Bot 文本的脱敏 payload smoke | 不阻塞需求确认；阻塞最终 QQ 行为验收 |
| `rendered_text_utf16_v1` 在当前 TDesign Markdown Shadow DOM 中能否稳定取得 selection / Range | 现有 DOM 可能足够，也可能需要用 TDesign custom content slot 增加 React 所有的 quoteable wrapper | 实现开始后先做最小 DOM 原型，并覆盖代码块、链接、换行、emoji 和重复文本 | 不阻塞方案确认；阻塞选区实现冻结 |
| 聚合入口和长引用 Popover 的最终密度、宽度与关闭延迟 | 已确定单入口 + 交互列表，实际窗口尺寸和 Tauri WebView 仍会影响参数 | 方案实现后按 frontend 视觉验收规则做真实桌面端观察 | 不阻塞技术方案；属于视觉验收项 |
| Codex Desktop 截图中的引用具体使用哪个闭源内部协议 | 公共源码能确认 UI 位置与模型上下文的边界，但不能证明截图功能和 `additionalContext` 的具体 wiring | 只有 OpenAI 后续公开 Desktop 协议或实现才能进一步确认 | 不阻塞；本方案只对齐可验证边界，不声称复刻内部实现 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
|---|---|---|---|
| 引用使用结构化来源关系，不退化为普通 Markdown 复制 | E-002, E-003, E-004, E-001 | 当前 | 有证据证明结构化引用无法与现有会话模型兼容，且纯文本方案仍能稳定满足恢复、定位和角色区分 AC |
| 桌面端采用选区引用、纵向多引用预览、发送后引用块和点击回跳 | E-001, E-002, E-003 | superseded by E-010 / E-013 | 不恢复旧输入区方案；只有聚合入口在真实桌面端不可用时重新设计 |
| 桌面输入区使用单一聚合入口，shadcn Popover 展示可交互多引用列表 | E-010, E-013 | 当前 | 真实桌面验收证明 hover / focus / click / touch 无法在当前窗口可靠操作 |
| 来源 event、文本块和范围只用于持久化与 UI 导航，不进入模型上下文 | E-011, E-012 | 当前 | 模型用例证明仅靠引用快照、来源角色和状态无法区分所指内容，且新增位置字段有明确语义收益 |
| QQ 原生引用纳入 106，并在提取后进入统一引用管线 | E-006, E-007, E-008, E-009 | 当前 | QQ 平台真实事件证明所需引用事实不可获得，或统一模型无法表达平台必须保留的语义 |
| QQ 兼容路径不能只依赖事件内完整原文 | E-006, E-007 | 当前 | 真实 QQ 多场景 payload 证明所有受支持事件稳定携带完整且可验证的引用原文 |
| QQ 平台不支持的多引用和精确片段能力不做伪装 | E-001, E-006, E-009 | 当前 | QQ 官方事件和客户端增加并稳定提供相应能力 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
|---|---|---|---|---|
| E-001、E-009、E-013 | 当前任务中的用户确认 | [requirement.md](./requirement.md)、[design.md](./design.md) | 文档状态与变更记录 | 不保存完整会话原文 |
| E-002、E-003、E-006、E-007 | 公开官方资料与上游源码 | 通过 URL 和读取日期锚定，未复制原文 | 未归档 hash；公开仓库 main 可能演进 | 公开 |
| E-004、E-005、E-008 | 当前仓库代码与既有需求 | 产品基线 `cdc0b547db8147c9ef95e4bf42f7f2641d2b79c9` | Git SHA | 私有仓库内可访问，不含用户数据 |
| E-010 | 当前任务临时附件 `codex-clipboard-7b12ff28-3bfa-4fba-b7cd-e86fb8210293.png` | 不归档原图，仅在 E-010 保留观察摘要 | 人工观察；临时文件生命周期不保证 | 截图包含会话摘录，避免复制进仓库 |
| E-011、E-012 | Codex 官方仓库 | URL 固定到 commit `bde9db1375667c50dcc0c2b52532a4e2672571c2` 与行号 | immutable Git anchor | 公开；不依赖 main 漂移 |
| E-013 | shadcn / Radix 官方组件资料 | URL + 读取日期；设计决策固化在 [design.md](./design.md) | 公开文档可演进，项目决策已版本化 | 公开 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
|---|---|---|
| 2026-08-26 | 用户确认、同类产品公开行为、002 消息标识、当前桌面消息与 Composer | 一致；确认结构化多引用、纯引用发送和精确回跳，普通 blockquote 只作为视觉语法 |
| 2026-08-26 | 锁定 QQ SDK、Tencent OpenClaw QQ Bot、agent-friend IM event / resolver / router | 一致；平台和 SDK 已提供引用事实，当前缺口位于引用规范化与统一上下文投影，而不是 QQ 完全不提供引用 |
| 2026-08-27 | 用户提供的 Codex 截图、Codex 官方 UI-only text range / additional context 边界、shadcn Popover 资料、修订后 requirement / design | 修订：输入区由纵向多卡片改为单一聚合入口；位置只服务持久化与 UI；QQ 锁定为单条完整消息引用；证据与方案一致 |
