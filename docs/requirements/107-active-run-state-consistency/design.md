# 107 · 活动运行状态与界面投影一致性 - 技术方案

## 状态

CONFIRMED

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

Runtime 已通过 `RunSnapshot` 提供按 `run_id` 版本递增、唯一 terminal 的运行事实，Bridge 的 REST 与 push 也都传递同一结构。问题主要发生在前端：`runSnapshots`、`activeRunIdBySession`、全局 `streaming`、`SessionConversationView.streaming` 与 `ChatMessage.status` 都能分别改写“是否仍在运行”，并被不同组件直接消费。

当前主要分叉路径如下：

- `openSession()` 会把历史 assistant 投影为 `complete`，同时根据 active map 单独设置会话 `streaming`。
- `applyRunSnapshot()` 会按 session 直接写 `streaming = !terminal`，旧 run terminal 可以清掉新 run 的 busy。
- `applyRunEvents()` 在缺少 active map 时会重新写入 active run，terminal 后迟到事件可以复活旧 run。
- stream transport 会在局部 `done` 后先结束 `ChatMessage.status`，但 Runtime 可能仍在继续模型或工具步骤。
- action bar 只看 `ChatMessage.status`，无法识别该消息仍属于 active run。
- 初始 `calibrateRuns()` 没有 readiness；列表响应与新 push 交错时，缺失项清理可能覆盖请求期间的新事实。
- command / approval store 对 REST 响应只做 merge，长期保留响应中已缺失的 pending / running 项；ToolCard 又允许 stale live process 覆盖持久化 terminal 结果。
- 后台 terminal 直接按 outcome 产生未读，无法区分真正的新对话内容与 hidden、memory-only 或外部出口。

首轮实现已经关闭上述 run ownership 与终态分叉，但主要活动选择仍分散在渲染组件中：`MessageList` 根据 active message 自行挂载 `ModelResponseStatus`，再用本地布尔值抑制授权和命令；`ApprovalDock` 独立构造授权、命令与无消息 run 的 generic；运行中的 `CompactionBlock`、普通工具活动和 `SocialAuthCard` 又各自呈现实时状态。`ModelResponseStatus` 的 10 秒慢响应计时只改变自身文案，不知道同一消息已经存在更精确的压缩活动，因此会在压缩持续时重新出现。这说明首轮只统一了“哪个消息承载 run 活动”，没有统一“哪个活动拥有本次前端投影”。

本方案不改变 Runtime 调度、Bridge run schema 或持久化 session 格式；使用现有 `RunSnapshot.version`、session revision、事件 `meta.run_id` 和 presentation 过滤完成收敛。

## 方案设计

### 涉及文件

| 文件路径 | 改动类型 | 说明 |
| --- | --- | --- |
| `frontend/src/types/chat.ts` | 修改 | 为 `ChatMessage` 增加稳定 `runId` 与 terminal `runOutcome` |
| `frontend/src/stores/runPresentation.ts` | 新增 | 纯函数选择 session 的 canonical active run，并派生 busy、恢复、停止能力 |
| `frontend/src/stores/snapshotFence.ts` | 新增 | 小型请求围栏 helper，保护全量 REST 对账不覆盖请求期间的新 push |
| `frontend/src/stores/conversation.ts` | 修改 | 移除会话级重复 `streaming` 与 active map，统一 run 合并、terminal 收口和后台结果 |
| `frontend/src/stores/sessionProjection.ts` | 修改 | 从持久事件 `meta.run_id` 恢复消息归属，并按 run 边界拆分 assistant 聚合 |
| `frontend/src/stores/sessionSync.ts` | 修改 | 使用派生 busy 与 calibration phase 调度详情刷新和重连对账 |
| `frontend/src/stores/commandRuntime.ts` | 修改 | REST/push 版本合并、缺失项清理与 terminal 事实优先 |
| `frontend/src/stores/filesystemApprovals.ts` | 修改 | 为 pending 列表补请求围栏，避免旧响应清理新 push |
| `frontend/src/pages/chat/components/MessageList.tsx` | 修改 | 按 active run identity 隐藏完整 action bar，并承载消息级通用活动 |
| `frontend/src/pages/chat/components/Composer.tsx` | 修改 | 使用派生 busy / canStop / recovering 控制输入和停止入口 |
| `frontend/src/pages/chat/components/ConversationModelSelector.tsx` | 修改 | 使用派生 busy 与恢复状态保护配置切换 |
| `frontend/src/pages/chat/components/HistorySidebar.tsx` | 修改 | 直接从 run snapshots 派生 queued/running，精确显示后台完成或失败 |
| `frontend/src/pages/chat/components/ApprovalDock.tsx` | 修改 | 按 canonical active run 过滤授权与命令，并参与唯一主要活动选择 |
| `frontend/src/pages/chat/components/ToolCard.tsx` | 修改 | terminal 持久结果优先于 stale live process，操作能力绑定 active run |
| 对应 `*.test.ts(x)` | 修改/新增 | 覆盖状态矩阵、乱序、恢复、消息/action bar、未读和 item 对账 |
| `frontend/src/pages/chat/components/activity/primaryActivity.ts` | 新增 | 纯函数把规范化候选解析为唯一 `PrimaryActivityPresentation`，定义活动 kind、承载 surface 与稳定实体身份 |
| `frontend/src/pages/chat/components/activity/usePrimaryActivity.ts` | 新增 | 聊天 surface 唯一的 store adapter；读取 run、消息、授权、命令和登录事实并调用纯 resolver |
| `frontend/src/pages/chat/components/activity/approvalCandidates.ts` | 新增 | 从 `ApprovalDock` 提取授权与运行中命令的纯候选构造，避免 UI 组件反向参与选择 |
| `frontend/src/stores/socialAuthRequests.ts` | 新增 | 统一社交登录请求快照、当前会话可见请求的有界轮询和动作后的权威快照更新 |
| `frontend/src/pages/chat/components/ChatContentSurface.tsx` | 修改 | 只计算一次主要活动，并把同一个判别联合结果传给消息区与 Dock |
| `frontend/src/pages/chat/components/ModelResponseStatus.tsx` | 修改 | 只格式化已经选中的 model/generic 活动；计时器只能更新该活动文案，不能决定是否挂载 |
| `frontend/src/pages/chat/components/ToolActivity.tsx` | 修改 | 接收选中活动身份；运行中普通工具可承载主要活动，高优先级表面存在时只保留审计呈现 |
| `frontend/src/pages/chat/components/SocialAuthCard.tsx` | 修改 | 使用共享登录快照并按选中请求区分主要活动与排队/历史呈现，不再自行维护第二份轮询状态 |
| `frontend/src/pages/chat/components/activity/*.test.ts(x)`、`frontend/src/stores/socialAuthRequests.test.ts` | 新增 | 覆盖组合矩阵、跨表面联合渲染、延时兜底和登录轮询收口 |

### 数据流与事实所有权

```mermaid
flowchart TD
    R[Runtime RunSnapshot<br/>run_id + version + terminal] --> M[conversation.runSnapshots]
    P[run_changed push] --> M
    L[GET /runs 全量快照] --> F[request fence + version merge]
    F --> M
    M --> S[SessionRunPresentation selector]
    C[calibration phase] --> S
    S --> H[侧栏 queued / running]
    S --> I[输入与停止能力]
    S --> A[action bar active-run 判断]
    S --> D[PrimaryActivityPresentation resolver]

    E[实时 ConversationEvent] --> Q[ChatMessage projection]
    J[持久 SessionEvent meta.run_id] --> Q
    Q --> A
    Q --> U[conversation-visible output 判定]
    M --> U
    U --> N[后台新回复 / 失败提示]

    T[approval / command snapshot] --> D
    Q --> D
    G[social auth request snapshots] --> D
    D --> O[ChatContentSurface<br/>single owner]
    O --> ML[MessageList message surface]
    O --> DK[ApprovalDock dock surface]
    T --> K[工具与 Dock 终态]
```

事实边界：

- `RunSnapshot` 决定整轮 queued、running、terminal 和控制范围。
- `ChatMessage.status` 只描述消息 item 是否仍在接收内容，不再决定 session busy。
- approval、command process、tool block 与 compaction 各自描述局部 item；它们只能提高主要活动的精确度，不能结束整轮。
- 社交登录 request snapshot 描述独立于单个 run 的用户交互事实；`pending`、`login_in_progress`、`authenticated` 和 `continuing` 参与当前 session 的主要活动选择，terminal request 只保留历史结果。
- session detail 决定最终显示内容；terminal 先结束 busy，detail 刷新只负责补齐持久内容。

### Canonical run 选择与派生状态

新增 `runPresentation.ts`，输入保持为结构化只读字段，不直接依赖 React：

```ts
type RunCalibrationPhase = "calibrating" | "ready" | "degraded";

interface SessionRunPresentation {
  phase: "recovering" | "queued" | "running" | "stopping" | "idle";
  run: RunSnapshot | null;
  busy: boolean;
  canStop: boolean;
}
```

选择规则：

1. 过滤目标 session 的非 terminal snapshots。
2. `running` 优先于 `queued`；同状态按 `created_at` 较早者优先，最后用 `run_id` 保证稳定顺序。
3. 有 run 时以 run 状态为准；没有 run 且 calibration 尚未成功时为 `recovering`；只有 ready 且无非终态 run 时为 idle。
4. `canStop` 仅在 run 的 `control_scope === "desktop_text"` 且 run 非 terminal 时成立。
5. stop request 只把 phase 改为 `stopping`，不改变 busy；terminal 才结束。

`ConversationState.streaming`、`SessionConversationView.streaming` 与 `activeRunIdBySession` 全部删除。store action、React 组件和 session sync 都调用同一纯 selector；消息自身的 `MessageStatus.streaming` 保留。

### Run snapshot 合并与请求围栏

`applyRunSnapshot()` 只做按 run identity 的版本合并：

- `incoming.version <= current.version` 时忽略。
- 非 terminal snapshot 只更新自身，不覆盖同 session 其它 run。
- terminal snapshot 只结束同一 `run_id` 的消息、stop request 和 item 操作能力；active run 由 selector 重新计算，因此旧 run terminal 无法清除新 run。
- 已知 terminal run 的 `run_event` 一律拒绝；未知 run event 可以补消息，但不能自行创建 run ownership。若同 session 已有另一 canonical run，则拒绝未知旧 run 的迟到事件。

全量 REST 对账使用 `snapshotFence.ts`：

1. 请求发起时记录 collection mutation epoch。
2. 每次 push / 本地 authoritative mutation 为对应 key 记录新的 touched epoch。
3. 响应内对象仍按对象 version 合并。
4. 响应中缺失的 pending / running key，只有 `touchedAt[key] <= requestStartEpoch` 时才可清理；请求期间收到新 push 的 key 保留。
5. reconnect 重新运行全量对账；对账失败进入 `degraded/recovering`，不伪装 idle。

Run、filesystem approval、command approval/process 分别持有自己的 fence 状态；先使用同一个小型纯 helper，不建立全局注册中心。

### 消息 run identity 与 terminal 收口

`ChatMessage` 增加：

```ts
runId?: string;
runOutcome?: RunOutcome;
```

- 实时 user turn、编辑重发、工具结果续跑和 conversation presentation 主动 run 在创建 assistant placeholder 时写入 run id。
- 历史投影从 `SessionEvent.meta.run_id` 恢复；只有相邻事件缺少 run id 时才沿用旧兼容聚合。两个明确且不同的 run id 必须拆成两条 assistant message。
- action bar 的隐藏条件为 `message.status === "streaming" || message.runId === activeRun.run_id || editing`。因此局部 message done 不会提前暴露 action bar，更早 run 的消息不受影响。
- terminal snapshot 立即把该 run 的 streaming message 投影为终态：completed → complete、cancelled → interrupted、failed → error；skipped / expired / rejected 使用 interrupted 展示但保留精确 `runOutcome` 文案，不归为失败。
- terminal 后即使 detail refresh 失败，消息也不会永久 streaming 或永久隐藏 action bar；后续 detail 成功时再用持久事件替换内容。

### 唯一主要活动

新增聊天展示层的判别联合，resolver 每次只返回其中一个成员：

```ts
type PrimaryActivityPresentation =
  | { surface: "dock"; kind: "approval"; request: PendingApproval; queuedCount: number }
  | { surface: "dock"; kind: "command"; command: RunningCommand }
  | { surface: "message"; kind: "socialAuth"; messageId: string; requestId: string }
  | { surface: "message"; kind: "compaction"; messageId: string; activityId: string }
  | { surface: "message"; kind: "tool"; messageId: string; toolCallId: string }
  | { surface: "message"; kind: "model"; messageId: string; status: ModelResponseProgress }
  | { surface: "message"; kind: "run"; messageId: string; phase: RunPhase }
  | { surface: "dock"; kind: "run"; phase: RunPhase }
  | { surface: "none"; kind: "none" };
```

`runPresentation.ts` 继续只负责 canonical run、busy 与控制能力；它不感知组件和 activity surface。`activity/primaryActivity.ts` 接收已经规范化的候选并按以下顺序选择：

1. 当前 session 仍需用户处理的社交登录或当前 active run 的可见 pending approval；同类多个请求按创建顺序和稳定 id 选择一个，其余只保留排队/历史信息。
2. 当前 active run 的 running command。
3. active message 上仍 running 的 compaction。
4. active message 上仍 running 的普通工具活动；命令工具由上一步的 Dock 接管，不在此重复成为主要活动。
5. active message 上明确的模型 `runStatus`，包括 waiting、reasoning、processing 和 retrying。
6. canonical run 的 queued、stopping 或 running generic；有 active message 时跟随消息尾部，没有 conversation message 时由 Dock 承载。
7. calibration 的 recovering；没有可关联消息时由 Dock 承载。
8. 无候选时返回 `none`。

表现和所有权规则：

- `ChatContentSurface` 调用 `usePrimaryActivity()` 一次，并把同一个不可变结果传给 `MessageList` 与 `ApprovalDock`。两个子树只比较 `surface`、`kind` 和稳定实体 id，不得根据 store 状态自行选择 fallback。
- `MessageList` 移除 `suppressRunActivity`、本地授权/命令订阅与“只要 active message 就挂载 generic”的分支。只有选中的 `model` 或 `run` 成员可以挂载 `ModelResponseStatus`。
- 运行中 compaction、普通工具和社交登录卡继续留在原消息顺序中；只有与判别联合 id 匹配的实例带实时强调与 `data-primary-activity`。更高优先级活动存在时，其余实例保留可发现的审计或排队呈现，但不再作为第二个实时状态。
- `ApprovalDock` 只渲染 resolver 已经选择的 approval、command 或 dock-level run，不再计算 `hasActiveRunMessage`、generic label 或优先级。
- `ModelResponseStatus` 的 slow/stale timer 只允许改变已经选中活动的 label。高优先级活动出现后组件会被卸载，计时器清理；高优先级活动持续期间即使越过 10 秒也不能重新挂载 generic。
- 所有主要活动根节点带统一的 `data-primary-activity` 与 kind，作为 DOM 诊断和联合渲染不变量；每个 chat surface 的计数只能是 0 或 1。
- completed compaction、terminal tool、terminal social auth 和更早 run 的活动继续作为历史显示，不携带主要活动标记，不影响 action bar 或 generic 回退。

社交登录状态当前由 `SocialAuthCard` 局部轮询，无法被兄弟组件安全消费。本轮把请求快照提升到 `socialAuthRequests` store：

- `ChatContentSurface` 从当前消息中的 `request_social_auth` 结果提取 request id，并同步当前可见请求集合；store 对每个 request 只保留一个轮询链。
- active 状态继续按既有 1 秒节奏刷新，terminal 或请求不再属于当前可见 session 时停止；切回会话时重新读取权威快照。
- approve、decline 和 cancel 动作由 store 调用现有 API，并把响应原子写回同一 entry；`SocialAuthCard` 只消费 snapshot 和 action，不保留平行本地状态。
- 不新增 Bridge endpoint、push 事件或持久化字段；轮询失败只影响登录卡自身的可恢复错误，不伪造 run terminal，也不让 generic 越过仍已知的 active 登录事实。

### 后台结果与用户可见内容

后台 terminal 不再直接把所有非 completed 归为 failed：

- `failed` 可以产生明确失败提示。
- `cancelled`、`skipped`、`expired`、`rejected` 不产生“有新回复”或“处理失败”标记。
- `completed` 只有在当前 session projection 中存在 `runId` 匹配、且含可渲染 conversation 内容的 assistant message 时才产生“有新回复”。
- 若后台 session 尚无缓存 view，terminal 后读取一次 active detail，投影后再判断；请求返回前用户已打开该 session 时不再写未读。
- `markSessionViewed()` 继续在进入会话时清理标记；当前可见 session 从不新增后台未读。

### Tool、approval 与 command 收敛

- pending / process REST 列表采用请求围栏，并清理快照中已缺失且请求期间未更新的 active 项。
- Dock 和 ToolCard 的操作入口同时要求 item 属于 canonical active run。
- run terminal 后立即不再展示可操作 Dock；随后触发 authority refresh，把 broker 已清理的项从 store 收口。
- ToolCard 仅在 tool block 仍 running 且 process 属于 canonical active run 时使用 live process；持久化 tool result 已 terminal 时优先使用 result，防止 stale running 覆盖。
- 独立长期后台 terminal 不在本期引入；现有 run-bound command 继续由 run lifecycle 约束。

### 初始化、切换与详情刷新

- store 初始 calibration phase 为 `calibrating`；首次列表成功后才进入 ready。
- current session 在 recovering 时禁用发送、编辑重发和模型切换，但允许查看已有历史。
- `openSession()` 使用 cached view 展示内容，busy 由 selector 独立派生；不再把 cached message status 写回 session busy。
- current session busy 时延迟 session detail replace；selector 从 busy 变 idle 后 flush 最新 revision。
- terminal snapshot 总是触发当前 session detail refresh；失败只保留已确定的 terminal projection，不恢复 busy。
- reconnect 清空未应用的 run event scheduler，进入 calibration，再对 runs、approval、command 和当前 detail 做权威刷新。

## 测试与验收设计

### 确定性自动化

- `runPresentation.test.ts`：queued/running 优先级、recovering、control scope、stopping、多 run 同 session。
- `snapshotFence.test.ts`：旧 pull + 新 push、缺失项清理、低版本响应、重连重复对账。
- `conversation.test.ts`：旧 terminal 不清新 run、terminal 后迟到 event 不复活、terminal 详情失败、后台可见/隐藏结果、外部 run、取消等待。
- `sessionProjection.test.ts`：历史 run id、同 run 多段聚合、不同 run 拆分、hidden/ambient/memory-only 过滤。
- `MessageList` / presentation 测试：active message 整个 action bar 隐藏、历史消息保留、无正文 run 的 session 级状态、Dock 优先级。
- `sessionSync.test.ts`：初次 readiness、重连、busy→idle detail flush、旧 response 不覆盖新 push。
- command / filesystem / ToolCard tests：缺失 pending/process 清理、terminal result precedence、run terminal 后无操作入口。
- `activity/primaryActivity.test.ts`：表驱动覆盖 approval、social auth、command、compaction、普通工具、model、generic 与 recovering 的单项和两两组合；每组只返回一个判别联合成员。
- `activity/primaryActivityProjection.test.ts`：在同一 React root 中同时挂载消息活动出口和 Dock，断言 `data-primary-activity` 数量始终为 0 或 1，并覆盖 activity 在两个 surface 间切换。项目 Vitest 只收集 `*.test.ts`，因此 JSX-free 的 React 测试继续使用 `.ts`。
- `ModelResponseStatus` fake timer：compaction 或其它高优先级活动持续超过 10 秒时 generic 不出现；活动结束后只出现一个正确 fallback，旧 timer 不泄漏。
- `socialAuthRequests.test.ts`：同 request 单轮询、切换 session 清理、active 续轮询、terminal 停止、动作响应覆盖与失败恢复。

### 真实 Tauri 验收

按项目桌面验收流程使用当前 worktree 的真实 Tauri runtime，核对 runtime lease 与源码新鲜度。使用可控运行态覆盖：

- active assistant 已有正文但 run 继续时，action bar 完整隐藏且保留唯一继续中反馈。
- A 会话运行中切到 B 再切回 A，侧栏、消息、composer 与 Dock 一致。
- queued、正在取消、等待授权、运行中命令和 terminal 的主要活动切换。
- 复现“正在整理较长对话”持续超过 10 秒，确认慢响应 generic 不再同时出现；压缩结束后只回退到一个正确状态。
- 运行中普通工具、社交登录和 continuation run 的组合只保留一个实时主要活动，历史行仍可查看。
- 不可由桌面控制的外部 run 无停止入口。
- terminal 后 action bar 恢复，失败/取消文案正确，无残留授权或运行中命令。

若验收使用 mock / dev-only 状态注入，只把它作为真实 WebView 视觉与 DOM 证据；跨 Bridge/Runtime 的行为结论必须由对应集成测试或真实运行证据支持。

## 影响分析

### 上下游影响

- Runtime 与 Bridge API 无 schema 变化，现有 voice、IM、AG-UI 和主动 run 入口继续产出原事实。
- conversation store 的 public state 会删除 `streaming` 与 `activeRunIdBySession`；所有前端消费者和测试 fixture 必须同步迁移到 selector。
- `ChatMessage` 新字段为可选，旧持久事件与 Persona 编辑器消息不需要迁移。
- command / approval store 的 terminal history仍可保留用于详情，但 active UI 只认 canonical run ownership。
- 新增的 social auth store 只保存 Bridge request snapshot 与短生命周期轮询控制，不写入持久设置、session 历史或模型上下文。
- Persona 编辑器复用的 `MessageTimeline` 不接入正式会话 activity owner；未传 `PrimaryActivityPresentation` 时保持现有静态/本地消息行为。
- session list `updated_at` 不承担 unread 判定，避免改变现有列表排序契约。

### 风险点

- `streaming` 消费者较多，若漏迁移会出现编译错误或局部门禁缺失；通过全仓 `rg`、typecheck 和组件测试机械覆盖。
- calibration 失败若没有恢复入口可能长期 recovering；重连、窗口恢复与显式 refresh 都要能够重新触发校准。
- history 中较老事件可能没有 `meta.run_id`；投影必须保留旧聚合兼容，不能为修新数据破坏旧会话。
- background detail fetch 可能与用户打开 session 竞态；写未读前再次检查 current session，并按 open token/revision 拒绝旧结果。
- request fence 依赖 REST 列表是单次一致读取；如果后续 broker 引入跨进程多写者，应升级为服务端 collection revision，而不是继续叠本地例外。
- 主要活动去重会触达 MessageList 与 Dock；需要真实桌面验收确认状态没有消失、重复或遮挡 composer。
- active message 和 Dock 位于不同组件子树，若任一子树重新读取 store 并自行降级，会再次形成双 owner；通过只从 `ChatContentSurface` 传入判别联合、移除旧 suppression 分支和联合 DOM 测试防止回归。
- 社交登录卡进入虚拟列表后可能反复挂载；轮询生命周期必须由 store 的可见请求集合管理，不能重新退回组件 mount 次数，否则会产生重复请求或失去主要活动事实。
- 运行中工具在高优先级 Dock 存在时仍需保留审计价值；被动呈现不能继续携带 pulse、`role=status` 或 `data-primary-activity`，否则视觉或无障碍层仍会把它解释成第二个当前状态。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
| --- | --- | --- |
| 2026-08-28 | 首次确认：canonical run selector、消息 run identity、请求围栏、terminal 收口、可见未读与分层 Tauri 验收 | 是 |
| 2026-09-05 | 补充确认：聊天 surface 只在 `ChatContentSurface` 解析一次判别联合；MessageList、Dock、压缩、工具与社交登录均消费同一结果；新增组合、联合渲染、计时与登录轮询回归 | 是 |
