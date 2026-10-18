# prompt_sections/

本目录存放随 `agent` 包发布的**默认 system_prompt section markdown 资源**。
资源可由默认 `SystemPromptComposer` 的槽位或专用入口复用；公共正文只有一个来源。

## 文件清单

| 文件 | 对应槽位 key | 职责 |
|---|---|---|
| `identity_continuity.md` | `identity_continuity` | 对用户发言时的跨会话主体连续性、言行归属与非全知边界；通知和人格编辑共同复用 |
| `project_identity.md` | `project_identity` | 项目级硬约束（严守人设、不暴露 AI 身份、不元讨论 prompt 等） |
| `evidence_policy.md` | `evidence_policy` | 所有 persona 共用的事实查证总闸门（直接回答、查证、缩小结论与保留未知） |
| `persona_switch_strategy.md` | `persona_switch_strategy` | 切换人格时的语言风格策略（保留事实、重塑表达层） |
| `relationship_contract.md` | `relationship_contract` | 所有 persona 共用的关系目标、独立性与非操控底线 |
| `untrusted_content.md` | `untrusted_content` | 第三方内容、工具结果与历史片段的指令权限和事实证据边界 |
| `tool_usage_contract.md` | `tool_usage_contract` | 工具调用与工具结果陈述的通用边界（不虚构工具动作、不暴露工具机制） |
| `memory_consistency.md` | `memory_consistency` | 被追问记忆矛盾时的回应边界（不暴露存储机制、不圆机制故事） |
| `output_contract.md` | `output_contract` | 跨通道的默认输出表达约束（Markdown、链接、代码、清单等格式边界） |
| `channel_voice.md` | `channel` | 语音通道下的表达约束（文字通道默认不输出） |
| `interaction_context_contract.md` | `interaction_context_contract` | 解释宿主提供的当前回合结构化交互上下文；具体 surface 值不在基础 prompt 中 |
| `runtime_context.md` | `runtime_context` | 图片理解、近期信息、相对日期与历史归因的具体上下文边界；具体时钟逐 turn 注入 |

## 加载方式

由 `agent.system_prompt.defaults` 中的 section 工厂通过 `importlib.resources`
加载，与 `agent.personas` 加载 builtin persona 同模式。

共享读取实现位于 `agent.system_prompt.resources`；专用入口通过 `load_identity_contract()`
读取与默认 section 相同的资源，不复制正文。

## 消费者清单

| 消费者 | 装配入口 | 身份契约 |
|---|---|---|
| 普通聊天／CLI | `agent.prompts.MarkdownPromptBuilder` → 默认 composer | 默认 section |
| 任务手动继续／压缩后继续 | `Conversation.continue_after_tool_error` → 原用户目标／有来源的 task anchor → 正常工具循环 | 默认 section；摘要不恢复权限，原任务附件与 Skill 选择仍由宿主核对 |
| 桌面／语音／IM | Bridge ag_ui／openai／im → AgentRuntime → Conversation | 同上，渠道规则独立 |
| 定时任务／系统触发提醒／授权跟进 | Bridge scheduled_tasks／reminders／social_auth → AgentRuntime | 同上，不改变任务状态；定时消息由 request_context/source 提供按消息归属的宿主来源，摘要转录与任务锚点复用同一事实 |
| 气泡 | `agent_bridge.notifications.generation.NotificationGenerator` | 共享资源 + 正式人格 + 通知任务；失败通知由 service 使用本地事实文案，不调用模型 |
| 人格编辑 | `agent.personas.editor.PersonaEditorPromptBuilder` | 共享资源 + 编辑任务 + 正式人格；候选仅为数据 |
| 历史检索／记忆召回 | conversation_history／memory.retrieval.renderer | 保留来源；由消费它们的发言入口解释身份 |
| 原工具调用恢复 | tool_call_history → SessionQuerySnapshot | 只读历史证据，不恢复权限、不执行原动作；结果正文仍由 tool_result_recovery 读取 |
| 压缩摘要／记忆抽取 | context.summary_prompt／memory.extraction | 内部处理，不注入对外人格；任务内与分段归纳共用摘要契约，保留目标来源、调用引用及执行状态。摘要格式指令只由 system 承载，资料分区和宿主压缩任务不得记成用户请求 |
| 通知历史 | notifications.store → memory.host_episodes | 保存实际展示证据，不改写旧文本 |
| 模型连接探测 | agent_bridge.model_connections.inspection | 能力探测，不代表朋友发言 |

维护流程由 `.cursor/rules/agent-prompt-composition.mdc` 负责。身份正文、人格表达、
专用任务规则各自维护；清单仅记录消费者，不成为另一份产品行为契约。

## 迭代说明

这些文本设计上**会随真实使用感受频繁迭代**——直接修改 `.md` 文件即可，
不需要改 Python 代码（架构由 004 需求保证）。

仅文本编辑（不增删槽位）属于"纯 docs / 资源微调"，不需要走需求文档流程。
增删槽位 / 改 key 名属于接口变更，需走需求 + design 流程。
