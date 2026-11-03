# 169 · 需求与实施证据索引

## 用途与范围

- 服务 [需求 169](./requirement.md) 与 [技术方案](./design.md)，记录代码核查、已有验收和官方资料之间的关系；不替代方案或实现验收。
- 最近对账：2026-11-03。代码基线：`3606c9fa79b331b1cb5d6103b40c883e98b3dbc4`；E-001～E-019 为实现前核查，当时仅有需求文档。E-020 起对应同一基线上的本需求实现 overlay，尚未提交，精确验证边界见 progress.md。
- 调研阶段的 QQ 证据来自 166；实施阶段已新增有界模型观察、真实桌面与一次获授权的 QQ 主动发送，不能把两个阶段混为同一次运行。

## 当前综合判断

原先识别的自主活动编排、目标发现与投递、通用自动审批、主动来源投影和记忆语境已落地，机制测试与代表性运行观察见实施证据。补验已覆盖 B 站实际分享及安静重访、跨会话记忆重开与遗忘、长内心会话压缩重开、桌面点击重审与消息预览通知。手机真实入站回复未实测。Guide 按用户决定完成本地验收，版本待定。

已有 QQ 主动发送曾完成真实平台回执和用户手机接收验收，不能因一份通用平台文档就断言现有渠道不可用，更不能直接扩大为新增 IM 提供方。本需求以复用已有链路为方案前提；平台适用范围与长期限制仍需如实保留为未验证项。

## 证据账本

以下本地路径均相对仓库根；代码类条目的版本统一为上述基线。`confirmed` 仅覆盖该条明确写出的主张。

| ID | 日期 | 问题与直接观察 | 类型及锚点 | 置信度 / 状态 | 局限 |
| --- | --- | --- | --- | --- | --- |
| E-001 | 2026-11-03 | 系统触发轮可以走普通工具循环；`tool_mode=normal` 要求 `output_visibility=user`，可选 `presentation=none`。 | code-supported；`agent/src/agent/runtime/inbox.py`、`agent/src/agent/conversation.py:1468` | confirmed / active | 现有 silent/memory_only 路径不能直接运行工具；前端隐藏与执行权限须分开考虑。 |
| E-002 | 2026-11-03 | 会话提交服务会准备 Conversation 并启动模型轮；scheduler 已有短时会话 mutation reservation，忙时拒绝。 | code-supported；`agent_bridge/src/agent_bridge/session_requests/service.py`、`agent/src/agent/runtime/scheduler.py:210` | confirmed / active | 没有现成的通用跨会话成果投递接口；普通 submit 不是无模型的消息追加。 |
| E-003 | 2026-11-03 | 当前审批模式为 per_request/session_auto；后者按已有灾难性规则判断后直接决定，并非独立模型审查。 | code-supported；`agent/src/agent/approvals/broker.py:170`、`agent/src/agent/approvals/models.py` | confirmed / active | 不能把现有 session_auto 重命名为“帮我审批”即认为对齐完成。 |
| E-004 | 2026-11-03 | IMRuntime 已能列举会话绑定及在线状态；send_proactive 在发送前复核绑定并使用无 reply ID 的消息，结果为 delivered/failed/unknown。 | code-supported；`agent_bridge/src/agent_bridge/protocols/im/runtime.py:214`、`protocols/im/delivery/once.py`、`protocols/im/adapters/qq.py:844` | confirmed / active | 通道在线不代表用户在线；通用能力发现和 Agent 选路仍需补齐。 |
| E-005 | 2026-11-03 | QQ 通用官方文档顶部写有 2025-04-21 停止主动推送的通知。 | external-doc；[腾讯文档](https://github.com/tencent-connect/bot-docs/blob/main/docs/develop/api-v2/server-inter/message/send-receive/send.md)，file blob `26d8ec7606e4f6d3058cb87a221067cc16a44b28` | confirmed / retained | 只证明文档如此说明；对当前 AF 账号/接入的适用性不明。其下还保留旧额度说明。 |
| E-006 | 2026-11-03 | 初期由 E-005 推断现有 QQ 不能承担首发主动触达，并询问新增渠道。 | inference；本轮讨论 | unconfirmed / invalidated | 漏查已有真实验收；E-007 直接反驳“现有渠道不可用”的笼统推断。 |
| E-007 | 2026-11-03 | 166 记录 2026-10-27 自动 scheduled 触发，QQ delivered、有平台回执、尝试 1 次，用户手机确认“已收到，内容正确”。 | recorded-runtime-and-user-acceptance；[166 progress](../166-scheduled-task-im-notification/progress.md)，实现 `9f2d07b1`，合入 `a2762e43` | strongly-supported / active | 本轮读取验收记录，未重开原临时证据或重新发信；不证明所有账号、任意频率或无限时间均可发送。 |
| E-008 | 2026-11-03 | 从 `a2762e43` 到当前基线，IM runtime、QQ adapter、deliver_once 三个目标文件无 diff。 | code-supported；`git diff a2762e43 HEAD -- agent_bridge/src/agent_bridge/protocols/im/runtime.py agent_bridge/src/agent_bridge/protocols/im/adapters/qq.py agent_bridge/src/agent_bridge/protocols/im/delivery/once.py` | confirmed / active | 只证明这些文件相同；不把当前树冒充当时实际运行树，也不代表外部平台状态相同。 |
| E-009 | 2026-11-03 | 当前记忆素材主要投影 user/assistant 发言，Utterance 只有 speaker/text/time/source；已有按来源失活及取消抽取入口。 | code-supported；`agent/src/agent/memory_feed.py`、`memory/src/memory/contracts.py:63`、`memory/src/memory/facade.py:157` | confirmed / active | 现有契约未充分表达自主经历、沟通副本、证据状态及跨来源派生关系。 |
| E-010 | 2026-11-03 | 前端会隐藏 presentation=none 的整轮；请求来源解释目前只识别 scheduled_task。 | code-supported；`frontend/src/stores/sessionProjection.ts:170`、`agent/src/agent/request_context/source.py` | confirmed / active | 不能仅隐藏内部 query 就假定最终消息、历史和模型上下文自动一致。 |
| E-011 | 2026-11-03 | Codex 自动审批替换审批者而不取消执行边界；只审需审批动作；失败不执行，普通任务有精确动作重试授权路径。 | external-doc；[Auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review) | confirmed / active | 文档不是 AF 已实现证据，也不保证不同模型判决一致。 |
| E-012 | 2026-11-03 | 当前 Codex 代码组装精确动作与可信历史；reviewer model 有目录选择/回退机制；标准熔断阈值为连续 3 次或最近 50 次中 10 次拒绝，特殊模型另有策略。 | official-source；[prompt.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/guardian/prompt.rs) blob `e2a82e65ae6cf21c0d60a2b4a59b59e29638dd43`；[reviewer_config.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/guardian/reviewer_config.rs) blob `66a5ed5f4a3485e02d8cde4b1be0bf4b58af5d53`；[circuit_breaker.rs](https://github.com/openai/codex/blob/main/codex-rs/ext/guardian-reviewer/src/circuit_breaker.rs) blob `83e8143a829d78fc6ecba21e8f1f2c780127bcee` | confirmed / active | 各文件通过 default branch 分别获取，未声称来自同一冻结 commit；正式对齐仍需固定整体版本。 |
| E-013 | 2026-11-03 | 官方文档列出的 core/src/guardian/policy_template.md 返回 404；代码已通过 codex-prompts 的模型消息装配策略，reviewer 代码位于 ext/guardian-reviewer。 | official-source；[instructions.rs](https://github.com/openai/codex/blob/main/codex-rs/ext/guardian-reviewer/src/instructions.rs) blob `4c77cffe726f3368fddd725eb85c594957b75159`；`codex-rs/Cargo.toml` | confirmed / active | 尚未核验完整默认策略正文，不能宣称“完整照搬 Codex 策略”已经准备完毕。 |

### 会话增量与回顾记忆补充核查

以下条目仍使用上述代码基线，均为静态核查，未运行真实模型或重抽用户记忆。

| ID | 日期 | 直接观察与推断边界 | 代码锚点 | 置信度 / 状态 |
| --- | --- | --- | --- | --- |
| E-014 | 2026-11-03 | recall_past_chats 按标题解析目标、按主题或时间检索；limit 表示对话轮次组，默认 10、上限 50。未暴露稳定会话引用直读、精确最后 N 条消息或唤醒增量读取参数。 | `agent/src/agent/tools/builtin/conversation_history/tool.py:58` | confirmed / active |
| E-015 | 2026-11-03 | 底层已有按 turn_limit、before、history_epoch 读取完整轮次的业务索引分页；目录提供会话 ID、标题、归档状态和更新时间。它们不是现成的“自上次唤醒新增业务消息数”接口。 | `agent/src/agent/sessions/business_index/pages.py:48`、`agent/src/agent/sessions/catalog.py` | confirmed / active |
| E-016 | 2026-11-03 | memory_feed 不直接把工具结果送入普通抽取，但会投影非 partial 的 assistant_message；基础抽取输入主要呈现发言者和正文，reconciler 的 provenance 来自本轮素材来源。因此回顾旧资料后的 AF 复述可能被当成本轮新素材；这是机制风险，未声称已观察到真实误写案例。 | `agent/src/agent/memory_feed.py:168`、`memory/src/memory/extraction/extractor.py:420`、`memory/src/memory/extraction/reconciler.py:66` | 代码事实 confirmed；实际误写 unconfirmed / active |

### 固定审批参考与讨论收敛

| ID | 日期 | 观察或确认 | 锚点 | 状态与局限 |
| --- | --- | --- | --- | --- |
| E-017 | 2026-11-03 | 定位完整默认策略与模板的新路径，并在同一官方 commit 读取策略、模板和 assessment 解析器。低风险允许简写 outcome；其余风险、授权与理由有默认处理。 | 官方 commit `8806313db4c82c89efcae3e6ac682bed6f4d6a66`；[policy.md](https://github.com/openai/codex/blob/8806313db4c82c89efcae3e6ac682bed6f4d6a66/codex-rs/prompts/templates/guardian/policy.md)，blob `a0c5ed2e32849d0fa3ffef637caa90e85c630d5b`；[policy_template.md](https://github.com/openai/codex/blob/8806313db4c82c89efcae3e6ac682bed6f4d6a66/codex-rs/prompts/templates/guardian/policy_template.md)，blob `74567d7c62a39f708f608799cf76e2d05d36bb28`；[assessment.rs](https://github.com/openai/codex/blob/8806313db4c82c89efcae3e6ac682bed6f4d6a66/codex-rs/ext/guardian-reviewer/src/assessment.rs)，blob `80c5af6b74070d21b47d716068be913f187636cb` | confirmed / active；补齐 E-013 的定位缺口，不否认旧链接 404。读取默认策略不代表已复用当前产品的服务端模型或实际策略覆盖。 |
| E-018 | 2026-11-03 | 用户确认后续简化：唤醒不额外注入延续事项；会话检索和直读拆开；回看旧消息的语义约束适用于所有会话，先以抽取 prompt 与 few-shot 实现，不建立逐条来源校验机制。 | 当前需求讨论与“可以。继续吧”；已同步 requirement/design | human-reviewed / active；不把模型行为目标当作确定性保证，尚未实施或真模型验证。 |

## 未决问题与决策影响

2026-11-03 补充确认（E-019）：用户要求丰富更有人味的场景，并明确示例仅作抛砖引玉，允许更好的新想法。已同步 requirement R-12 / AC-21 和 design §9.1；这里记录产品意图，不是这些行为已被模型验证的证据。

| 问题 | 依赖证据 | 当前边界与下一项有效证据 |
| --- | --- | --- |
| 是否需要新 IM 提供方 | E-004～E-008 | 没有必要性证据；继续已有 QQ 路线。若真实出现平台拒绝，再按具体错误和适用账号查明原因，不提前引入 NapCat/飞书。 |
| 自动审批的精确基线 | E-003、E-011～E-013、E-017 | 已固定源码版本并定位完整默认策略；实现仍需核对 AF 的执行边界、输入装配和模型行为，不声称不同模型判决完全相同。 |
| 自主经历如何进入共享记忆 | E-009、E-016、E-018 | 来源语境与现有链路复用；旧事实语义去重先做 prompt/few-shot，不把此前较重的派生治理建议带入实现。 |
| 跨会话投递与前端一致性 | E-002、E-010、E-021、E-022 | 已有落盘、恢复、通知和冷启动 UI 证据；真实 QQ 与桌面为分开的验证，不宣称一次完整真模型到手机再回复的 E2E。 |

## Artifact 映射与完整性

- E-001～E-004、E-008～E-010：原始材料为本仓库上述 commit 的文件；没有另外复制源码。E-008 命令 exit 0、输出为空。
- E-007：原始运行与用户确认的归档入口是 166 progress；原临时目录与接收端信息不在本需求复制。本轮没有重新校验临时 artifact 完整性。
- E-005、E-011～E-013：公开资料通过 GitHub connector 或官方文档工具读取；网页 URL、日期与可获得的文件 blob 已列出。正文未另行归档，远端 main 链接可能继续变化。
- E-017：GitHub connector 获取上述固定 commit 的 recursive tree（truncated=false）和三个文件；文件 blob 与 tree 一致。未复制外部完整策略到需求文档，实施时按固定来源及许可证取用。
- 本索引不保存用户凭据、生产会话正文或完整模型输入输出。

## 对账记录

| 日期 | 范围 | 结果 |
| --- | --- | --- |
| 2026-11-03 | 169 需求、当前实现、166 验收与官方资料 | 需求仍为 CONFIRMED，design 尚未创建；更正 E-006，保留 E-005 文档事实但不再据此扩大渠道范围。方案准备继续。 |
| 2026-11-03 | 会话直读、分页与回顾内容的记忆入口 | 补充 E-014～E-016；增量简报、直接读取和回顾记忆限制仍在讨论，没有把建议写成已确认方案或已实现行为。 |
| 2026-11-03 | 后续讨论确认及方案落盘 | requirement/design 为 CONFIRMED；E-017 补齐审批策略定位，E-018 收敛记忆范围。没有生产代码变更、运行时测试或真实发送。 |
| 2026-11-03 | 自主活动场景与开放性 | 记录 E-019，增加启发性行为素材及不局限示例的明确要求；未调用模型验证或修改生产 prompt。 |
| 2026-11-03 | 实施与验收 | 已实现内心会话、通用审批、直读和投递；实际结果与精确本机证据见 progress.md。30 次有界真实模型调用覆盖审批/记忆/自主情境/控制；冷启动 Tauri 验证通过；用户明确授权后向已有 QQ 发送一次，平台 delivered。此前“尚未实现/发送”的条目是调研阶段事实，现由本条更新，不删除历史记录。 |

## 实施证据索引

| ID | 证据 | 结论与局限 |
| --- | --- | --- |
| E-020 | progress.md 的本机验证证据：observation-01、controls-01/02 | 合成历史、同一配置的 30 次真实请求；包含一次 provider service_unavailable 及仅剩余样本复验。不是长期稳定性或所有模型效果保证。 |
| E-021 | af-169-qq-receipt-01.json | 既有唯一绑定、一次真实主动投递、目标会话追加成功、QQ 返回回执；没有用户先发新消息。用户明确不需要额外回复，未验证手机回复和已读。 |
| E-022 | af-169-desktop-evidence-05/result.json 与完整窗口截图 | 同 worktree 新鲜 Tauri/bridge/Vite 的实际交互和状态落盘；主动消息用合成 fixture，UI 验收没有真实模型或 IM。不是整条真模型到手机的单次 E2E。 |
| E-023 | frozen host-run 7e8f9e7bd703475abb4ad4acb26df0e9 的 output.log / result.json | staging doctor、冻结服务和自主陪伴 capability 通过；后续仅成功内心记忆抽取标记修正及格式修改，另跑源码回归，不声称冻结制品包含后续逻辑修正。 |
| E-024 | af-169-tab-01，项目迁移与调度测试 | 新默认开启、单独设置标签、失焦/Enter 自动持久化。已明确关闭、暂停和已有会话身份可恢复；用量统计不终止活动。 |
| E-025 | af169-retry-desktop-03，af169-presentation-04 | 实际 WebView 点击精确重审，真实文件工具读取隔离文件；搜索显示主动消息正文，独立气泡显示同一正文，通知记录 presented。隔离 bridge，模拟模型/裁决/初始拒绝推送。 |
| E-026 | af-169-bili-followup-01、af169-bili-revisit-01 | 有来源与理由的 B 站投递 1 条，已分享同一来源及用户要求减少联系的情境 0 条。不同合成情境，不能推出每次的决定。 |
| E-027 | af-169-continuity-01、af-169-compaction-03 | 实际抽取、落盘和重开，双向跨会话调用记忆、遗忘后回顾未恢复旧事实。真实摘要与 checkpoint 重开保留未完成事项。结果为代表样本。 |
| E-028 | guide-content.patch，r104 本地候选 | 本地候选已核对对话设置、主动 QQ 通知、只读活动入口、系统输入时间线索和授权模式，内容摘要 04991304ff02da89c87e627bae9d5c10e5d50b036a96739573cce6d0146de9ac。用户确认本期只验本地 Guide。 |
| E-029 | af169-unattended-01.json，test_companionship_runtime.py | 无前台输入、无手动 tick 的独立进程实际等待 603.6 秒，正常唤醒一次且保持安静；模拟错过两天的 bridge 重建后只处理一次。模拟模型、关闭 IM，不能合并推断为真实手机回复或特定系统合盖行为。 |
| E-030 | af169-frozen-verification-02.json，af169-freeze-input-overlay-02.zip | 本次修订后冻结运行时 build 20261103T032834Z-3606c9fa79b3-dcfc3fee 的 doctor、bridge、voice 通过；运行时 inventory SHA256 为 3f580f735dc916173c52d5c884216476b56c82ad3fb223a0221bd93017131d5a。保持开发桌面在线。 |
