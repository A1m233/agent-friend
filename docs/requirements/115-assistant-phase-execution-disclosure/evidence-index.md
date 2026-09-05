# 115 · Assistant 消息阶段与执行过程折叠证据索引

## 用途与范围

归属：[requirement.md](requirement.md)。记录 114 合入后的代码审计、已有真实证据的引用、官方协议实践与本轮用户决定。最近对账：2026-09-05。

E-001 至 E-026 是 Phase 1/2 基线调查，不代表后续实现状态。115 经实现与验证后，因 E-029 回到设计 review；最新记录见 E-027 至 E-029 和 progress.md。真实 smoke 仅通过桌面凭据入口取密钥发送授权请求，不保存凭据、不读取用户会话；未启动本任务桌面端。外部资料只在本索引记录来源与适用边界，不在 requirement/design 中复写比较账本。

## 当前综合判断

- E-046：用户明确“先把手头需求合入主干推送远端，然后来看下这个问题”，确认115验收及本次Git交付；会话锁调查不混入115修复。进入最新main同步、项目复验与merge-main-and-push Guide门禁。最终commit/推送结果待实际产生，不预填。
- 最新E-044/E-045：初始发送未知跳变已按用户确认修复，定向38测试、frontend794测试/构建与真实WebView合成probe15通过。queued时无计时头/分隔线，实际开始后79帧均为已进行0秒，排队2497ms不计入；历史/异常缺时仍未知，尾部可见。E-043未覆盖的发送过渡现已补齐，用户最终验收仍待确认。
- E-042/E-043历史验证：frontend786测试/构建与desktop probe13通过，顶部运行/中性停止/完成文案、尾部停止去重及0.5px细线成立。无取消原因字段/归因或后台变更，仍待用户最终验收。
- 当前决定E-041：顶部按运行/中性停止/完成显示用时，移除尾部停止行、分隔线更细；requirement已CONFIRMED，design DRAFT待前端简化方案确认。用户明确不区分停止者，因此不扩展取消原因透传。E-040的视觉与旧文案证据保留，不代表新摘要已实现。
- 最新视觉细化见E-040：按用户实际DOM要求完成入口字体/对齐、下方分隔线、无hover变色；frontend773测试/构建和desktop probe11通过。probe10的搜索步骤超时保留，原因未定；没有把此前失败改写为成功。
- 最新：E-038确认展示职责分离方案；E-039完成纯计时/尾部活动实现、frontend773测试/构建、backend2544测试、Rust检查与真实WebView合成probe09，仍待用户最终验收。原协议/计时证据保留，E-037的布局修订由新验证覆盖。蓝点只登记issue060，本期不修、文档随115后续合入。
- E-035的独立授权Vision smoke03通过；E-036完成真实115 WebView的持久化合成历史与UI-only运行态检查，并修正引用源展开后仍在屏外的定位问题。此前“仍待授权/未启动”只作为历史状态；仍未获用户最终验收或合入。

- E-033：用户确认 Phase 2 修订，历史回放/运行态重连不代表执行进程异常退出后自动续跑。requirement/design 已 CONFIRMED，重新实施中；E-027 旧检查及两次失败 smoke 不能当作新修订通过。
- E-034：修订实现及离线回归通过，含最终 phase 归一、纯 metadata、不重播正文、Runtime 时间事实及单轮状态入口。真实 Vision/桌面仍待授权后复验；未合入，不宣称支持率或真实交互完成。

- 114 基线提供原生 transport 和每个 delta 的 phase；115 已实现从 item 到产品事件、模型历史和 UI 的保真链路，仍待完整交付验收。
- 既有业务已经使用工具前后的完整 assistant 文本。用户确认搜索、IM、语音及长期记忆继续包含 commentary；phase 不应成为全局正文过滤器。
- 记忆抽取和 reconciliation 使用一次性 JSON 输出；上下文压缩使用 `<analysis>/<summary>` 文本。两者都通过 `generate()`，在 114 中仍走 Chat Completions，须分别保护输出契约。
- 会话 JSONL 包含产品事件与模型 rollout 两个职责明确的投影。产品显示与折叠依据 `Session.active_events` 中该 run 的事实，rollout 用于模型恢复，不能依据当前模型改写历史交互。
- DeepSeek 三模型开启 native 是本次用户确认的产品决定；114 只给 Flash 留有 phase 正面观测。Vision 两次真实 smoke 均未通过；02 已确认同一原生 item 开始 final_answer、完成 commentary 被本地严格校验拒绝，需回到 Phase 2 review 定稿时机。Pro 不属于本次已授权的真实样本。
- E-030：用户已确认局部需求修订，接受原生生命周期阶段更新且保护正文、时序和完成后的跨层一致性；requirement CONFIRMED，design DRAFT。Vision 仍启用 native，不能把能力声明或本次需求确认表述为真实交互已通过。
- E-031/E-032：用户追加并确认每轮状态/实时用时与单一折叠入口；计时含实际执行期间的工具/重试/授权等待、不含排队，真实终止后停表。已有 RunSnapshot 可提供活跃时间，持久化计时尚有缺口；新增 AC-21/22，待设计与实现，不改变既有 smoke 结论。

## 版本与采集锚点

- 代码审计 revision：`c863ac793717f3b2335993a17471b5531d1326e4`，114 merge commit；本轮 fetch 后 `origin/main` 与之相同。
- 早期只读审计使用 `git show main:<path>`，当时当前 worktree HEAD 为 `cb386a6a6e9e3f60eb5e9c5d10b76550f95121d9`；没有在旧 HEAD 执行 runtime 来佐证新代码。
- 文档所在分支：`feature/115-assistant-phase-execution-disclosure`，从上述 `origin/main` 建立并推送基线占号；实现与文档尚未提交。候选为该基线加 working-tree overlay，测试结果见 E-027，真实运行源码 hashes 见各 smoke artifact。
- 下表仓库路径均相对仓库根，可用 `git show c863ac793717f3b2335993a17471b5531d1326e4:<path>` 恢复审计对象；后续代码修改不得悄悄更新该历史锚点。
- 外部资料于 2026-09-05 在线读取；动态文档未下载快照，GitHub `main` 链接也未固定上游 SHA，仅能支持当时读取的结论。

## 证据账本

| ID | 问题 / 来源 | 类型与直接观察 | 局限 / 解释 | 置信度 | 状态 |
| --- | --- | --- | --- | --- | --- |
| E-001 | `llm_providers/src/llm_providers/stream_events.py`、`responses/stream.py`、114 requirement/design | code-supported：LLMTextDelta 有可选 phase；ResponsesState 按 item 追踪，但公开 delta 无 item ID，ProviderAssistantTurn 只有合并 content、tools、reasoning | 原生 phase 可传出不等于上层多 item 回放已实现 | confirmed | active |
| E-002 | `llm_providers/src/llm_providers/responses/request.py`、`client.py` | code-supported：assistant 输入允许 commentary/final phase；generate/complete 保留 Chat 路径；没有 previous_response_id 状态链 | 手动回放的上游 Message 尚未带回 phase | confirmed | active |
| E-003 | `agent/src/agent/conversation.py` 的流处理、`_append_assistant_event`、`sessions/model_history.py:append_provider_assistant` | code-supported：每次模型调用文本持久化为完整 assistant_message；TextDelta 转换和 ProviderAssistantTurn 聚合丢失 phase；幂等文本回放又合并所有完整片段 | 工具前文本当前已是业务文本，不能新增 final-only 过滤后声称保持兼容 | confirmed | active |
| E-004 | `agent/src/agent/memory_feed.py`、`memory/src/memory/extraction/extractor.py` | code-supported：完整 assistant_message 进入 Memory，partial 被过滤；基础抽取与 reconciliation 分别调用 generate 并解析 JSON | 保留文本是兼容性目标，不等于已证明多分段对抽取质量无影响，需测试观察批次/顺序/来源 | confirmed | active |
| E-005 | `agent/src/agent/context/summarizing.py`、`context/summary_prompt.py` | code-supported：完整可用历史渲染给单次 generate；输出按 analysis/summary 标签提取正文 | 前序口头表格误称“摘要 JSON”；代码明确为结构化文本，此条修正该表述 | confirmed | active |
| E-006 | `agent/src/agent/sessions/search.py`、`memory_search/session_history_source.py`、`memory_search/session_source_order.py`、`tools/builtin/conversation_history/render.py` | code-supported：搜索/回忆使用 assistant 正文和事件锚点，各自已有来源/可见性/partial 规则 | commentary 纳入不意味着取消原业务过滤；前端折叠后需要展开锚点 | confirmed | active |
| E-007 | `agent_bridge/src/agent_bridge/protocols/im/router.py`、`protocols/openai/encoders.py` | code-supported：IM 聚合所有 TextDelta 并保留工具后段落；stream relay 和 OpenAI-compatible 语音出口传全部可见增量 | 仅检查代码，未执行 QQ/TTS 实际投递 | confirmed | active |
| E-008 | `agent_bridge/src/agent_bridge/session_bridge.py:assistant_event_after`、`protocols/im/qq_references.py`、`reference_aliases.py` | code-supported：聚合 IM 发出的 message alias 只绑定最后一个完整 assistant event；没有直接引用文本时 fallback 取单 event 正文 | 当前已有聚合与锚点粒度差异；新增 item 分段可能进一步缩短 fallback，纳入兼容范围；未复现实网故障 | confirmed（代码关系） | active |
| E-009 | `agent/src/agent/runtime/sources.py`、`runtime/listeners.py`、`frontend/src/stores/petBubblePolicy.ts`、`petStatePolicy.ts` | code-supported：bedtime 使用 ambient agent turn；idle reflection 为 memory_only/none；气泡只接指定 presentation 并聚合所有 text_delta；状态/口型同样消费增量 | commentary 纳入仍服从原 source/presentation；固定宿主文本不是模型 phase | confirmed | active |
| E-010 | `agent_bridge/src/agent_bridge/reminders.py`、`social_auth.py`、`agent/src/agent/runtime/listeners.py:publish_fixed_ambient` | code-supported：Todo 固定提醒直接产生 ambient 文本；Agent 增强回应 presentation=conversation；IM watcher 聚合所有增量；社交授权续接分别路由 | 固定提醒和 Agent 回应是两个入口，不能以“都叫提醒”统一过滤 | confirmed | active |
| E-011 | `frontend/src/types/chat.ts`、`stores/sessionProjection.ts`、`pages/chat/components/MessageList.tsx`、`searchNavigation.ts`、`messageQuoteSelection.ts` | code-supported：TextBlock 无 phase；文本→工具→文本保持顺序；copy 包含所有文本；search/quote 依赖块 ID；主活动状态已有独立投影 | 历史 ambient 兼容仍有只跳过下一条 assistant 的逻辑，分成多 item 后需按 run 保护可见性；未做视觉观察 | confirmed | active |
| E-012 | `agent/src/agent/sessions/events.py`、`session.py:active_events`、`rollout.py`、113 design | code-supported：同一 JSONL 中 rollout 内部事件不进入 active_events；产品投影负责 UI/Memory，完整 rollout commit 恢复模型 transcript | 折叠事实不能由当前模型或 rollout 反推，用户已明确这一约束 | confirmed | active |
| E-013 | `agent/src/agent/conversation.py:append_run_terminal_event`、`agent/src/agent/runtime/__init__.py`、`sessions/recovery.py` | code-supported：run_terminal 是唯一持久终态；中断恢复只探测/补交已有工具证据，不重执行原工具 | phase 与 runtime terminal 是不同维度；具体 UI terminal 投影将在 Phase 2 核对 | confirmed | active |
| E-014 | 114 [evidence-index.md](../114-llm-provider-responses-api/evidence-index.md) 与 [design.md](../114-llm-provider-responses-api/design.md) | historical evidence：Flash 曾观测原生 final_answer，K3 样本 phase=None；DeepSeek 三模型已有 Responses route | 此处引用已有记录，没有重跑；Flash 观测不能证明 Pro/Vision 的 phase 实测通过，K3 缺失也不能证明永不支持 | strongly-supported（历史记录） | active |
| E-015 | 用户逐业务确认及追加说明 | human-reviewed：记忆保持 commentary 且不改抽取 Prompt；搜索、IM、语音包含 commentary；补 IM 整条引用；DeepSeek 全启用、Vision 优先 smoke、K3 暂缓 | 产品选择和真实支持率证据分开；范围限于现有模型 | confirmed（决定） | active |
| E-016 | 用户要求逐轮判断及最后“确认” | human-reviewed：支持/不支持模型切换不能改变旧轮；接受 Session 产品事件的事实源、rollout 的恢复职责及手动开关仅 view state | 正式字段、协议和异常语义仍需 Phase 2 设计确认 | confirmed（决定） | active |
| E-017 | `scripts/responses-smoke/run.py`、`run.ps1`、`scripts/README.md` | code-supported：现有 harness 固定 Flash/K3、合成工具、每 Provider 至多 2 请求，有 endpoint/重试护栏 | 尚不接受 Vision 图片输入，也不覆盖 115 Agent/业务/UI；不能直接跑旧脚本声称 115 已通过 | confirmed | active |

## 外部协议实践

| ID | 来源与读取日期 | 直接观察 | 对本期判断的适用边界 | 状态 |
| --- | --- | --- | --- | --- |
| E-018 | [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.5)，2026-09-05 | assistant item phase 区分中间更新和回答；手动回放要求保留原 phase，previous_response_id 由服务端保留状态；Structured Outputs 独立于 phase | 支持“原生 metadata 保真”和严格输出契约隔离；不证明其它厂商逐模型能力，也不要求本项目迁移所有调用 | active |
| E-019 | [Codex models.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs)、[event_mapping.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/event_mapping.rs)、[App Server](https://learn.chatgpt.com/docs/app-server)，2026-09-05 | MessagePhase 缺失按 unknown 兼容；核心映射保留 phase；agentMessage 带 id/text/phase，并有 item 开始、delta、完成生命周期 | 支持 item 级身份和阶段；开源协议不证明闭源 Desktop 的内部数据结构 | active |
| E-020 | [Codex default instructions](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/prompts/base_instructions/default.md)，2026-09-05 | preamble 简短、相关工具说明合并，进展承接已有信息，避免琐碎重复播报 | 用于行为约束取舍；不引入 Prompt 文本 marker，不替代结构化协议 | active |
| E-021 | [Anthropic streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)，2026-09-05 | typed content block 按 index 关联 start/delta/stop，并累积成完整 Message，示例保留工具前后的文本 | 只支持“完整内容块保留”的工程模式，不把它解释成 Anthropic 已有同名 phase | active |
| E-022 | [Vercel tool calling](https://ai-sdk.dev/docs/ai-sdk-core/tools-and-tool-calling)、[stream protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol)，2026-09-05 | steps 可读取各步 text/tool/results，response.messages 供完整历史；流协议有文本 ID 和 step/finish/abort 边界 | 页面直开间歇失败，内容由官方域名检索返回，缓存标注约五个月；只作工程模式佐证，不据此锁定库 API 或新增依赖 | active |

以上公开事实置信度仅限所述协议；本文对业务选择的推断是：完整 transcript、原生阶段事实和最终展示选择需要分别表达，避免用统一 final-only 聚合替换所有业务入口。

## Phase 2 补充检查

下列为 Phase 2 设计前同一 revision 的代码观察；用户随后确认 design.md 并授权实施，当前实现证据见 E-027。

| ID | 源码锚点 | 直接观察与设计影响 | 置信度 / 状态 |
| --- | --- | --- | --- |
| E-023 | `agent/src/agent/conversation.py:_RunModelSnapshot/_capture_run_model_snapshot`、`agent_bridge/src/agent_bridge/routes/meta.py:get_session`、`frontend/src/stores/conversation.ts:projectSessionDetail` | run 已冻结 config/client，可在该边界读取一次能力；session detail 已提供 active_events，前端历史加载优先使用它。无需新建持久数据库来保存阶段资格。 | confirmed / active |
| E-024 | `agent_bridge/src/agent_bridge/protocols/ag_ui/encoders.py`、`frontend/src/stores/conversationReducer.ts:applyPushConversationEvent`、`stores/sessionProjection.ts` | AG-UI 在首个文本/工具边界自行生成 ID，push 使用 run+文本序号，历史使用 event UUID。它们目前不是同一套 item 身份，115 须统一来源以保护定位和引用。 | confirmed / active |
| E-025 | `llm_providers/src/llm_providers/multimodal/adapter.py:_prepare_deepseek`、`responses/request.py:_content` | 产品 Vision 图片先以 Files API 上传为 file_id，再转换为 Responses input_image.file_id；原 smoke 阻止同步传输且只允许 /responses，因此需显式增加一次合成图片上传的护栏，不能直接套用原 harness 声称覆盖图像。 | confirmed / active |
| E-026 | `agent/src/agent/conversation.py:_iter_model_stream`、`sessions/rollout.py:commit` | 候选模型历史在 LLMTextDelta/LLMTurnDone 时确认接纳，完整 rollout commit 才构成恢复点；仅新增 item start 不能无意中改变 request acceptance 或失败重试边界。 | confirmed / active |

## 原设计问题与当前收口

| 问题 | 下一项具体工作 | 对阶段的影响 |
| --- | --- | --- |
| 原生 item 与 Agent segment、产品 event、模型 Message 的身份怎样关联，避免同 phase item 合并 | 已按确认设计由 Agent 分配业务 ID，三路径和模型回放有测试 | 已实现；完整验收待收口 |
| 接收时合法 final 后续又有工具/commentary，如何保留原始 metadata 并按事实源降级展示 | 已分开原生 phase 与 append-only run 校验结论；同 response 的工具尚未执行，出现 final 同样按冲突处理 | 已实现及回归；不改写原始 phase |
| 逐 run capability、终态在实时与历史如何保持同一来源 | 已复用冻结 snapshot、assistant_phase 产品事件与 run_terminal；不读取当前模型追溯判断 | 已实现并覆盖双向切换/重载 |
| split item 后的 Memory 批次、ambient 过滤、IM whole-message fallback | 已按原业务投影保留正文，并补多 item 与聚合来源 fixtures | 已实现及离线回归 |
| Vision 图片路径能否在首次两次生成请求内观察到 commentary、工具续接和 final | E-025 确认需一次合成图片上传和至多两次 /responses；执行前列出前置上传、超时/重试护栏与输出预算，未观测到的行为保留缺口 | 实现后 smoke；不得自动扩大调用 |
| 桌面真实视觉由谁执行 | 用户确认稍后由本任务执行；另一 worktree 当前在使用 | 尚未启动，等待可用时机 |

## 决策影响与演进

| 决定 | 依赖证据 | 状态 / 重新打开条件 |
| --- | --- | --- |
| 115 完整纵向交付，取消早先讨论中的 Chat 文本 framing | E-001、E-002、E-015、E-018、E-019 | 用户已确认；只有明确新增兼容需求才重开 |
| 各业务保留全部原可见 assistant 文本，记忆抽取/摘要格式隔离 | E-003 至 E-011、E-015 | 用户已确认；记忆质量策略变化需要独立需求或重新确认 |
| Session 产品事件按 run 决定展示，rollout 只恢复模型状态 | E-012、E-013、E-016 | 用户已确认；不随当前模型或未来 catalog 修改追溯重判 |
| DeepSeek 三模型开启，K3 legacy，Vision 优先验证 | E-014、E-015、E-017 | 当前产品决定；其余模型未测状态照实保留，运行反证出现时回到设计/范围确认 |
| 手动展开/收起只保存当前界面 | E-016 | 用户已确认；跨重启记住开关属于新增范围 |
| 历史口头“上下文摘要 JSON”的归类被 E-005 修正 | E-005 | invalidated：仅错误的输出格式归类；generate 隔离结论仍成立 |
| 原始 phase 与有效展示阶段需要分离 | E-001、E-012、E-013、E-018、E-027 | design 已确认，已按独立产品校验事件实现 |

## Artifact 映射

| 证据 | 原始位置 | 归档 / 完整性 | 访问与敏感性 |
| --- | --- | --- | --- |
| E-001 至 E-013、E-017、E-023 至 E-026 | 上述仓库相对路径 | Git commit `c863ac793717f3b2335993a17471b5531d1326e4`；无额外 dump | 私有项目源码，可从 Git 对象复核 |
| E-014 | 114 需求目录及其 evidence-index 引用的记录 | 保留原归属，不复制或改写历史实测；本期未重新校验原 artifact hashes | 已有脱敏证据，不能标成本期 smoke |
| E-015、E-016 | 当前任务的用户消息 | 本 requirement 与索引保存最小决定摘要；无独立原始附件 | 项目内部需求决定，不包含用户会话数据 |
| E-018 至 E-022 | 上述公开网页 | 来源 URL、读取日期和摘要；未归档网页快照/上游固定 SHA | 公开资料，动态内容变化时需复核 |
| E-027 自动化检查 | progress.md 的命令及测试映射；当前 feature working tree | 最终 backend/frontend/rust 门禁通过；不是已合入 commit | 不含真实模型请求，HTTPX 外发 guard 开启 |
| E-028 首次 Vision smoke | evidence/vision-smoke-20260905-01.json | SHA256：8d851238e680dc98dbf14c038c950fd56b74e07729e5ea28bca6326822263a3a；43765 bytes | 仅配置、源码 hashes、请求路径和异常类型；不含正文、推理或凭据 |
| E-029 Vision smoke 02 | evidence/vision-smoke-20260905-02.json | SHA256：4a9ccb25bea472fe9e04340f388af44a2d4485dd4f20ebaf161d57e0d980a876；45481 bytes | 追加错误链、结构事件摘要、partial 事件长度；无正文/推理/凭据 |
| E-031 交互参考截图 | 用户附件 codex-clipboard-328b1454-f1b4-4805-bd6d-f62074f75207.png（原位于本机 Temp） | evidence/codex-duration-reference.png；SHA256：23e7904d66f85410832f8195ac61eb47e2fe8583b01b9ded181239313b7366d1；192941 bytes | 用户提供的 Codex 局部界面与本任务文字；仅作交互参考，不证明 agent-friend 桌面已验收 |
| 桌面视觉 | 用户已明确可开始；doctor 与 dev status | 049 实例仍占用 1420/9222/18800/18900；115 未启动，没有截图/DOM 证据 | 已询问停止现有 049 实例的授权，未接管 |

## 对账记录

| 日期 | 对账范围 | 结果 |
| --- | --- | --- |
| 2026-09-05 | 114 合入 commit、源码业务入口、113 权威边界、公开协议与用户确认 | 首次建立索引；requirement 为 CONFIRMED，design/progress 尚未创建；未实现、未运行验证。摘要格式按源码修正，DeepSeek 全启用与真实实测覆盖明确分开。 |
| 2026-09-05 | Phase 2 的 run snapshot、三路径文本 ID、Vision 图片准备和 rollout 接纳时机 | 增加 E-023 至 E-026；提出方案所需证据已记录，design 仍待 declare/review。两份文档本地链接和 UTF-8 without BOM 检查通过；未执行产品测试或真实 LLM。 |
| 2026-09-05 | E-027：115 实现、fake 回归与三阶段门禁 | 见 progress.md；原始 phase 与有效折叠分离、业务正文保留、当前 view 手动开关不持久化。未做桌面视觉验收 |
| 2026-09-05 | E-028：第一次真实 Vision 请求 | 一次上传成功，第一次 /responses 调用发生 LLMProviderError；只确认运行失败，不能确认厂商或适配代码根因。没有续跑或重试 |
| 2026-09-05 | E-029：有独立授权的第二次 Vision smoke | Files/Responses 均 200；首 item 开始/完成 phase 不同触发协议错误。离线单变量对照复现；requirement 不变、design 回 DRAFT、progress BLOCKED，等待方案 review |
| 2026-09-05 | E-030：用户确认局部需求修订 | 后续核对发现 requirement 也含失败条款，已按 Phase 1 declare/review 修订 §5 与相关 AC，进入 Phase 2；原实现、smoke 01/02 及其局限保留，未新增运行证据 |
| 2026-09-05 | E-031/E-032：计时/统一入口追加确认与源码盘点 | 用户回复“符合”，requirement 加入逐 run 计时与统一入口，AC-21/22 待实现；design DRAFT。已归档参考截图，未启动桌面或发起真实模型调用 |

## E-028 失败归因与局限

- Observed failure：未完成预期的图片→工具→原生 phase 闭环；actual 请求为 1 Files + 1 Responses，低于授权上限。
- Last known good / earliest error：图片已通过产品附件适配进入模型请求；第一个 Responses 请求没有得到 LLMTurnDone。缺失原始流结构与错误 cause，无法把边界进一步缩小。
- Interaction edge / fault side：Provider boundary ↔ External environment 尚未区分；Evaluator → Observability 的错误细节丢失已由脚本 catch 代码确认。
- Evidence：报告绑定 baseline HEAD 和当时源文件 hashes；未保留当时 working-tree patch，也未保留服务端错误详情，不能声称完整可重放。fake HTTP 验证只证明本地成功和协议错误分支，不复现这次真实失败。
- Trigger / contributors：使用合成图片及真实 Vision 路径；尚无依据认定 Prompt、模型能力、phase 值或工具 schema 是触发原因。
- Counterfactual：已补错误链、HTTP 状态、事件类型/item phase 结构和 partial 事件形状；fake 验证确保不保存正文、推理、密钥。下一次有界真实执行需要新的 llm-api-confirm 授权。
- Repair owner：观测缺口已在本需求 smoke 修补；生产故障归属未定，不靠更换模型、文本 marker 或放松协议校验绕过。
- Confidence：运行失败 runtime-supported / confirmed；生产根因 unconfirmed，observability-gap；首次报告不作为 native phase 通过证据。

## E-029 · 原生 phase 定稿时机不兼容

- 状态 active；补充 E-028 的诊断缺口，但不能反向认定 01 也是同一根因。
- 运行：2026-09-05 14:29:38–14:29:44 +08:00；DeepSeek deepseek-v4-flash-vision-exp、default reasoning。用户授权最多 1 合成图片上传 + 2 生成、每次 1024 输出 token、零重试；实际 POST /files 1 次、POST /responses 1 次，均 HTTP 200。没有第二次生成或任何重试。
- Observed failure：第一个 assistant output item 的 added phase=final_answer，done phase=commentary；未得到 LLMTurnDone，未到工具执行和续接，未得到完整 usage。产品保存 24 字符 partial assistant_message，phase=final_answer；本次不是成功折叠证据。
- 最后正确点：HTTP 与结构化 SSE item/delta 已进入 ResponsesState。最早未恢复边界：responses/stream.py:_finish 验证相同 item 身份后强制开始/完成 phase 相同，抛出 responses_protocol_error: assistant phase changed during output。24 字符已流式发出，因此不重试。
- 归因：External environment → Provider boundary 的生命周期兼容不成立；repair owner 是 llm_providers 的定稿归一契约，并需统一完成事件/Agent 投影配合。现有实现符合旧 design 的严格条件；不能据此证明 DeepSeek 服务端违反官方规范，更不能归因为图片、模型推理能力或 UI。
- 证据：02 的 error_chain、stream_shapes、wire_requests 和 product_event_shapes；report 绑定 baseline HEAD 与源码 hashes，未保存完整 dirty patch 或原始 SSE 正文，不能声称原始流可完整重放。结构摘要没有独立 item ID，但执行到 phase 错误前已通过源码身份/索引校验。
- 离线反事实：用当前 ResponsesState、固定 synthetic-item、固定文本 synthetic、开始 final_answer，分别令完成 phase 为 final_answer/commentary。前者 accepted，后者复现同一异常；不联网，不改变生产代码。仅证明校验机制，不证明上游之后能完成工具/最终回答。
- 官方核对（2026-09-05）：[DeepSeek Responses guide](https://api-docs.deepseek.com/guides/responses_api/) 定义 added/done 生命周期及 completed 完整响应，但没有 phase 不变声明；[OpenAI Phase parameter](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.5#phase-parameter) 要求保留返回 assistant items 的原生 phase 用于回放，未在该页保证 added phase 恒定。Context7 与直接官方网页均已核对；没有把文档缺失当成允许任意冲突的规范。
- Confidence：本次失败与严格校验的因果链 runtime-supported + code-supported + reproduction-supported / confirmed；DeepSeek 所有模型的 phase 稳定性与 item 完成后可靠性 unconfirmed。
- 待 review 方向：开始 phase 不可靠时先输出 unknown，正文仍实时显示；在完整原生 item 到达时经统一事件定稿，取消/不完整继续 unknown。身份、正文、工具 JSON 及已完成快照一致性继续严格校验。此处只是候选，不是已批准设计；不采用文本标记或 tool/位置猜测。
- 下一步：用户 review 设计后才修改；真实复验另获授权。桌面需取得停止 049 实例的授权并启动身份/源码新鲜度匹配的 115 实例。

## E-030 · 局部需求修订确认

- 来源：当前任务中用户要求先对齐官方生命周期、只读盘点并按需求流程逐步推进，随后对局部 requirement declare 回复“可以”。human-reviewed / confirmed（需求决定）/ active。
- 已确认：同一原生 item 的 phase 更新本身不导致协议失败；正文实时、消息身份及与工具的相对顺序稳定；完成后的产品历史、最终展示与模型回放阶段一致；真正身份/正文矛盾与取消/不完整仍保持明确降级。
- 边界：保留 DeepSeek 三模型 native 范围、逐 run 事实源、unknown、各业务完整文本与 JSON 隔离；不增加真实调用授权，不代表设计或实现验收完成。
- 演进：替代原 requirement §5 的 phase 变化失败行为。E-029 早期“开始 phase 不可靠时统一 unknown”的候选保留为历史，不再当作已选择方案；当前方向是接受原生信息并支持更新，Phase 2 仍需确认精确契约。
- 产物：requirement 的修订正文和变更记录、progress 的 R1/R2；没有新的运行 artifact。既有 E-028/E-029 不能改写为新方案的成功证据。

## E-031 · 统一状态/用时入口与计时口径

- 来源：用户给出的 Codex 局部截图和“符合”确认；human-reviewed / confirmed（需求决定）/ active。截图可见完成态“用时 1分52秒”与箭头；实时增长来自用户明确要求，不能由静态图证明。
- 决定：每个桌面对话 run 一个状态/用时入口；有折叠资格和过程内容时可点击，否则仍显示文案和时间、没有箭头/点击/按钮悬停；同一入口控制该 run 全部可折叠过程分段。
- 计时：实际开始至实际结束，包含工具/重试/授权等待，不含开始前排队；取消请求不提前停表，刷新/切会话/重开不清零，旧数据缺可信时间显示“用时未知”。
- 边界：只扩展桌面对话，不给 IM/语音/宠物新增计时 UI；不改变 phase 能力、业务正文或工具授权。替代原“不包含新计时功能”的范围限制；尚未实现，不是验收通过。

## E-032 · 时间事实与现有入口的源码基线

- 来源：当前工作树的 agent/runtime/models.py（RunHandle.mark_running / finish_once / snapshot）、runtime/scheduler.py:_run_one、runtime/__init__.py 的终态持久化，以及 conversation.py:append_run_terminal_event、sessions/recovery.py。code-supported / confirmed（现状）/ active；基线 HEAD 为 c863ac793717f3b2335993a17471b5531d1326e4 加既有 115 overlay，不是运行时观测。
- RunSnapshot 已有 started_at/finished_at，队列到 running 时记录开始；durable run_terminal 只保存状态/原因等，event.ts 是写入时刻。运行中与历史若分别用两个时间来源，会产生语义漂移；需在方案中绑定统一运行事实。
- 恢复补写 run_terminal 的 event.ts 使用恢复当下时间，不证明原执行何时结束；不能拿它减原起点计算包含停机时间的“用时”。
- MessageList.tsx 当前逐 execution section 创建按钮；ModelResponseStatus / primaryActivity 已有活动文案和状态选择。统一入口需要 run 级控制、复用现有状态语义，不能再堆一条相互矛盾的活动指示。
- 此条仅约束待 review 方案的输入，不自行批准新字段、迁移或运行生命周期改动。

## E-033 · Phase 2 确认与“恢复”边界澄清

- 来源：当前任务完整设计 declare 后，用户询问异常退出是否会停下，经源码核对并澄清后回复“可以”。human-reviewed / confirmed（决定）/ active。
- 代码依据：agent_bridge/assembly.py 构造 ToolTransactionReconciler 未注册恢复执行 adapter；app.py 启动时调用 reconcile_known_sessions。sessions/recovery.py 只探测/补写遗留工具结果及可判定中断，不重发模型请求或重执行工具。frontend/stores/sessionSync.ts 的 reattachSessionRuntimeForSync 获取 snapshot 与事件 journal，不创建新 run。
- 边界：现有 reconciler 以工具请求为锚点，不声称它能给所有无工具遗留 run 补全终态；后台仍活着的前端断线重连与执行进程退出必须区分，已启动外部工具的副作用也不能据此断言已撤销。
- 决定：接受生命周期 phase 更新、完整 response 定稿、纯 metadata 事件；Runtime 时间用于快照与产品历史，每 run 一条状态/时间入口控制全部过程段。没有可信结束时刻时未知，不把启动补记时间当原结束时间；本期不新增异常退出自动续跑。
- 产物：design 正文替代旧不变约束/分段入口并恢复 CONFIRMED；requirement 明确范围排除，progress IN_PROGRESS。未新增真实运行证据，真实复验仍需新的授权。

## E-034 · 修订实施与离线验证

- 来源：2026-09-05，本115 worktree、baseline c863ac793717f3b2335993a17471b5531d1326e4 加未提交 overlay；code-supported / deterministic-tested / confirmed（所列机制）/ active。不是新的真实模型运行，不回写 E-028/E-029。
- Provider：item.done 携带阶段，response.completed 的完整 output 定稿；只允许 phase 元数据更新，身份/正文/工具快照仍校验；缺失/非法完成 phase 归一未知。按6种分片位置×4种最终 phase 回归；不新增厂商分支。
- Agent/Bridge：同消息 metadata 更新独立事件、相同UUID/顺序，完整结果先对齐再语义校验/持久化；Memory及文本聚合不重复。AG-UI回归核对更新不增加TEXT_MESSAGE_START/CONTENT/END。
- Runtime/前端：RunHandle finish-once 回调持久化同一终态时间后发布快照，落盘失败不发布成功；开始与结束作为产品事实，不进入模型消息。状态行计时独立于phase；取消请求不终止计时，历史缺可靠终点未知。单轮按钮统一各过程段，final/unknown/必要交互留在外；无折叠资格时不生成按钮/箭头。
- 验证：后端完整 pytest 2543 passed / 4 skipped，326.59s，no_external_http 开启；新增最终 Bridge/计时/fake smoke/边界定向24 passed。ruff通过，mypy616 files通过。backend 总入口两次曾因格式/类型失败，修复后子入口全部通过，最终总入口待合入前再跑；不将失败入口写成成功。check frontend 最终124 files/759 tests、所有静态门禁及build通过；check rust 218+6 passed/1 ignored，release check通过。
- 警告：测试构造重复ZIP entry warning、bilibili_api退出清理warning、既有CSS highlight/bundle体积与Rust unused警告；各最终退出码均核对为0。测试内无真实LLM外发。
- 局限：没有新Vision成功样本；UI证据是jsdom组件/纯投影，不是desktop screenshot/CDP/product E2E。再次dev status仍为049实例（PID2676、桌面PID18208），未接管。旧模型、其它平台和真实取消体感不能由离线样本外推。
- 当前核心文件本机字节SHA256如下，仅用于内容核对；不是完整working-tree patch或已提交复现锚点。后续Git换行归一时需区分字节变化与内容变化，不能重写历史运行身份。

| 文件 | SHA256 |
| --- | --- |
| llm_providers/src/llm_providers/responses/stream.py | 1b48c6c7bab39176cbce360a990ff685a1165d07ced9ea901cec089f93962ce3 |
| agent/src/agent/assistant_phase/state.py | 5bc30ab483c7ec8626ecd75895fe87e31207794969540821dd3e988cdf971e0f |
| agent/src/agent/runtime/models.py | ae589214395943c2a24cad539216a5b02481f82b94ffd23581e951c3b9369ee7 |
| agent/src/agent/runtime/__init__.py | 1f14a24f749d82df79459dc7685db0e8ab0852b5c958ccbd975fcad4041518c0 |
| frontend/src/pages/chat/components/ExecutionDisclosure.tsx | 614c7482593af2699aec7df5ec1b334c6a1a06d4f01fc3449d70e5214ce78736 |
| frontend/src/pages/chat/components/RunStatusLabel.tsx | 3aa7097a27bfc648b6f64c146381dc9edb0fc9fefec9a1355707d44770df19f2 |
| frontend/src/stores/sessionProjection.ts | 6a87cc3032f55e1d6973602146a4f9487e6ddde50c0d00dc55bceafc59adfc8b |

## E-035 · Vision smoke03 成功样本

- 授权：用户明确“授权。就是用现在这个dev是吧”；执行前再次告知1合成图片上传、至多2生成、各1024输出tokens、无重试。2026-09-05 21:15:15–21:15:25 +08:00，deepseek/deepseek-v4-flash-vision-exp/default，独立临时会话与合成工具。当前授权已消费完，不覆盖额外运行。
- 实测：1 Files + 2 Responses，均HTTP200；首item added=final_answer、done=commentary，本次被正常接收；工具执行1次，第二响应final_answer，包含cobalt-115。产品phase与rollout回放均[commentary, final_answer]，业务消息ID一致，validation末值valid，passed=true。
- 用量：两次prompt_tokens分别6651/6744，completion_tokens73/19，cached_prompt_tokens6528/6656；总输出92，reasoning_state_present均false。只证明usage/cache可观测，不推断缓存率泛化或reasoning正文保真已由实测覆盖。
- 证据：evidence/vision-smoke-20260905-03.json，50126 bytes，SHA256 5856a8962200e6e0169542b6b0539475d2506873a58df0f9e828ee2dd1889f12。report绑定baseline HEAD及当时源码hash；未保存完整dirty patch/raw SSE正文，不能声称完整原始流可重放。
- 置信度：runtime-supported/confirmed（该样本闭环）；真实模型路径经过SessionManager/Conversation/LLMClient，不经过桌面UI与Runtime计时，不能据此单独认定桌面模型E2E。01/02失败不覆盖、不反向归因；不外推Pro/K3/所有Vision请求稳定性。

## E-036 · 115真实WebView验收与引用落点修正

- 用户授权旧049→本115实例切换。旧lease PID2676经dev stop结束；本worktree feature/115-assistant-phase-execution-disclosure，lease PID2824，Tauri PID48440，Bridge PID36112，voice PID34092，Vite PID41048。Tauri/Bridge/Vite于21:15–21:16 +08启动，晚于后端协议/Runtime源码修改；后续只修前端定位，探针在每次开始重载WebView，从当前Vite加载。CDP选择http://localhost:1420/chat.html，CSS viewport1055×758，截图保留完整WebView。
- 夹具：scripts/assistant-phase-desktop/run.* seed新建合成会话5b39770e-29cd-4312-a428-9ee83587cb48，native/legacy/interrupted三轮、固定112秒/8秒/未知，当前持久模型K3。未编辑已有会话、未调用模型；普通JSONL产品事件经Bridge真实历史API加载。fixture manifest见evidence/desktop-fixture-20260905.json。
- 通过：native仅一个开关控制被unknown隔开的两个过程段；unknown/final可见；legacy显示8秒但无折叠按钮；interrupted内容可见且用时未知；WebView重载按该轮事实恢复，不按当前K3重判。真实Ctrl+F/搜索/Enter打开commentary，实际选择正文并点击引用，来源block ID及引文一致。
- 运行计时边界：只向当前合成会话注入UI-only RunSnapshot/临时消息，真实WebView观察legacy秒数增长、stopping继续、terminal固定，并截图；没有向后台提交或取消任务，不把它当成Runtime/模型全链计时证据。Runtime终态与持久化由离线集成测试负责。临时clock/reference消息和本探针生成的引用在finally清理，持久合成会话保留供用户手验。
- 探针历史：01用父innerText遗漏Shadow DOM正文，截图证明实际可见；02局部通过。03引用后检查误用draft.ts而失败（真实模块drafts.ts）；04增加计时检查通过。05沿用上次手动open状态导致初始closed断言失败；06改为初始重载并通过DOM断言。上述失败属于测量/初始化问题，均保留，不改产品来迎合错误断言。
- 产品反证与修正：06只有DOM展开/高亮成立，截图显示引用原文仍不在可视区，故该条可视定位结论superseded。07补4秒内几何收敛断言，source y=-206.41、h=21，失败成立。代码确认引用分支缺少搜索已有的detach，并只在展开前定位虚拟行；display:contents锚点不能直接量自身零矩形。115修正引用导航解除auto-follow/旧布局保持，展开后测实际子元素并扣除Composer占位定位；搜索共用几何helper，有确定性单测。
- 08相同夹具/同一断言通过：source y=182.39、h=21，在可视区且高亮；完整截图经检查。runtime-supported + code-supported + reproduction-supported / confirmed（该引用落点问题及本场景修正）。未对相邻无关popover布局或所有滚动路径作外推。
- 证据：evidence/desktop-20260905-01至08内独立report与截图，均不覆盖。08 report SHA256 308342de49e8d888a07193a91479b32437cbc91e88feb1efcb060a48d4e05188（1343 bytes）；reference-reveal.png f7a2a2079a95341a5fa4362ee4e0102ec2390afb549f259792ecbb3e3b2255f8；live-stopping.png 039d0099d8298d1a9b6f797f1542f8a9f568b30394918c9fa098eddbc8994e42。
- 代码锚点（本机字节SHA256）：最终probe.mjs a33c65ad5918fd6f7806695aa377e840c25fcc601521125fc5535cb94bb409fb；run.py c537790b5fc24774d743362cfe7f315baa6619b0e3cd873657b5e95a807e1bbe；MessageList.tsx 846fa974b5c0e0c0330da6544e361a179de0bf8aa1f03988f819eb6b950ac44a；searchNavigation.ts af3ba529f4c856bd2cf6ae7af7b517fd231b98b7fbe02866d46fc99d4b9a049c。未提交overlay，不是clean commit锚点。
- 隐私/层级：真实desktop DOM/interaction与WebView screenshot；UI-only注入均明确标注，不宣称真实模型桌面product E2E。截图包含本机侧栏会话标题，仅内部验收资料，公开分享需另行脱敏。全部新增桌面探针为0真实模型调用。
- 最终对账：新增harness及引用修正后，backend总入口2544 passed/4 skipped，ruff715 files及mypy616 files；frontend总入口124文件/761测试及生产构建；Rust218+6 passed/1 ignored及release check，全部退出0。requirement/design仍CONFIRMED，progress IN_PROGRESS，仅等待用户验收与后续Git交付；没有把本次“授权运行”当成“验收通过”。

## E-037 · 独立计时/尾部活动修订与蓝点延期

- 来源：2026-09-05用户手验截图及说明，随后对局部 requirement declare 回复“符合”，并明确“蓝点确实没必要本次修，去加个issue，顺着本次需求合入”。human-reviewed / confirmed（决定）/ active。
- 决定：顶部用时行只负责时间与可用的折叠入口；当前活动跟随正文/工具进展出现在尾部，不留旧加载提示。用户手动收起过程后，当前等待/取消等状态仍在折叠外可见；工具和审批保留既有专用状态，避免重复加载。实际终态移除活动提示，时间固定，结果仍可见。
- 现状与演进：RunStatusLabel.tsx调用useModelResponseStatusLabel并把activity文案放入ExecutionDisclosure的label；MessageList.tsx撤掉了原有尾部MessagePrimaryActivityStatus。此合并布局需要修订；E-031/E-033的时间口径与单轮入口继续有效，合并瞬时活动文案的呈现由本条supersede。E-034/E-036的机制/历史观察保留，不把旧布局已测当成新布局已通过。
- 蓝点：只读源代码及WebView状态、隔离三函数检查指向基线既有的已读依据缺失和异步读取竞态，详见[issue060](../../issues/060-session-read-state-reappears/README.md)。未保存用户正文或完整窗口截图；原用户附件在本机Temp，非长期复现artifact。问题只登记为open，后续随115提交文档，不包含生产修复。
- 边界与下一步：requirement CONFIRMED、design DRAFT、progress IN_PROGRESS，先确认展示职责分离方案；只重验受影响的活动/折叠/滚动与计时呈现。底层阶段、Provider选择、Runtime时间、模型历史和业务消费范围不变，本次没有新增模型调用或提前Git合入授权；用户已明确issue060文档纳入115后续交付范围。

## E-038 · 展示职责分离方案确认

- 来源：用户对Phase 2 declare回复“可以的”。human-reviewed / confirmed（决定）/ active。
- 确认：顶部纯计时/开关、折叠外尾部活动、独立失败/取消结果，复用既有主活动组件与状态选择，不改变phase/Provider/计时持久化。design恢复CONFIRMED，沿用有效的实现授权。
- 边界：没有把本次方案确认当作实现验收；没有新增真实LLM授权。蓝点issue060仍延期，只有文档属于本期提交范围。

## E-039 · 独立计时/尾部活动实现与复验

- 版本：baseline c863ac793717f3b2335993a17471b5531d1326e4加本115未提交overlay。RunStatusLabel重命名并收窄为RunDurationLabel；MessageList在ExecutionDisclosure外的内容尾部渲染MessageActivityTail。组件复用MessagePrimaryActivityStatus/ModelResponseStatus，终态优先移除旧活动及其定时器，工具/compaction/审批不再生成重复模型等待，已有错误正文不再重复泛化失败。
- 源码SHA256（本机字节，不是clean commit）：RunDurationLabel.tsx `5868f46dd5112b6eba7131c022671cf68d091abcee0a111b4780bee094218afa`；activity/MessagePrimaryActivity.tsx `1bbea605d16ba5fe0a0c088582158c07b5caf2cd32943d49accfc86090b23ba5`；MessageList.tsx `7c9bdcbd3794d085ac20db319d10b2abe8f85661ddd96ad275489946b46b20e6`。
- 确定性验证：scripts/frontend/test.ps1定向5文件22测试通过；scripts/check/run.ps1 --stage frontend为125文件773测试，所有静态门禁与构建通过（74.8秒、退出0）。覆盖纯时间无活动计时器、stopping继续/终态固定、活动替换、终态移除定时器、卡片/dock无重复fallback、legacy/error/interrupted。Rust stage fmt/test/release check通过，218+6 passed/1 ignored（6.2秒、退出0）；backend stage在no_external_http guard下2544 passed/4 skipped，ruff715文件及mypy616文件通过（总入口203.5秒、退出0）。既有duplicate ZIP fixture、bilibili_api退出清理、CSS highlight/bundle体积和Rust unused提示不掩盖退出码，均为非失败提示。
- 桌面身份：复用E-036的115 Tauri lease PID2824、app48440、Bridge36112、Vite41048，doctor/status核对本worktree/branch/端口一致。只改前端代码；probe09先重载真实 `/chat.html`，加载当前Vite源码，后台源码未变。
- 执行：scripts/assistant-phase-desktop/run.ps1 probe --fixture docs/requirements/115-assistant-phase-execution-disclosure/evidence/desktop-fixture-20260905.json --output-dir docs/requirements/115-assistant-phase-execution-disclosure/evidence/desktop-20260905-09，2026-09-05约21:50 +08。复用既有持久合成会话；UI-only clock与native activity使用独立run，未在同一run内切换冻结mode，未请求模型/真实工具，退出0。
- 观察：开始等待时头部只有用时；文字→工具运行时无重复模型等待；工具完成后尾部显示整理结果。CSS viewport1055×758下，duration y427.40/h15.99、text y452.99/h21、tool y483.59/h23.99、tail y516.17/h15.99，顺序与上下间隔断言通过。手动收起后文字/工具隐藏，尾部等待仍可见，按钮只含用时；终态后尾部活动及primary marker消失。legacy计时、取消继续/终态固定及原有折叠/搜索/引用回归均通过；引用原文y182.39在可视区。
- 证据层级：code-supported + deterministic-tested + desktop DOM/interaction/screenshot / confirmed（上述合成机制与布局）；已检查activity-after-tool、activity-collapsed及terminal-fixed完整WebView截图。不宣称新增真实模型桌面E2E；其它平台未验证。finally清除本探针临时clock/activity/reference消息和引用，持久合成会话保留供手验，未改用户真实会话。
- Artifact：evidence/desktop-20260905-09/report.json（2133 bytes，SHA256 `c754242443d59fef86d4b53e5ad619225a4e7055e5e4685b99b90ea9dfd013d5`）；activity-after-tool.png（96754 bytes，`b5d3e7eb06a76d4056129b5197a2c127a272e96f8969d99a3038ab495796e94d`）；activity-collapsed.png（91813 bytes，`810d4e3d39dd02941991d746c25337ac068f2ad5ed40054d0781f7a1fdbd00e8`）。probe.mjs SHA256 `5864540a3e4e96a114b4f3e14fc55a166465e59cb9578c9636ab716660a8c3cb`。旧01–08报告不覆盖；截图含本机侧栏，仅内部验收资料。
- 参考核对：本次通过Context7查阅[React Effect清理](https://github.com/reactjs/react.dev/blob/main/src/content/learn/synchronizing-with-effects.md)，只用于组件卸载/依赖变化清理定时器；没有引入新React API或依赖。用户截图/明确修订拥有产品布局口径，不从闭源桌面外观推断内部实现。
- 演进：E-037的新展示由本条实现/验证覆盖，E-034/E-036合并文案的布局不再是当前候选。保留旧协议/时间/引用证据与失败记录；用户最终验收和合入仍未完成。

## E-040 · 用时入口排版、分隔线与无hover变化

- 用户先要求移除两侧padding，随后打断并明确：按DOM让文字与正文对齐、相同字号、增加横线分隔、hover不变。只细化既有入口的视觉，不改变功能/协议；前一次清零尝试已被当前指令替代。
- 只读DOM：本115 Tauri `/chat.html`、CSS viewport1055×758。原清零入口12px/16px、x279.60；正文14px/21px、x281.20，两层透明边框各约0.8px带来内缩。遵循tdesign-chat-style核对实际text variant与项目font-size-sm变量，没有修改正文气泡、node_modules或注入Shadow DOM样式。
- 实现：ExecutionDisclosure使用text-sm/leading-normal/font-normal、px-0.5及匹配的has-svg覆盖；信息行同样处理。复用项目Separator置于入口下方，ghost按钮覆盖hover背景为透明、文字保留muted，键盘focus-visible保留。
- 最终DOM：入口和正文均14px/21px，入口padding左右2px；文字x281.60与正文x281.20相差约0.4CSSpx（验收容差小于1CSSpx，考虑WebView设备像素取整，不声称所有DPI绝对零偏差）。hover前后color均rgb(107,114,128)、background透明、shadow=none，字号/行高/内边距也无变化；分隔线在入口与正文之间，高1px。完整截图已检查。
- 验证：ExecutionDisclosure.test覆盖正文尺寸类、两种入口padding、has-svg覆盖、无hover变色和一个分隔线；check frontend全量125文件773测试、静态门禁及构建通过，88.1秒、退出0。后端/Rust生产输入未变，沿用E-039证据；无新LLM请求。
- probe10保留：入口字号/对齐/分隔/hover、折叠和重载断言通过，但随后等待搜索结果30秒超时；失败截图显示搜索弹窗已消失，没有足够时序证据唯一归因于用户操作、HMR或产品逻辑，整轮passed=false。报告2813 bytes，SHA256 `29169200923190fbd08f34bba227b291239dfebfcc9c89101479b2fcd9a3c6ea`；不修改搜索代码或把此报告写成通过。
- probe11：前端构建结束、无生产源码变更后重跑，只增加首次截图上方留白；所有入口样式、展开/收起、历史、搜索/引用、独立计时和尾部活动检查通过，退出0。report.json位于evidence/desktop-20260905-11/，3226 bytes、SHA256 `10b0f0c6f26ef2e5a91ae7f59ec81a3ed753ca3e5e082c968e84107c80493a25`；header-hover.png为93227 bytes、SHA256 `deee51b721db6923c7951345cce5225640b679af32cef5c46f4bd1c26a3df7e9`。未覆盖旧报告，截图仅内部资料。
- 最终源码锚点：baseline c863ac793717f3b2335993a17471b5531d1326e4加115 overlay；ExecutionDisclosure.tsx SHA256 `399736e9efe39ba130c526fa6c71371d8b450e43627374fd9419ecb002880d08`；probe.mjs SHA256 `33103a1f6297b8af704c5c921fbb58313bd1b6437c4ee4f27324a495b1848cb0`。运行态仍为E-039的同一dev，探针重载加载当前Vite，后台源码未变；复用既有合成会话，不创建真实模型会话。
- 置信度：code-supported + deterministic-tested + desktop DOM/interaction/screenshot / confirmed（本次视觉与合成行为）。probe10关闭弹窗原因unconfirmed，不能据probe11通过宣称已修复该未知原因；其它平台/DPI与真实模型E2E不外推。

## E-041 · 顶部运行/中性停止/完成摘要确认

- 来源：用户提出顶部“已进行xx”、停止终态替代尾部停止行及更细分隔线；后续明确“只说被停止，不说是被谁停止，xx后被停止”。human-reviewed / confirmed（产品决定）/ active。
- 决定：顶部运行中“已进行xx”、停止后“xx后被停止”、成功“用时xx”；停止未知用时为“已停止，用时未知”。不从停止结果推断停止者，不为这段文案新增原因透传。发出取消请求未真正结束时仍运行并计时；失败/过期等结果保留原语义。当前模型加载/工具进度仍在尾部，只移除独立停止结果行。
- 代码核对：现有RunSnapshot包含状态/结果及时间，ChatMessage也已有runOutcome/status与持久时间；runTiming已按真实起止时间计时。后端还保存reason_code，但本次中性文案不需要消费它；此前因“你停止了”而提出的原因透传方向已放弃。
- 流程：requirement局部CONFIRMED，design仍DRAFT，progress R8待简化方案确认后实施。未改生产代码、未做新模型调用或桌面重载；E-039/E-040旧结果不能作为本次摘要行为已通过的证据。

## E-042 · 简化前端方案确认

- 用户对仅复用现有状态/时间、移除尾部停止提示、局部减细分隔线的design declare回复“对”。human-reviewed / confirmed（决定）/ active。design恢复CONFIRMED，继续已有实现授权；不改后台/Bridge/历史格式，失败与过期等结果不误并为停止，真实终态才停表。

## E-043 · 运行/中性停止/完成摘要与半像素细线

- 候选：baseline c863ac793717f3b2335993a17471b5531d1326e4加115未提交overlay。RunDurationLabel优先可信queued/running快照，再使用既有runOutcome/status；runTiming格式化“已进行xx / xx后被停止 / 用时xx”及未知用时。MessageActivityTail不再输出停止终态行，但继续保留失败/过期等结果与运行中的当前活动。没有Provider或reason_code分支，没有新增后台字段。
- 确定性验证：定向4文件30测试通过；包括真实projectSessionEvents投影后重载/恢复缺时、明确失败等结果不误判停止、活跃快照覆盖旧interrupted、四种取消原因得到同一中性文案、小时/分钟格式化、取消请求继续计时、终态固定/计时器清理、尾部去重。最终check frontend125文件786测试、lint/typecheck/token检查及build通过（81.0秒、退出0）。backend/Rust输入未改，沿用E-039的验证而不声称本轮重跑；未新增真实模型调用。
- 首版细线失败：第一轮frontend786测试/构建通过（91.6秒），但probe12的半像素断言失败，实际height=1。DOM显示自定义h-chat-run-divider与默认data-horizontal:h-px同时保留；0.5px变量和对应CSS已存在，最终被默认1px规则覆盖，属于类名合并/级联问题，不是模型或协议问题。报告evidence/desktop-20260905-12/report.json（1862 bytes，SHA256 `ed53c9807f97c7a01011f4f533bba9d8b35f8a6049fb1073f7013eb245828d8a`）保留为失败。
- 修正：删除未能安全覆盖的自定义高度utility映射，只在本Separator实例通过style.height引用`--size-chat-run-divider`；token=0.5px，通用Separator及其它分隔线不变。增加变量引用断言，真实WebView复测computed height与bounding height均0.5px。Context7核对过Tailwind主题变量/inline规则，CSS生成本身有效；最终无需修改全局类名合并器或通用组件。
- 桌面身份：同115 Tauri dev，lease2824/app48440/Bridge36112/Vite41048，status确认worktree/branch/端口匹配。只改前端，等待最终构建退出后执行probe13并重载真实chat WebView；CSS viewport1055×752。复用既有合成会话，实时状态是UI-only独立run注入，0真实模型/工具调用。
- 实测：运行及取消请求中为“已进行”，计时继续；终态后顶部为“6秒 后被停止”且固定，无尾部停止行或残留活动。历史合成中断为“已停止，用时未知”，成功仍为“用时1分52秒/用时8秒”。字体14px/行高21px、文字起点偏差约0.4CSSpx、hover不变；分隔线height=0.5。原有折叠、unknown/final、搜索引用、尾部活动及可视定位全部通过，退出0；terminal-fixed/live-stopping/header-hover完整截图已检查。证据层级为desktop DOM/interaction/screenshot，不外推真实模型E2E或其它平台。
- Artifact：evidence/desktop-20260905-13/report.json（3275 bytes，SHA256 `c65d074763a43e4f012dbe91e60fef008c7d5ad1076824ba25d75f957f3e583e`）；terminal-fixed.png（92253 bytes，`33a1b357bf3afae2faec82b0a21b98a2dde3526641c0c1e2e52cf924c63cb196`）；header-hover.png（93297 bytes，`fe80b1cf8bd56371be7a0dddc200c1f64fb38dbeecde577d633a5f94fa1fa838`）。finally清理临时UI状态和引用，合成历史会话保留供手验；截图包含本机侧栏，仅内部资料。
- 最终本机字节SHA256：runTiming.ts `29cc8c6eb17da9451d3a7106208ae47cd0d48177751fe3aefd771c716f8eadde`；RunDurationLabel.tsx `d7e02ade52d9286f93d98ea9b5cbcdc7da4eea55df15fb696ef5dff3af403d4f`；ExecutionDisclosure.tsx `4e574f12dc8163284b142078a24b4507326630c024d7e71a7cdfcf9b88600f9c`；activity/MessagePrimaryActivity.tsx `eb34caca9b0b8ea549167591598578c443ee5a5f5522ae4899e51fb9d84c7b19`；styles/theme/tokens.css `2d9352cc7fe4bee90e34f5f489ebbca9aafe70b9862a3e294a84adb293082c8b`；probe.mjs `32548e826b73d62abcd013d8330819780bd0b8fab2a705c19ba8c21604f4058d`。未提交overlay，不伪装为clean commit。
- 判断：code-supported + deterministic-tested + runtime-supported / confirmed（本次中性摘要、计时及细线）；失败probe12不覆盖，旧E-039尾部停止展示被本候选替代。R8实现验证完成，用户最终验收/合入尚未完成；issue060仍仅文档随本需求交付。

## E-044 · 发送初始计时未知跳变与确认修复

- 来源：用户手验报告“刚发出时用时未知，稍后已进行0/1秒”。代码支持：conversation.optimisticRunSnapshot创建queued、started_at=null、version=0；原RunDurationLabel只区分running/stopped，其余走finished，elapsedRunSeconds返回null后显示“用时未知”。这是前端展示分支遗漏，不由Provider phase或模型加载时间决定；未重新读取用户私有会话。
- 覆盖缺口：旧单测明确把queued断言成未知，desktop probe13的实时用时直接从已running且开始于4秒前的快照进入；该证据没有覆盖初始发送到接纳/开始的间隙。E-043其余文案/历史/细线/交互证据保留，不能外推为本过渡通过。
- 用户确认：对“尚未开始隐藏计时行与分隔线、只保留尾部加载；实际开始显示真实计时；历史或异常缺时仍未知”的修复方案回复“可以”。同一已确认最小修订同步requirement/design，沿用115分支和既有实现授权；不改变排队计时口径，不新增状态/时间事实，不发真实模型请求。
- 实现：RunExecutionDisclosure集中使用既有可信快照和校准门禁，queued及尚无开始/终态的streaming占位暂不挂载计时头/Separator/计时器；正文、执行区及尾部不被条件卸载。RunDurationLabel仍只负责小范围每秒刷新，以实际started_at为key初始化当前时刻，不保留排队时的旧now。运行异常或终态缺时间仍显示原未知，queued/running继续优先于旧interrupted标记。
- 首轮确定性验证：4文件37测试通过，覆盖无快照/校准、version0/1排队、15秒等待后从实际开始显示0→1秒、两类折叠能力、正文DOM身份不变、排队取消/拒绝/失败、运行起点非法/缺失、原终态与历史回放。frontend793测试通过，完整构建与桌面复验尚待结束；最终结果另条追加，不把运行中写成通过。

## E-045 · 初始计时显隐实现与完整复验

- 最终范围：仅frontend展示、既有桌面合成探针及115文档。RunExecutionDisclosure复用现有校准/快照，queued或无正文/起止/终态的初始占位不挂载计时头与Separator；已有部分正文但丢失时间的记录继续明确未知，不因无快照误隐藏。RunDurationLabel仍按真实起止时间计时；没有新增Provider分支、状态源、后台字段或计费请求。
- 确定性验证：首次frontend793测试/构建通过（149.9秒），其间补充已有正文缺时兼容后最终定向4文件38测试通过；最终`./scripts/check/run.ps1 --stage frontend`共125文件794测试、lint/typecheck/颜色/token/组件来源门禁及构建通过（111.8秒、退出0）。backend/Rust输入未变，沿用E-039，不冒充本轮重跑。`git diff --check`通过；未构建安装包。
- 源码锚点：c863ac793717f3b2335993a17471b5531d1326e4 + 115未提交overlay，本机字节SHA256：RunExecutionDisclosure.tsx `0fddec453a8620b2b9d410955023a890f9bf8e0b3da649406972077334093a0c`；RunDurationLabel.tsx `1ad0078b59dff8201064b14f2961c3ba652bfc96f18857cba6253f015dbea2ab`；ExecutionDisclosure.tsx `1de1490e4f61104b49bca89b6a88180aeb5e6ea9302dc90d486584b88c5dc103`；runTiming.ts `385b7e59beb68f27ecfefde887545dc5611590a013fef22542dea51b4218402c`。不是clean commit。
- 桌面身份：dev status再次确认2df5/feature115/Tauri，lease2824/app48440/Bridge36112/Vite41048，端口1420/1421/9222/18800/18900。frontend源码最后改动23:03:41，探针重载真实`http://localhost:1420/chat.html`加载当前源码；无后台改动。CSS viewport1050×920（窗口由实际运行态决定，未强制固定小viewport）。保留同一合成历史会话，只注入临时UI run；0真实LLM/工具请求。
- probe14（retained）：发送/接纳均queued、无头/分隔线且尾部可见；排队2404ms后连续样本为已进行0秒，原有交互断言通过，退出0。但just-started截图捕获时尾部靠近输入框；探针在搜索/引用后只做DOM scroll，没有显式请求恢复跟随，不能把该截图当作完整可视布局通过。report.json位于evidence/desktop-20260905-14/，9019 bytes，SHA256 `f24f6b3635007eff9d3127d60240f4b3295cbaa3f8ccbd0cb73f14734aa9ac18`；旧报告/截图不覆盖。
- probe15（active）：仅改harness，在UI-only插入后通过真实“滚到底部”按钮恢复跟随，再检查头部出现后的尾部与输入区几何；未改生产滚动逻辑。排队2497ms不显示计时头/分隔线，开始后约300ms内79个动画帧均为“已进行0秒”、分隔线同时可见。尾部bottom≈694.69，输入区top≈726.40，间隔约31.71CSSpx；开始前/后完整截图已人工检查。停止后固定、取消请求继续计时、历史unknown、折叠/搜索/引用/hover/0.5px细线与原有尾部时序均通过，退出0。
- Artifact：evidence/desktop-20260905-15/report.json（9957 bytes，SHA256 `5fef1deb8b64ac32c364e60cd3c22c0e555ea69a25a3dcfbab7faf48f1e8567a`）；before-start.png（109873 bytes，`124f7f694d30dede3ea5e0e48411810fa01ff80aef1d45365d74e19274a6f129`）；just-started.png（109505 bytes，`9d6b2c2b2624f5a9bba743e910cba8043c63f9b85209f22139a6e3225fb1ee23`）。最终probe.mjs SHA256 `9f6efaba0ceaf21d0916a4f776a05cef66f35d565c15fab17115334faf57fe00`；14的harness为`ca16d32fc9da90eafd3b6ff0019de5cd77ce4da3bfb779bcb80ce055d9c72815`。截图含本机侧栏，仅内部验收资料。
- 结论：code-supported + deterministic-tested + desktop DOM/interaction/screenshot / confirmed（本次展示与合成过渡）。两轮finally清理临时clock/activity/reference状态与合成引用，持久合成会话保留供手验。未使用真实发送/模型业务链路，不外推真实模型桌面E2E、其它平台或全部运行时序。R9完成；T8用户最终验收/合入尚未完成，dev继续保留。

## E-046 · 用户验收与主干交付确认

- 用户明确要求先完成115合入main并推送远端，再回到会话锁问题。视为115用户验收与本次正常Git交付授权；此前T8待用户确认的门槛解除，仍须最新主干复验与Guide门禁，不能在执行前写成已合入。
- 刚发现的长会话保存锁超时仅完成只读诊断，不在115实施存储修复。store.py/rollout.py/meta.py相对基线没有115修改；未重发该业务模型请求或工具操作。该问题合入后另行推进，不能把115验收写成它已修复。
- 当前全部dirty/untracked已核对属于115实现、测试、文档和合成证据，以及用户明确要求同行合入的issue060文档。后者保持open，不顺带修蓝点。远端产品仓库确认为PRIVATE；公开Guide只发布用户使用说明，不带本机截图/会话/日志。
- 最新origin/main为62db58fd5fb46cfea51c0737a4151d4a293f7d9f，包含049与工作流契约更新。公开Guide基线722ceb0在该main内，需先同步main再做祖先与diff复核。Guide受管checkout干净、origin正确，既有r29已immutable发布；当前dirty overlay仅是预览，不写入公开来源锚点。
- 同步主干前查询/v1/runs?includeRecentTerminal=false为空，按桌面生命周期规则停止本任务dev；保留原会话/日志/证据，不将接下来测试写成新桌面E2E。JSON/PNG证据使用局部gitattributes保留原始字节，避免跨平台checkout改变完整性校验。
