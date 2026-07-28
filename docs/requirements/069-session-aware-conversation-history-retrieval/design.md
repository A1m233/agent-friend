# 069 · 会话感知的历史查询与可靠召回 - 技术方案

## 状态

CONFIRMED

## 需求文档

→ [requirement.md](./requirement.md)

## 1. 现状与问题边界

### 1.1 跨会话正文扫描已经存在

`ConversationHistoryTool` 通过统一 `MemorySearchService` 调用
`SessionHistorySource`。后者会遍历 `SessionStore.list()` 中的持久化会话，
所以底层并不是“只能读取当前会话”。当前会话 id 只用于排除本轮尚未完成的事件，
不是数据访问范围。

本需求不重做一套历史存储，也不把能力改名为“跨会话工具”。它修正现有
`recall_past_chats` 从用户意图到证据结果之间的四个断点：

1. 会话展示标题位于 `SessionMetadataStore`，历史 source 只看到消息正文；
2. Agent 需要把会话名、正文搜索内容和时间准确拆成语义化工具参数；
3. source 扫描 raw `events`，排序又主要偏向最近消息；
4. 无命中时，其他记忆 source 仍可能提供不可归属或弱相关候选。

### 1.2 会话管理与历史搜索的依赖方向不同

`SessionManager` 负责列表、重命名、归档、删除和创建 `Conversation`，不适合作为
搜索工具的依赖。若工具反向依赖 manager，会让工具注册、conversation 创建和 session
管理形成环。

因此本需求抽出一个只读会话目录。它只组合 `SessionStore` 与
`SessionMetadataStore`，不承担任何写操作，也不复制 manager 的生命周期业务。

### 1.3 显式工具调用与自动记忆召回是两条不同链路

当前对话页根据 `ToolCallRequest / ToolCallResult` 事件展示
`recall_past_chats` 卡片。用户明确要求翻历史时，这个可见反馈属于既有产品行为。

本需求不会用隐藏的 memory prefetch 替代它。Agent 根据 tool description 与 schema 自主决定
调用后，调用必须经过真实工具执行链，持久化并发出正常的请求/结果事件；前端继续使用现有
卡片，无需新增历史查询 UI。宿主层不先于 Agent 解析原始 query，也不伪造工具调用。

### 1.4 统一搜索仍是唯一召回编排层

会话标题解析和工具意图属于 agent 层；跨源查询、候选和融合契约属于 memory 层；
原始 session event 投影属于 agent 侧 adapter。依赖继续保持：

```text
Agent -> structured tool args -> ConversationHistoryTool -> MemorySearchService
                                                -> SessionHistorySource -> SessionStore
```

`memory` 不反向 import `agent.sessions`。目标会话 id 只作为已解析的内部 scope 进入
`SearchRequest`。

## 2. 设计原则

1. **可见名称是用户契约**：定位使用最终展示标题，不要求用户知道内部 id。
2. **单一语义所有者**：Agent 理解用户 query 并生成参数；宿主和工具不再维护第二套自然语言解析。
3. **显式查询必须显式执行**：模型产生的工具调用走正常工具事件，保留现有卡片和证据链。
4. **目标会话是硬范围**：指定会话后，无法归属到该会话的证据一律不进入结果。
5. **当前有效分支是真相**：召回视图与用户当前看到的 history 使用同一 active projection。
6. **空结果不可填充**：多样性、语义相似和 core memory 都不能把不相关内容包装成命中。
7. **新增字段保持 additive**：旧工具调用、旧 session 文件和无指定会话查询继续可用。

## 3. 总体结构

```mermaid
flowchart TD
    A["收到用户输入"] --> B["Agent 理解意图并选择工具"]
    B -->|"需要历史证据"| C["生成语义化结构参数"]
    B -->|"不需要工具"| D["基于当前上下文回答"]
    C --> E["通过 ToolRuntime 执行 recall_past_chats"]
    E --> F["发出并持久化 ToolCallRequest"]
    F --> G["SessionCatalog 解析可见会话名"]
    G -->|"唯一命中"| H["构造带目标 scope 的 SearchRequest"]
    G -->|"歧义或不存在"| I["返回可澄清状态"]
    H --> J["MemorySearchService 调度受控 source"]
    J --> K["SessionHistorySource 投影 active_events"]
    K --> L["排序、覆盖、轮次聚合和命中窗口"]
    L --> M["发出并持久化 ToolCallResult"]
    I --> M
    M --> N["现有对话页显示工具卡片"]
    M --> O["模型依据结果回答或澄清"]
```

新增和调整的内部边界：

```text
agent/src/agent/
├── sessions/
│   └── catalog.py                  # 只读会话描述与标题解析
├── memory_search/
│   └── session_history_source.py   # active 分支、定向 scope、排序与窗口
└── tools/builtin/conversation_history/
    ├── tool.py                     # schema、目录解析、统一搜索调用
    └── render.py                   # 状态、会话范围和轮次证据渲染
```

以下职责不得合并：会话目录不写数据，Agent 不把整句 query 交给工具，memory 搜索层不解析
产品展示标题。

## 4. 只读会话目录与标题解析

### 4.1 `SessionCatalog`

```python
@dataclass(frozen=True)
class SessionDescriptor:
    session_id: str
    display_title: str
    archived: bool
    updated_at: datetime | None
    channel_kind: str | None = None

class SessionCatalog:
    def list_all(self) -> tuple[SessionDescriptor, ...]: ...
    def resolve(self, title_query: str) -> SessionResolution: ...
```

`list_all()` 直接读取全部持久化 session summary，再用 `SessionMetadataStore.load_all()`
覆盖最终标题和归档状态。它不调用默认排除归档项的 `SessionManager.list()`：归档只是列表
收起，本需求需要继续可回忆。硬删除后 raw session 不存在，即使 metadata 有残留也不返回。

持久化入口必须把同一个 metadata store 注入 catalog；旧的 store-only 构造入口继续可用，
此时只能做正文搜索，不宣称支持展示标题定位。

### 4.2 标题规范化

标题与查询统一执行：

- Unicode NFKC；
- `casefold()`；
- 连续空白折叠为一个空格；
- 去除首尾空白。

不做拼音、编辑距离、同义词或联系人昵称猜测。为支持
“诗欣IM（用户在用，……）”这类长标题，额外生成一个受控的前导名称：只截取首个成对
括号 `()` / `（）` / `[]` / `【】` 之前的非空文本。不会按任意连字符或标点无限拆别名。

### 4.3 匹配顺序与歧义

对显式 `session_title` 依次尝试：

1. 完整规范化标题精确相等；
2. 前导名称精确相等；
3. 完整标题或前导名称以前缀开头；
4. 查询是完整标题或前导名称的连续子串。

在某一级只有一个候选时停止；同一级有多个候选时返回 `ambiguous`，不继续用更新时间
偷偷选一个。候选按最近更新时间排序，最多向模型返回 5 个可见标题。候选正文、内部 id、
文件名和归档字段不进入模型可见结果。

Agent 提取标题时只传用户明确指向的完整标题或前导名称；普通包含匹配只在 Agent 已经明确
提供 `session_title` 参数后使用。

已存在的通道类型可以作为消歧过滤条件，但只消费结构化的已知通道信息；不得从 session id
字符串临时猜一套对外通道协议。

### 4.4 解析结果

```python
SessionResolutionStatus = Literal["matched", "ambiguous", "not_found"]

@dataclass(frozen=True)
class SessionResolution:
    status: SessionResolutionStatus
    matched: SessionDescriptor | None = None
    candidates: tuple[SessionDescriptor, ...] = ()
```

只有 agent 内部对象携带 session id。tool renderer 只输出查询名称、匹配后的展示标题或候选
展示标题。

## 5. 结构化工具参数与约束保真

### 5.1 公共工具参数

`recall_past_chats` 使用以下模型可见参数：

```json
{
  "session_title": "诗欣IM",
  "search_text": "项目排期",
  "since": "今天",
  "until": null,
  "said_by": "you | me",
  "mode": "search | summary",
  "limit": 10
}
```

- `session_title` 是用户可见名称或足够明确的片段，不接受 session id，也不包含动作词。
- `search_text` 只表示要在历史正文中搜索的主题、关键词或原话片段；不得传用户整句请求。
- `mode=search` 用于确认、找原话；`mode=summary` 用于“聊了什么”一类时间段概览。
- 指定 session 或明确时间范围时允许不传 `search_text`；无边界的近期查询由 `limit` 控制。
- `query` 不保留兼容别名。069 尚未作为稳定外部 API 发布，继续接受含义模糊的名称会让错误
  职责固化；调用方与测试在同一修复中一次性迁移到 `search_text`。

### 5.2 运行时隐藏上下文

执行层只注入工具无法由模型可靠提供、且不表达用户语义的运行时事实：当前 session、当前 turn
边界、persona 与冻结的当前时间。`ToolRunContext` 不保存或注入 `original_user_input`。

“今天、昨天、上周”等表达由 Agent 映射到 `since / until`；工具使用冻结时间解释这些已选定的
结构化时间表达。工具不从原始 query 补会话名、主题、说话方或 mode。

### 5.3 跨轮补充条件

`ambiguous_session` 与 `session_not_found` 会把请求标题和候选标题作为模型可读结果返回。用户
下一轮补充“是另一个会话”“叫诗欣IM”“只看昨天”时，Agent 基于正常对话上下文保留仍适用的
主题、时间和说话方并发起新调用；宿主层不维护隐藏 pending plan。

## 6. Agent 控制的显式工具调用

### 6.1 职责边界

Agent 结合用户输入、当前对话、tool description 与 schema 决定是否调用历史工具。它同时负责：

- 从“去看看诗欣IM这个会话今天发了什么”提取 `session_title=诗欣IM`；
- 把“今天”映射为 `since=今天, until=今天`；
- 识别这是范围总结，使用 `mode=summary` 且不传 `search_text`；
- 当前上下文已有答案时不作无意义调用；需要澄清时先自然澄清。

宿主层不运行 `HistoryIntentRouter`，工具不暴露 `plan_explicit_invocation()`，会话目录也不接收
整句用户 query。这样工具选择、参数语义与自然语言理解只有一个所有者。

### 6.2 真实且可见的调用

模型 tool call 通过现有工具执行器完成完整链路：生成 call id，持久化并发出
`ToolCallRequest`，执行 hooks、非语义运行时参数注入和 `ConversationHistoryTool.invoke()`，再
持久化并发出 `ToolCallResult`，最后由模型依据结果回答。新调用的 origin 始终是 `model`；历史
事件类型仍允许读取旧的 `deterministic_history_router` 值，仅用于已落盘数据兼容。

重复调用继续由通用 tool runtime 的预算与无进展机制约束，不增加历史工具专用提示或缓存。

### 6.3 结果后的生成行为

- `ok`：只依据返回证据回答；summary 由当前回复模型完成，不再发起额外 LLM 调用。
- `ambiguous_session`：列出返回的少量标题并询问目标。
- `session_not_found`：说明没有定位到该名称，邀请补充名称或特征。
- `no_results`：说明目标存在，但给定范围内没翻到。
- `source_error`：说明这次读取失败，可以稍后重试。

工具 description 与历史查询 system contract 同步声明：工具覆盖本机可访问的多个会话，
一次无命中不代表只能查当前会话。面向用户回答仍使用自然语言，不说 schema、索引或 id。

## 7. 搜索请求与 source 范围

### 7.1 additive 内部 scope

`SearchRequest` 增加：

```python
target_session_ids: tuple[str, ...] = ()
retrieval_goal: Literal["search", "summary"] = "search"
```

这是 agent 已解析后的内部约束，不向模型暴露。当前 `session_id` 继续表示“当前正在运行的
会话”，只服务当前轮隔离；不得复用为目标会话，避免两个语义混淆。

### 7.2 指定会话

存在 `target_session_ids` 时，tool 设置 `source_scope={"session_history"}`。本期指定会话
查询只采用 raw history adapter 产生的 utterance evidence：现有结构化 memory 没有稳定的
目标 session 归属，不能用于回答“这个会话里聊过什么”。

未来结构化 memory 若提供经过验证的 session provenance，可在后续需求中接入；本期不通过
字符串 provenance 猜归属。

### 7.3 未指定会话

普通跨会话显式查询仍可经过统一多 source 检索，但融合规则区分 automatic 与 explicit：

- automatic 保持既有 core memory 注入策略；
- explicit 只保留真正通过 keyword / dense / time 条件命中的候选；
- 没有相关性证据的 core memory 不再为了占满 top-K 无条件进入显式结果；
- 有 utterance 精确证据时优先于只有语义相似的结构化候选。

这项规则落在 `MemorySearchService`，不在 tool renderer 中事后丢弃，以便 trace 能说明候选为何
被排除。

## 8. 有效历史投影与当前轮隔离

### 8.1 使用 `active_events`

`SessionHistorySource` 从 `session.active_events` 读取用户和助手正文，不再遍历 raw
`session.events`。工具调用、persona、system、partial 和其他非对话事件继续排除。

编辑重发产生的 inactive 旧分支因此自然不可召回；读取旧 session 文件不需要迁移。

### 8.2 当前轮边界

现有 `current_turn_start_index` 是 raw events 的边界。source 先取得
`session.events[boundary:]` 的稳定事件身份集合，再投影 active events，并排除集合中的成员。
这样即使 active projection 改变顺序，也不会把本轮用户问句、过渡 assistant 文本或工具结果
召回；边界前的 active 历史仍可命中。

若旧事件缺少稳定 id，使用 session 内 raw 索引到 active projection 的现有关联信息实现，
不得直接把 raw index 当作 active index。

## 9. 候选相关性、覆盖与轮次证据

### 9.1 相关性分层

对有 `search_text` 的 utterance 使用确定性信号形成相关性层级：

1. 完整短语或连续原话命中；
2. 全部规范化词项命中；
3. 部分词项命中，按覆盖率与稀有度排序；
4. 仅语义相似候选。

同层再按时间接近度、现有 importance 和稳定事件顺序排序。时间更新不能把低层候选提升到
精确命中之前。没有任何可接受相关信号的候选不进入最终池。

### 9.2 跨会话覆盖

未指定会话时，在同一相关性层内做软多样化：第一轮每个有候选的会话至多取一个，再按全局
排序补满；进入下一相关性层之前先耗尽更强层。这样其他同样相关会话不会被当前会话数量淹没，
又不会为了“多样”强行加入较弱或无关内容。

指定会话时不应用跨会话配额。

### 9.3 对话轮次聚合

source 为每个命中构造最小相邻问答窗口，并给窗口稳定 key。重叠窗口按同一 session、相邻
event identity 合并；同一轮的用户问句和助手回答只算一组 evidence，不作为两个独立事实。

`mode=search` 优先返回命中强的窗口；`mode=summary` 在满足过滤条件的有效轮次中按时间分桶，
优先覆盖不同时间段和不同主题，再按时间正序交给模型总结。`limit` 表示最大证据轮次数，
不表示原始事件数。

summary 不调用第二个摘要模型；当前对话回复模型基于有界轮次证据生成自然总结。超出预算时
结果明确带 `truncated=true`，回答不能声称覆盖全部内容。

### 9.4 命中感知片段

长消息不再固定截取开头 200 字。对关键词查询，以首个最高质量命中为中心保留前后窗口，
并用省略标记表示被裁剪部分；无 `search_text` 的 summary 采用开头与必要结尾的有界投影。

renderer 保留 utterance 的说话方和相对时间，增加可见会话标题和查询范围，但不输出
session id、event type、raw index 或文件路径。

## 10. 工具结果协议

模型可见结果保持不可信数据包，并使用稳定状态：

```json
{
  "source_type": "past_conversation",
  "trust": "untrusted_data",
  "status": "ok | ambiguous_session | session_not_found | no_results | source_error",
  "scope": {
    "session_title": "诗欣IM（……）",
    "time": "今天",
    "mode": "summary"
  },
  "candidate_session_titles": [],
  "result_count": 3,
  "truncated": false,
  "results": []
}
```

- `ambiguous_session / session_not_found / no_results` 是可处理的业务结果，`is_error=False`；
- 参数非法和底层 source 全失败为 `is_error=True`；
- source 部分失败但仍有可靠证据时可返回 `ok`，meta 记录降级；
- 候选标题只在 ambiguity 时出现，最多 5 个；
- `results` 只在 `ok` 时包含证据。

`ToolResult.meta` 增加内部诊断：resolution status、source runs、各 session 候选/入选计数、
过滤原因、截断和 model origin。meta 不复制消息正文，也不进入面向模型的文本。

## 11. 装配与入口兼容

### 11.1 bridge / desktop / IM

`agent_bridge.assembly` 已同时持有 persistent `SessionStore` 和
`SessionMetadataStore`。装配时创建一份 `SessionCatalog`，注入默认 registry 中的
`ConversationHistoryTool`；同一 registry 被 desktop 与 IM conversation 使用，语义一致。

catalog 独立于 `SessionManager`，因此不改变现有创建顺序，也不形成依赖环。

### 11.2 CLI

持久化 CLI 从同一 user data sessions 目录创建 metadata store 与 catalog，并传给
`make_default_registry()`。只传 `SessionStore` 的测试和旧调用方继续使用兼容构造，但只有
注入 catalog 后才启用按展示标题定位。

### 11.3 无状态入口

无状态 API、fixture 或 `NullSessionStore` 不注入本机 catalog，不获得跨请求数据。
没有历史数据时返回正常 `no_results / session_not_found`，不能回落到进程上其他持久化 store。

### 11.4 工厂兼容

`make_default_registry()` 只增加可选 `session_catalog` 参数；现有 `session_store` 与
`memory_search` 参数继续支持。工具名、旧参数和注册条件不变。

## 12. Prompt 与能力陈述

工具 description 更新为：

- 能读取本机当前可访问的多个持久化会话；
- Agent 必须先理解用户目标再填参数，不把整句请求放进任一参数；
- `session_title` 只用于产品展示名称，`search_text` 只用于历史正文；
- 有会话名、时间和说话方时分别传递；范围总结不传 `search_text`；
- 歧义时使用候选澄清，无结果时不能解释成“不支持跨会话”；
- 不向用户复述工具名、状态码或参数。

通用 tool usage contract 只补本工具的事实边界，不建设全局能力目录。系统仍要求只有真实
工具结果后才能说“翻了历史”。

## 13. 可观测性

一次查询沿用 tool call id 串起：

```text
model_tool_call -> session_resolution -> source_run
                -> candidate_filter -> fusion/diversity -> render -> generation
```

诊断字段包括：

- 模型可见参数与 tool call origin；
- resolution status 与候选数量；
- 每个 source 的状态、候选数和失败类型；
- 各 session 候选数、入选数及 quota/filter 原因；
- active/inactive/current-turn 排除数量；
- final count、truncated 和 tool result status。

日志与 trace 不记录完整用户正文、完整会话标题集合、证据正文或 session 文件路径。调试界面
需要正文时继续走既有受控 evidence inspector，不在 INFO 日志复制隐私内容。

## 14. 测试策略

### 14.1 会话目录

- 初始标题、重命名标题和长标题前导名称；
- NFKC、大小写和空白归一化；
- 精确、前缀、包含匹配优先级；
- 同级多候选歧义、候选上限和稳定排序；
- 归档可见、硬删除不可见；
- metadata 孤儿记录不产生会话。

### 14.2 Agent / 工具职责契约

- schema 暴露 `search_text` 而不是 `query`，description 明确禁止整句透传；
- “去看看诗欣im这个会话里今天有没发什么”由模型调用映射为 session + 今天 + summary，
  不传 `search_text`；
- 调用产生真实 request/result 事件且 origin 为 `model`，现有卡片事件完整；
- 工具执行入参不包含 `original_user_input`，只注入时间和当前 turn 等非语义事实；
- capability 问句、技术 session 讨论和当前上下文问题是否调用由 agent eval 观察。

### 14.3 source 与融合

- 只读取 active_events，编辑重发旧分支不可命中；
- 当前轮 raw boundary 正确映射到 active projection；
- 指定目标时只返回该 session，并跳过不可归属 structured memory；
- 归档 session 可命中，删除后不可命中；
- 精确短语、全词、部分词和语义候选保持层级；
- 当前会话候选很多时，同等相关的其他会话仍进入结果；
- 无关会话不因 diversity 被强行加入；
- 长消息中后部命中时片段包含命中；
- 相邻重叠窗口去重，summary 按轮次而非事件计 limit；
- explicit 无证据时 core memory 不填充为成功。

### 14.4 工具与装配

- 五种业务状态与 is_error 语义；
- 模型可见结果无 session id、event type、路径和内部 trace；
- bridge、IM、desktop 和持久化 CLI 使用同一 catalog；
- store-only 旧构造仍能做正文检索；
- stateless/Null store 不读取本机持久化数据；
- 020、039、048 现有工具、证据与自动召回测试继续通过。

### 14.5 Agent eval

扩展 `c1-cross-session-recall.yaml`，覆盖：

1. 按展示标题和今天查询并正确总结；
2. 首次歧义后用户补充名称，继续原查询；
3. 目标存在但无结果时不编造；
4. 用户追问“不是能跨会话吗”时不说“只能查当前会话”；
5. 当前上下文已有答案时不无意义翻历史。

评测同时断言所需工具事件存在、参数约束完整，并禁止“只能查当前会话 / 无法查看其他会话”
等错误能力陈述。默认使用 fake LLM、固定 clock 和本地 fixture，不调用真实外部 LLM。

## 15. 实施顺序

1. 落 `SessionCatalog`、标题解析和生命周期单测。
2. 扩展 `SearchRequest` 与 `SessionHistorySource`，先修 active 分支、目标 scope 和当前轮隔离。
3. 修 explicit 融合、跨会话覆盖、轮次聚合和命中感知片段。
4. 扩展工具 schema、状态 renderer 与装配入口。
5. 强化 tool description，让 Agent 生成语义化参数并走真实可见工具事件。
6. 更新 tool contract、trace、agent eval 和全量回归。

先用确定性测试固定工具执行语义，再用 agent eval 验证模型是否选择正确工具和参数；不以宿主
词法路由替代模型行为评测。

## 16. 接口与迁移

- `recall_past_chats` 名称不变；正文参数由 `query` 更名为 `search_text`，不提供旧名兼容；
  `session_title / since / until / said_by / mode / limit` 保持结构化可选参数。
- `SearchRequest` 只做 additive 字段扩展，旧 source 忽略或由 service 统一处理默认空 scope。
- session JSONL 不做数据迁移；active projection 直接解释现有 rewrite 事件。
- metadata schema 不变；catalog 只读复用 `display_title / archived_at`。
- tool request/result 事件继续展示现有卡片；新事件 origin 为 `model`，类型层保留旧 origin 字面量
  以读取已落盘事件。
- 不新增第三方依赖，不预建全文索引；先基于现有本机规模验证扫描性能。

## 17. 性能边界

标题目录构建只读轻量 session summary 与 metadata，不读取每个会话正文。一次工具调用只构建
一次 catalog snapshot。

定向查询解析后只打开目标 session；未指定会话时继续扫描现有 session 集合，但在 source 阶段
尽早应用时间、speaker 和 active-event 过滤。测试增加多 session 基线，若常见本机规模下查询
出现可感知回归，再在本需求内做最小缓存；不提前引入持久化索引或向量库。

## 18. 已知限制与后续边界

- 本期标题匹配不支持拼音、错别字、语义别名或联系人昵称；歧义时必须澄清。
- 标题变化后，旧标题不是永久别名；只查询当前最终展示标题。
- structured memory 尚无可靠 session provenance，指定会话查询暂时只用原始对话证据。
- summary 是基于有界证据的当前回复，不保证在超大时间跨度内逐条覆盖；`truncated` 必须如实
  传递给生成层。
- 不改变会话列表、归档、重命名或工具卡片的视觉设计。
- 不提供跨设备、云端同步、硬删除恢复或通用产品能力目录。

## 19. 变更记录

| 日期 | 变更内容 | 影响范围 |
| --- | --- | --- |
| 2026-07-28 | 推翻宿主层确定性路由与原始 query 暗注入；改为 Agent 独占意图理解，工具只执行结构参数；`query` 更名为 `search_text` | §1～§6、§12～§16 |
| 2026-07-28 | 初始技术方案；确定可见工具卡片、标题解析、约束保真、有效历史与可靠空结果 | 全文 |
