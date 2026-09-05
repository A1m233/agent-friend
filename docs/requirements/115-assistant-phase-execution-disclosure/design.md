# 115 · Assistant 消息阶段与执行过程折叠技术方案

## 状态

CONFIRMED

当前有效状态：用户回复“可以”确认并授权的发送/排队计时头显隐修复已实现，requirement/design均CONFIRMED。E-045前端与真实WebView合成过渡复验通过，等待用户最终验收；未扩展运行状态源、时间字段或后台协议，历史和异常缺时保持未知。E-043此前摘要/细线验证保留，但不能代替本次初始发送过渡证据。

回溯历史：真实 Vision smoke 02 观察到同一原生 item 的 phase 在开始/完成之间变化，曾回到 Phase 1/2 review。旧方案要求 phase 不变并逐连续段设折叠按钮，本修订分别替代为生命周期更新和统一轮级入口；失败证据 E-029 及此前验证仍保留。

用户于 2026-09-05 确认设计并明确授权“写完就开始实现”。需求见 [requirement.md](requirement.md)。实现进度见 [progress.md](progress.md)，代码检查和来源见 [evidence-index.md](evidence-index.md)。

## 现状分析

114 的 ResponsesState 内部维护 item identity，但输出仅有带 phase 的文本 delta；ProviderAssistantTurn 又合并所有文本。Conversation 抹掉 phase 并按一次模型调用保存一条 assistant_message。AG-UI、push 与历史各自生成文本 ID，无法可靠关联同一 assistant item。

会话产品事件与 rollout 同在 session JSONL。Session.active_events 排除 rollout 内部事件、应用编辑重发失活标记，负责产品历史；SessionModelHistory 与完整 rollout commit 负责模型回放。现有 _RunModelSnapshot 已冻结当轮 config/client。runtime 的 run_terminal 是持久终态，UI 还有实时 RunSnapshot；二者均不能由 assistant phase 替代。

## 方案设计

### 模块与文件

| 范围 | 修改 |
| --- | --- |
| llm_providers/stream_events、responses、client、catalog | 统一 assistant item 身份与完成文本；保真回放；Chat 字段兼容；DeepSeek 三模型能力 |
| agent/assistant_phase/ | 集中阶段归一化、文本分段累积和逐 run 语义校验；不含 Provider 分支 |
| agent/conversation、conversation_events、messages | 所有流式入口使用同一累积器；统一业务 message ID、阶段事件与持久化 |
| agent/system_prompt 与 prompt_sections | 独立阶段行为资源，经冻结能力启用；不修改内部 JSON/摘要 Prompt |
| agent/sessions | phase 产品事件、Message 投影、分段模型历史、rollout 保真与 active projection |
| agent/runtime、conversation、sessions/recovery | 统一实际开始/结束时间，写入产品事实；失联核对不虚构原结束时间、不续跑 |
| agent_bridge/protocols/ag_ui、session_bridge、protocols/im | 同 ID 阶段透传；IM 聚合来源关联与引用 fallback |
| frontend/types、stores、chat/components | 三条事件入口统一文本身份与 run 阶段投影；执行区折叠、搜索/引用展开、现有活动组件协作 |
| scripts 与各模块 tests | 离线回归、Vision 合成 smoke 的模型/上传/request 预算护栏 |

### Provider item 协议

LLM 文本事件保留既有 text/phase 字段，并增加可选 item_id 与顺序信息。新增统一 assistant item 开始/完成事件，完整响应保留有序 assistant message items；已有 content 聚合入口继续提供完整文本。工具和 reasoning 仍沿各自既有类型传递。

Responses adapter 从原生 item 映射身份、phase 和顺序。缺失/非法 phase 保留 None，不按能力补值。同 phase 的两个 item 仍有不同身份；交错 delta 按 ID 归位。开始值仅是当前快照，不锁死 phase；item.done 更新该身份的 phase，完整 response.completed 再以其 output 快照定稿。两种完成快照仅 phase 不同不构成协议错误；身份、正文、工具参数及重复完成结构仍单独严格校验。缺失/非法完成值归一为未知，不能继承旧合法 phase 冒充完成值。

LLMAssistantItemDone 携带完成 phase；完整 ProviderAssistantTurn.text_items 携带最终 phase。Agent 消费二者时只更新同一个业务消息的元数据，发出独立 AssistantMessagePhase 事件，不重播正文、start/end 或触发新工具。未收到完整 response 的中断流不当作成功完成，保留已知阶段和 partial 可见性。

Chat 路径及旧 fake 没有 item 事件时，以一次模型 response 的边界建立一个 unknown 文本 item；兼容归一只根据统一事件是否有身份，不检查厂商。空 start 不产生正文，失败重试不能使空 item 残留为可见消息。仅增加元数据边界不改变“已经输出业务正文”的重试/请求接纳判定。

ProviderAssistantTurn 保留多 item 和完整工具参数。模型历史转换生成多个 assistant Message，并按来源顺序关联工具容器；reasoning state 仅绑定一次。Responses 请求保留各条 assistant phase；Chat 请求转换在 llm_providers 内移除不支持的 phase 字段，保留正文、工具及合法 reasoning。

### Agent、身份和持久化

新增集中于 agent/assistant_phase 的累积器：按 provider-neutral item ID 保存文本、原始归一 phase 和顺序，在第一次可见文本前分配业务 message UUID。该 UUID 同时用于 TextDelta/message 生命周期、assistant_message.uuid 和模型 Message 的来源关联，避免 Bridge/前端重造身份。

每次模型响应完成后，先将完整 response 的 phase 按 item 身份对齐到累积器，再语义校验并沿既有提交时机持久化完整 assistant items；不能用开始值反向覆盖 ProviderAssistantTurn。取消/错误时持久化尚未提交的文本为 partial，并保留每段身份。空 item 不制造空聊天气泡。模型调用完整终态与 run 终态分离。

逐 run 能力使用冻结的 _RunModelSnapshot。阶段模式为 native 或 legacy；写入同一 session 产品事件流的 assistant_phase 事件，关联稳定 run_id。事件表达模式和阶段校验状态（pending、valid、missing_final、conflict），不保存 isFoldable 或手动 open。阶段状态改变以追加事件表达，active projection 按 run 恢复最新状态。旧 session 无此事实时默认 legacy。

能力快照与 assistant 原生 phase 是不同事实。即使 legacy run 偶然收到原生 phase，也保留原始 metadata 用于模型回放；产品仍依该轮 legacy 策略显示。future catalog 改变、会话切模型或进程重启均不追溯重判。

run 的阶段校验检查有正文的 final、后续 commentary/unknown 文本、工具及未完整 item。final 后仍有工作时整轮标为 conflict，禁用自动折叠；不改写历史 assistant phase。没有可信 final 标为 missing_final；不为补齐 final 自动增发请求。取消/错误没有成功 run_terminal，即使曾见 final 也不能自动收起。

同一累积结果生成产品文本和模型历史，rollout 序列化 Message metadata 保留原生 phase。产品 UI 不读 rollout；模型恢复不从前端或折叠状态重建。幂等回放按原有业务 ID 和阶段重新发事件，不重新生成或执行工具。

### Prompt

阶段指令放在 agent.prompt_sections，装配入口属于 agent.system_prompt。冻结的 native 能力决定是否加入固定资源；不在 provider 注入业务 Prompt。内容规定 commentary 是简短的可见进度，final 是本轮真实收尾，正常直接回答不强制 preamble，严格 JSON/其它用户输出格式要求仍有效。

阶段指令只服务 Conversation 的交互输出。Memory extractor、reconciliation 和上下文 summarizer 仍通过各自 generate 请求及专用 Prompt 工作。固定资源保持稳定，模型/能力切换导致既有 cache epoch 的合理变化；不引入每片段变化的 Prompt 字符串。

### Bridge、实时流和前端

ConversationEvent 增加 item 生命周期、纯消息阶段更新及 run 阶段状态，TextDelta 携带业务 message ID、phase。AG-UI 使用既有 TEXT_MESSAGE_START/CONTENT/END 及项目 CustomEvent 表达附加 phase/run metadata；push 继续序列化同一 ConversationEvent。Bridge 不选择阶段或过滤正文。

前端 TextBlock 增加 phase，ChatMessage 增加本 run 的阶段状态。历史 projection、AG-UI 和 push 均归一到相同字段；legacy payload 缺身份时保留已有 fallback。阶段更新按稳定 block ID 原位修改，不能移除再按文本序号插入而跨过工具卡。持久 run_terminal 与实时 terminal snapshot 都只结束自己的 run；迟到事件不复活消息或改变更高版本状态。

阶段展示由一个纯投影函数读取该 ChatMessage 的 run 模式、校验、终态和 blocks。native 且无 conflict 时把 commentary 与可收纳工具活动按原顺序组织执行区；final/unknown、固定提醒、错误与交互式授权卡保持区外可见。unknown 或交互卡作为连续区间边界，避免为了一个折叠容器打乱时序。

复用现有 Collapsible、Button、ToolActivity、TextBubble 和主活动选择，领域状态留在 chat 组件/store。每 run 顶部只有一条用时/轮级状态/折叠行：有折叠资格且存在过程时用 Button 提供箭头、键盘与 aria-expanded，统一控制各连续执行段；否则渲染普通信息元素，没有箭头、点击或按钮 hover，不使用 disabled 按钮灰化整行。该行不消费模型活动文案，仅按现有运行/终态表达用时。字体/行高与正文一致、小内边距对齐，hover不变；下方复用Separator，通过本入口专用的半像素高度token减细，不修改全局Separator默认样式。各段不再各设入口，final/unknown/审批保持原位。运行中默认 open；成功且 valid final 后默认 closed；当前 UI 用户手动覆盖优先，搜索/引用展开所属整轮。无过程不建空容器。failed/cancelled/partial/missing_final/conflict 不自动收起。

MessageList 在已渲染内容之后、ExecutionDisclosure/Collapsible 之外恢复活动尾部。复用现有 primaryActivity 选择及 MessagePrimaryActivityStatus/ModelResponseStatus；模型等待、重试、工具后整理与 run 等待/取消保持单一状态来源，组件随当前活动替换，不写入历史 blocks。工具、compaction、社交授权和 dock 审批继续由各自组件承载，不额外生成模型加载文案。未提供主活动投影的兼容入口仅在 streaming 时沿用原 ModelResponseStatus fallback，不按模型/phase 再判断。

终态优先于残留活动：MessageActivityTail 在真实 runOutcome 或 error/interrupted 消息上移除加载组件及其定时器；成功和停止无额外尾部结果行，失败/过期/未执行/未接纳保持独立提示，有错误正文时不重复泛化失败。活动尾部不受执行区开关影响，手动收起后当前等待/正在取消仍可见。无提示时不留空容器。

RunExecutionDisclosure统一读取同run的可信快照与现有校准门禁：queued（包括version=0的发送占位）隐藏完整计时头和Separator；尚无可信快照、无正文/起止时间/终态的streaming占位同样等待。已有正文或结束事实而缺失快照的记录不是发送占位，运行和终态快照也不是排队；这些历史或异常缺时间仍显示未知。ExecutionDisclosure只控制头部显隐，不卸载正文/执行区域或重置手动开关，尾部活动仍在外部独立渲染。

RunDurationLabel接收同一可信run与message，只负责现有文案和每秒刷新；等待期间不挂载计时标签/定时器，首次真实起点变化以该起点为key初始化当前时刻，避免沿用等待期间的旧now产生一帧未知。running时文案为“已进行xx”；真实停止终态或无快照的既有interrupted历史显示“xx后被停止”，缺时间为“已停止，用时未知”；成功沿用“用时xx”。现存queued/running快照优先于旧消息的interrupted标记，取消请求不提前构成停止；failed/expired/skipped/rejected等明确结果不被interrupted显示状态误归并为停止。格式化不读取reason_code、当前模型或phase，后台/Bridge/历史格式不变，活动变化和折叠不重置计时，不从created_at或组件挂载时间虚构执行起点。

搜索和引用依稳定 message/block ID 在展开后定位。整条复制始终读完整 blocks，开关状态不改变 copy/IM/voice/Memory 数据。折叠变化通知原有虚拟列表测量/滚动机制，不强制把离开底部的用户拉回。

### 逐 run 计时与历史回放

RunHandle 拥有实际 started_at/finished_at，同一组 UTC 时间用于 RunSnapshot 与持久产品事实。实际进入 running 时记录起点；scheduler 在执行及收尾工作实际完成后确定唯一终态时间并通知持久化，不能在 conversation 返回前后分别造两个结束时间。开始事实写入 run_started，结束事实写入既有 run_terminal（含 started_at/finished_at），由 Session.active_events 投影；不写入 Prompt、rollout 或新的全局存储。队列创建时间不作为起点，取消请求只表达 stopping，实际终态才停表。

前端优先使用同一 run 的实时快照，历史使用有效产品事件。运行时每秒刷新当前时刻减 started_at 的显示，结束用 finished_at 减 started_at；不每秒写盘、请求后台或以组件挂载时间重新累计。顶部用时与尾部活动分开渲染，共用既有运行事实而不新增状态源。即使尚无正文、模型不支持 phase 或没有过程，仍按各自条件显示用时与当前活动；发送/排队尚未开始时只显示尾部等待，历史或运行异常缺时间明确未知，不拿排队起点冒充执行起点。

历史回放指重新读取已保存消息、阶段与时间；运行态重连指前端重新订阅仍存活的后台 run。Agent 执行进程丢失后不自动重发模型请求、不重新执行工具或续跑旧轮。启动核对只能补记已有结果或可判定的中断；补记事件的 ts 是核对时刻，不是原结束时间。缺失可信起点/终点、旧记录或异常退出时显示“用时未知”，不将停机期间算入执行。没有匹配存活 run 的历史不能继续走活动计时器。

### 业务兼容

Memory feed 保留完整 assistant item 的原序、来源、partial 过滤和每 run 的观察次数；不把 phase 字面标签加入抽取文本。session 搜索仍覆盖 commentary。分段后 ambient/none 的历史与搜索过滤按整个 run 生效，旧无 run_id 数据保留原 fallback。

IM 的输出正文仍由全部 TextDelta 聚合；引用 alias 增加可选有序来源集合，以产品事件恢复一条聚合消息的正文，旧 alias 保留单事件读取。不会根据 final 选择引用源。来源被编辑失活时按已有 unavailable 语义处理，不取其它轮代替。

语音、CLI/send/eval、提醒和授权续接继续聚合全部 TextDelta。Todo 固定提醒不经过阶段分类；Bedtime ambient 气泡与口型保留全部文本，idle reflection 保持静默。新增元数据事件不能触发意外气泡、重复通知或 observe。

## 验证与影响分析

按 requirement AC 建立确定性测试：分片/交错/同 phase 不同 item、开始→item.done→response.completed 阶段更新、最终快照缺失/非法 phase、真正的身份/正文/工具矛盾、unknown、冲突/partial、run 模型切换矩阵、幂等回放、产品/rollout 一致性、Chat 回放字段过滤、工具/reasoning/usage/cache 边界，以及逐业务消费与前端展开定位。计时覆盖排队、running、等待授权、stopping、真实终态、失败、中断、历史缺时间及重连；统一入口覆盖 native/legacy、无正文/无过程、多个连续段、搜索强制展开和时间不重置。追加独立用时不含活动文案、尾部当前活动替换/终态移除及定时器清理、工具/审批无重复加载的组件测试；真实WebView合成探针覆盖文字→工具→后续等待的DOM顺序/几何、手动收起后尾部可见及结束固定，仍明确UI-only证据边界，不增发真实模型请求。

项目门禁使用 scripts/check 的 backend/frontend/rust 三个独立 stage；离线模型测试使用 fake/MockTransport，禁止意外 HTTPX 外发。对新增 Prompt 资源核对现有 package data 收集，按实际 diff 执行 frozen-runtime 分类，不凭加载规则自动构建安装包。

首次真实 smoke 使用 DeepSeek Vision：一次固定合成图片 Files 上传，至多两次 Responses 生成，每次最多 1024 输出 token；合成工具无外部副作用，关闭自动重试，使用隔离 session/附件目录，不读用户会话。护栏分别限制上传与模型 endpoint/次数。记录实际 revision/overlay、模型/档位、图片 hash、请求数量、phase、工具关联和 usage；只保存脱敏结果。缺少 commentary/final 或失败时报告缺口，不扩大预算。取消/故障主要由确定性验证承担。

桌面真实视觉验收在执行前按项目规则确认，未获授权时只做自动化组件/状态验证并标记视觉未验收。实现、验证和用户验收完成前不合入。

## 变更记录

| 日期 | 内容 | 实施影响 |
| --- | --- | --- |
| 2026-09-05 | 用户确认逐 run 产品事实、统一 item 身份、原生 metadata 保真与整轮冲突降级，并授权开始实现。 | 首次设计落盘，进入 Phase 3 |
| 2026-09-05 | 用户确认生命周期 phase 更新及完整响应定稿、纯元数据透传；新增统一轮级状态/计时入口与 Runtime 时间事实。明确历史回放/运行态重连不含异常退出自动续跑。 | 替代旧 phase 不变约束与分段按钮；重新进入 Phase 3，重验受影响 AC，真实 smoke 另行授权 |
| 2026-09-05 | 用户确认展示职责分离方案：顶部纯计时/开关，复用主活动的尾部提示，终态结果独立保留；不改变phase/时间持久化，蓝点仅登记issue060。 | 局部回到Phase 3；修订组件与桌面探针，重验活动/折叠/滚动及计时呈现 |
| 2026-09-05 | 用户确认简化顶部摘要：复用现有状态/时间，中性“xx后被停止”，不扩展原因字段；移除尾部停止结果，分隔线仅本入口减细。 | R8进入Phase 3，补状态优先级、历史/未知时间、尾部去重及桌面细线验证 |
| 2026-09-05 | 用户确认发送/排队隐藏完整计时头、实际开始再显示的最小前端修复。复用可信run门禁与现有消息状态，正文/手动开关/尾部独立，缺时历史不隐藏。 | R9实施；回归从无快照/optimistic queued/已接纳queued进入running，再核对终态与历史，不扩大真实模型调用 |
