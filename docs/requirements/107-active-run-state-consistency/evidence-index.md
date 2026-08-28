# 107 · 活动运行状态与界面投影一致性 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：issue 058 的历史运行观察、当前代码与测试基线、相邻状态场景扫描、同类 Agent 产品公开实践，以及需求范围所依赖的用户决定。
- 本索引不记录：用户会话正文、完整运行日志、私有截图、隐藏推理、未获授权的真实 LLM 实验或技术方案实现决定。
- 最近对账：2026-08-28，基于 `agent-friend` `d766d87ff2a0915cd76b2f353181e8ee96e8db2d` 与当前需求确认结果。

## 当前综合判断

agent-friend 已有可查询、版本化且唯一 terminal 的 Runtime run 事实，但桌面把同一事实再次拆成会话 busy、消息 status、瞬态模型进度、工具块、授权和命令进程等局部投影。当前代码能够静态产生“session active、最后一条助手消息 complete”的组合，相关 action bar 又只依赖消息 status，因此 issue 058 的机制在当前 `main` 仍成立。

进一步扫描确认同一机制还覆盖初次校准窗口、旧 run terminal 与新 run 竞态、terminal 后迟到增量、外部 channel 停止能力、后台 outcome 归并、授权/命令 REST 对账和 terminal 详情刷新失败。这些场景共享“整轮 run、局部 item 与用户可见表面缺少统一不变量”的修复边界，适合由一个需求收束。

公开实践支持把 session/thread、turn/run 和 message/tool item 的生命周期分开，并在恢复时使用权威状态重新校准；公开资料不证明其它产品的具体 action bar 像素行为。active run 消息完整隐藏 action bar 是本项目经用户确认的产品决定，不冒充外部产品内部实现。

技术预研进一步收束了四个实现方向：前端不再保存可与 active run 分叉的第二份 `streaming` 真相，而是通过 selector 派生；后台“有新回复”由带稳定 run 关联的用户可见消息事实决定，不由 run outcome 推断；REST 全量快照与 push 交错时使用请求围栏和对象版本共同收敛，只有请求期间未被新 push 触碰的缺失项才可清理；最终验收采用确定性状态矩阵加真实 Tauri WebView 关键链路的分层组合。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-08-28 | 真实运行中出现 session busy 与消息完成表面矛盾 | 运行时观测 | issue 登记 commit `620f7852a8034934e31b8b744c6b23ae27836723`；观察窗口 01:12-01:17 | 任务切回后侧栏和停止按钮仍 active，消息已有时间/复制；后续运行继续并正常 terminal | strongly-supported | active | [issue 058](../../issues/058-active-run-projection-divergence/README.md) |
| E-002 | 2026-08-28 | 当前代码可确定性构造同一矛盾 | 代码 | `d766d87`；`sessionProjection.ts`、`conversation.ts`、`MessageList.tsx` | 历史 assistant 默认 `complete`；打开 session 独立依据 active run 设置 busy；action bar 与通用进度只看消息 status | confirmed | active | 本索引“代码锚点” |
| E-003 | 2026-08-28 | issue 登记后相关实现尚未变化 | 代码版本 | `git diff 620f785..d766d87 -- <相关运行投影文件>` 无输出 | 当前 HEAD 未通过后续提交自然修复 issue 机制 | confirmed | active | 无独立 artifact；Git 历史可复查 |
| E-004 | 2026-08-28 | 现有相关测试通过但没有覆盖跨层一致性不变量 | 受控检查 | `d766d87`；4 个 Vitest 文件，76 tests | conversation、session sync、sidebar、command presentation 测试全绿；现有断言覆盖 busy 与局部状态，没有断言 active message/action bar/统一活动表面 | confirmed | active | 本索引“验证记录” |
| E-005 | 2026-08-28 | 相邻乱序与恢复场景属于同一状态边界 | 代码 | `conversation.ts:694-825`、`commandRuntime.ts:78-99`、`ToolCard.tsx:399`、`runs.py` | 旧 terminal 可影响新 run、迟到 event 可重建 active、初始校准无 readiness、command refresh 只合并、桌面停止与 control scope 可不一致 | confirmed | active | 本索引“场景扫描” |
| E-006 | 2026-08-28 | Codex 将 thread、turn 和 item 生命周期分层 | 外部官方源码文档 | `openai/codex` app-server README，2026-08-28 获取 | thread 有独立 active 状态；turn 以 `turn/completed` 结束；item 有自己的 started/completed；async agent message 可见但不结束 turn | confirmed | active | [Codex App Server README](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md) |
| E-007 | 2026-08-28 | Claude Code 将会话、任务清单和后台工作分开展示 | 外部官方文档 | Claude Code docs，2026-08-28 获取 | 后台 Bash 有独立 task ID；task list 与 background task view 分离；Desktop 以侧栏和 tasks pane 分别承载 session 与后台活动 | confirmed | active | [Interactive mode](https://code.claude.com/docs/en/interactive-mode)、[Desktop](https://code.claude.com/docs/en/desktop) |
| E-008 | 2026-08-28 | 错过 terminal 后客户端需要显式重连对账或清理旧 active | 外部事故报告 | `openai/codex#18860`，用户报告，非稳定规范 | 报告记录服务端已 idle、客户端仍 Working 并继续按旧 turn 路由；为本需求的重连反例提供方向性证据 | directional | retained | [Codex issue 18860](https://github.com/openai/codex/issues/18860) |
| E-009 | 2026-08-28 | active run 消息完整隐藏 action bar，真实 Tauri 验收为最终 AC | 用户决定 | 当前需求会话 | 用户明确选择隐藏整个 action bar，并确认参考外部实践扩充场景和纳入真实桌面验收 | confirmed | active | 当前需求确认记录 |
| E-010 | 2026-08-28 | 可派生状态不应作为重复字段写入 Zustand store | 外部官方文档 | Zustand selectors 文档，2026-08-28 获取 | 官方示例明确用 selector 计算 derived state，以避免重复并保持 store 最小；支持移除会与 run ownership 分叉的可写 `streaming` | confirmed | active | [Zustand derived state](https://zustand.docs.pmnd.rs/learn/guides/beginner-typescript.html#derived-state-with-selectors) |
| E-011 | 2026-08-28 | 恢复时应由服务端实际活跃事实修正历史中的 stale in-progress | 外部官方源码 | `openai/codex` thread lifecycle / routing，2026-08-28 获取 | Codex resume 会合并实际 active turn，并在 thread 非 active 时把历史 `inProgress` 修正为 interrupted；客户端缓存与服务端 active turn 冲突时显式清理或改用服务端 run id | confirmed | active | [thread lifecycle](https://github.com/openai/codex/blob/main/codex-rs/app-server/src/request_processors/thread_lifecycle.rs)、[thread routing](https://github.com/openai/codex/blob/main/codex-rs/tui/src/app/thread_routing.rs) |
| E-012 | 2026-08-28 | 初始快照与增量流需要共同的同步边界，缺失项清理不能覆盖更新事件 | 外部官方文档 | Kubernetes API concepts，2026-08-28 获取 | List-Watch 用 resourceVersion 和 bookmark 表示初始状态完成点，随后只接收更晚事件；过期版本要求重建一致快照。agent-friend 不照搬协议，但采用等价的请求围栏与版本保护 | confirmed | active | [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/#efficient-detection-of-changes) |
| E-013 | 2026-08-28 | run 完成与用户可见回复是不同事实 | 外部官方协议与源码 | Codex app-server / exec，2026-08-28 获取 | `turn/completed` 表示 run 终态；`agentMessage` 是独立、显式的用户可见 item，成功 turn 也只是“有时”包含 final message；exec 只从完成的 agentMessage item 提取最终消息 | confirmed | active | [Codex App Server README](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)、[Codex exec event processor](https://github.com/openai/codex/blob/main/codex-rs/exec/src/event_processor_with_jsonl_output.rs) |
| E-014 | 2026-08-28 | 用户可见正文和 item/run terminal 是不可丢的协议层事件 | 外部官方源码 | Codex app-server client，2026-08-28 获取 | Codex 将 agent message delta、item completed 和 turn completed 归为 lossless tier；其它进度事件可降级。这支持“正文/终态权威，精细进度可回退通用活动”的优先级 | confirmed | active | [Codex app-server client](https://github.com/openai/codex/blob/main/codex-rs/app-server-client/src/lib.rs) |
| E-015 | 2026-08-28 | mock/runtime 测试与真实 WebView E2E 证明的边界不同 | 外部官方文档 | Tauri 2 tests / WebDriver，2026-08-28 获取 | mock runtime 不执行原生 WebView；Tauri 官方提供真实应用 WebDriver E2E。支持用 store/integration 测反序矩阵，再用真实 Tauri 验关键组合，不互相冒充 | confirmed | active | [Tauri tests](https://v2.tauri.app/develop/tests/)、[Tauri WebDriver](https://v2.tauri.app/develop/tests/webdriver/) |
| E-016 | 2026-08-28 | 取消请求需要精确绑定运行身份，接受请求不等于完成 | 外部官方协议 | Codex app-server，2026-08-28 获取 | `turn/interrupt` 以 thread + turn 标识目标，请求成功后仍要求等待 `turn/completed: interrupted`；独立后台 terminal 使用另一控制 API | confirmed | active | [Codex App Server README](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md#example-interrupt-an-active-turn) |
| E-017 | 2026-08-28 | 等待用户处理的 item 需要显式 resolved 与生命周期兜底清理 | 外部官方协议 | Codex app-server，2026-08-28 获取 | approval / request-user-input 在响应后发 `serverRequest/resolved`，turn start/completion/interrupt 也会发同一清理通知；item terminal 另由 `item/completed` 确认 | confirmed | active | [Codex App Server README](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md#server-requests) |

## 代码锚点

| 主张 | 当前锚点 | 直接观察 | 局限 |
| --- | --- | --- | --- |
| 历史 assistant 默认完成 | `frontend/src/stores/sessionProjection.ts:47` | `assistantMessage()` 创建 `status: "complete"` | 只证明当前投影机制，不单独证明历史事故运行了该 SHA |
| 会话 busy 独立来源 | `frontend/src/stores/conversation.ts:444-481` | `openSession()` 依据 `activeRunIdBySession` 单独设置 streaming | active run 与消息未建立统一展示关联 |
| action bar 与消息 status 耦合 | `frontend/src/pages/chat/components/MessageList.tsx:476-511` | streaming 消息隐藏 action bar；complete 消息显示时间与复制 | action bar 的具体视觉仍需真实桌面验收 |
| 旧 terminal 可清当前 streaming | `frontend/src/stores/conversation.ts:737-782` | terminal snapshot 按 session 直接把 view/current streaming 设 false，即使 active map 已属于另一 run | 需要技术方案决定如何消除重复 projection |
| 迟到 run event 可重建 active | `frontend/src/stores/conversation.ts:694-733` | active map 为空时未拒绝已知 terminal run 的后续 event，并写回 active | 正常 push 路径会先 flush，但不能覆盖异常乱序与重连 |
| 初始校准无 readiness | `frontend/src/stores/sessionSync.ts:147-191` | subscriber 启动后异步 `calibrateRuns()`，store 没有初次校准完成状态 | 用户是否能命中窗口取决于启动与交互时序 |
| command REST 只合并 | `frontend/src/stores/commandRuntime.ts:78-99` | pending/processes 响应合并进旧 map，不处理响应中缺失项 | disconnect 时另有本地降级，但不能覆盖所有漏帧/重连顺序 |
| live process 覆盖持久结果 | `frontend/src/pages/chat/components/ToolCard.tsx:399` | `process?.status ?? result.status` | 需要结合版本与 run ownership 决定最终 precedence |
| 外部 run 不可由桌面取消 | `agent_bridge/src/agent_bridge/routes/runs.py:51-72` | cancel 要求 `control_scope=desktop_text` | 后端保护正确，桌面仍需隐藏不适用操作 |

## 场景扫描

| 场景族 | 已确认风险 | 需求影响 |
| --- | --- | --- |
| 消息与 run | 历史 assistant complete、push `done` 与 Runtime terminal 存在独立时序 | action bar 与整轮完成必须由 run 身份和 terminal 约束 |
| 会话导航 | cached streaming、active map 与 detail projection 可分别更新 | 切回时必须重新解析统一当前状态 |
| 初次加载 | active run 校准完成前存在暂时 idle 窗口 | 增加恢复 readiness 与保守操作门禁 |
| push 重连 | 漏掉 terminal 或旧 active 项时可长期 stale | 权威对账需要清理缺失项并防旧响应覆盖新 push |
| 跨 run 乱序 | 旧 terminal 可清新 run；terminal 后 event 可复活旧 run | terminal dominance 与 active ownership 必须按 run 隔离 |
| 取消 | accepted 不等于 terminal；外部 control scope 不可由桌面停止 | 取消中继续 busy；停止入口按 control scope 提供 |
| 工具与命令 | ToolResult、approval、process、Dock 各读不同事实 | 建立统一 precedence 和权威缺失清理 |
| 后台结果 | 非 completed 一律 failed；无可见输出 completed 仍可能有未读 | unread 必须结合 outcome 与用户可见内容 |
| 无正文 run | hidden/ambient/voice/IM 可能占用 session 但没有 chat message | 使用 session 级通用活动，不篡改历史消息 |
| terminal 补全 | detail refresh 失败时消息与 run 可能不能同时收口 | terminal 先确定性结束，detail 只补最终内容 |

## 技术预研结论

| 原 review 点 | 外部实践 | 对 agent-friend 的收束 | 不采用的方向 |
| --- | --- | --- | --- |
| `streaming` 去留 | Zustand 用 selector 表达 derived state；Codex resume 以实际 active turn 修正历史状态 | 删除 store 与 session view 中可独立写入的 `streaming`；由 active run、初次校准状态和当前 session selector 派生 busy / recovering / controllable | 不保留“兼容缓存 + 多处同步”，因为它继续允许旧 terminal 或详情刷新写出矛盾组合 |
| 用户可见新回复 | Codex 把 `agentMessage` item 与 `turn/completed` 分开，完成不保证存在 final message | 给实时与历史 `ChatMessage` 保留稳定 `runId`；后台 terminal 后只在 session projection 中找到该 run 新增的 conversation-visible 消息时标记有新回复 | 不按 `outcome=completed`、`updated_at` 或“最后一条消息变了”猜测；这些会误报隐藏、ambient、memory-only、voice/IM 输出 |
| REST 与 push 对账 | Kubernetes List-Watch 用同步版本边界避免旧快照覆盖新事件；Codex 以服务端 active turn 修正本地缓存 | 每次全量请求记录本地 mutation epoch；响应合并仍按对象 version，缺失项只在其未被请求期间的新 push 触碰时清理；重连重新跑权威快照 | 本期不为所有 broker 新建跨进程全局 resourceVersion 协议；现有列表读取为单点快照且对象已有版本，请求围栏已能解决前端竞态，后续若出现多写者再升级协议 |
| 真实桌面验收 | Tauri 明确区分 mock runtime 与真实 WebDriver 应用 | Vitest/Bridge 测全部时序矩阵；真实 Tauri 用可控运行态验证 action bar、侧栏、composer、Dock 与 terminal 收口；任何 mock 注入只证明相应层 | 不用真实 LLM 随机时序承担完整回归矩阵，也不用浏览器/Vite 结果冒充桌面端 |

## 验证记录

### V-001 · 当前相关前端测试基线

- 日期：2026-08-28
- 代码：`d766d87ff2a0915cd76b2f353181e8ee96e8db2d`
- 命令：

  ```powershell
  .\scripts\frontend\test.ps1 src/stores/conversation.test.ts src/stores/sessionSync.test.ts src/pages/chat/components/HistorySidebar.test.ts src/pages/chat/components/commandRuntimePresentation.test.ts
  ```

- 结果：4 个 test files、76 个 tests 全部通过。
- 解释：证明现有局部契约基线稳定，不证明跨层一致性已经满足；issue 58 的核心组合没有对应断言。

## 剩余设计细化点

| 问题 | 已收束边界 | design 中仍需写清 | 阻塞情况 |
| --- | --- | --- | --- |
| derived selectors 的领域入口 | `runSnapshots + activeRunIdBySession + calibration phase` 是唯一运行 presentation 输入 | selector 名称、返回类型、组件消费者迁移顺序 | 不阻塞 design |
| 消息与 run 关联 | `ChatMessage.runId` 来自实时 run id 或持久事件 `meta.run_id` | 合并多段 assistant item、无正文 run、编辑重发后的身份规则 | 不阻塞 design |
| request fence 的最小实现 | 本地 mutation epoch + 对象版本，按 collection store 就近实现 | run、approval、command 三处公共小 helper 是否有真实重复，避免提前造框架 | 不阻塞 design |
| 真实 Tauri 可控运行态 | 分层组合已确认 | 使用现有 desktop-visual 注入能力还是新增测试 fixture，要在实现前核对现有脚本能力 | 不阻塞 design；若需要新增脚本按项目脚本规则处理 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
| --- | --- | --- | --- |
| 用一个需求统一收束 issue 058 与相邻场景 | E-001, E-002, E-005 | 当前 | 设计证明这些场景需要不同事实源或独立用户目标 |
| Runtime run terminal 拥有整轮结束语义 | E-002, E-005, E-006 | 当前 | Runtime 契约发生正式变更 |
| active run 消息隐藏整个 action bar | E-002, E-009 | 当前 | 用户重新调整产品交互，或桌面验收证明产生更严重可用性问题 |
| 精确活动缺失时保留 session 级通用兜底 | E-001, E-005, E-006 | 当前 | 存在更可靠且覆盖所有 run 的精确活动协议 |
| busy / recovering / controllable 从 canonical run state 派生，不再保存重复 `streaming` | E-002, E-005, E-010, E-011 | 当前 | 发现某个消费者必须表达不属于 run/calibration 的独立生命周期 |
| 后台“有新回复”以显式 conversation-visible message + run identity 判定 | E-005, E-013 | 当前 | 持久化事件无法稳定提供 run identity 或 presentation 事实 |
| REST/push 对账使用请求围栏与对象版本，不做无保护 replace | E-005, E-012 | 当前 | 后端列表不能保证单点快照，或出现跨进程多写者导致本地围栏不足 |
| 停止入口按 active run identity 与 `control_scope` 派生，accepted 后等 terminal | E-005, E-016 | 当前 | Runtime 取消契约或控制所有权发生正式变化 |
| approval / command 的 terminal cleanup 使用显式 item 终态并由 run terminal 兜底 | E-005, E-017 | 当前 | 独立后台命令获准脱离 run 长期存活 |
| 把真实 Tauri 验收列为最终 AC，并与确定性矩阵分层 | E-009, E-015 | 当前 | 用户明确改为自行验收或取消该 AC |
| 不照搬 Codex/Claude 的具体 UI | E-006, E-007, E-008 | 当前 | 获得可核验且适合 agent-friend 约束的公开产品规范 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
| --- | --- | --- | --- | --- |
| E-001 | issue 058 引用的真实运行观察；本目录无原始截图/日志 | [issue 058](../../issues/058-active-run-projection-divergence/README.md) | 原始 artifact 未归档，保留摘要与时间窗口 | 仓库内摘要；不含用户正文 |
| E-002-E-005 | Git 仓库 `d766d87` 与所列代码路径 | 当前仓库 Git 历史 | commit SHA 可复查 | 私有产品源码 |
| E-004 | 当前终端执行结果 | 本索引 V-001 摘要 | 保留命令、范围与计数；未归档完整控制台输出 | 无用户数据 |
| E-006 | OpenAI 官方公开仓库 | 外部 URL | 2026-08-28 获取；页面后续可能演进 | 公开 |
| E-007 | Anthropic 官方公开文档 | 外部 URL | 2026-08-28 获取；页面后续可能演进 | 公开 |
| E-008 | OpenAI 官方仓库中的用户 issue | 外部 URL | 方向性事故报告，不视为稳定规范 | 公开 |
| E-009 | 当前需求会话中的用户确认 | requirement 与本索引决策摘要 | 无独立会话导出 | 不归档用户完整对话 |
| E-010-E-017 | Zustand、OpenAI Codex、Kubernetes、Tauri 官方文档或源码 | 外部 URL | 2026-08-28 获取；版本演进时需重新对账 | 公开 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
| --- | --- | --- |
| 2026-08-28 | issue 058、关联需求、当前代码、测试基线、Codex/Claude 公开资料与用户决定 | 一致；将 action bar、初始化 readiness、乱序 run、外部 control scope、后台 outcome 和真实 Tauri 验收纳入 107 |
| 2026-08-28 | Zustand derived state、Codex 恢复/事件分层、Kubernetes List-Watch、Tauri 测试边界 | 收束 `streaming` 派生、可见回复判定、REST/push 请求围栏与分层桌面验收；四项均可进入 design |
