# 111 · Agent 运行连续性与执行效率 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：触发需求 111 的真实诊断包、运行时/缓存分段事实、历史构建身份、相关源码基线与外部实践。
- 本索引不记录：诊断包中的完整用户/助手正文、工具参数与结果原文、隐藏推理、附件、凭据、完整日志或可逆 prompt/transcript 指纹。
- 最近对账：2026-08-30，已对账 requirement 范围、私有诊断包脱敏汇总、构建 commit 与 OpenAI 官方文档。

## 当前综合判断

1. 本地 01:36:50 的长时间工具事务已持久化 request 并开始 batch，但在 01:52:35 电脑卡死重启前没有 result 或 run terminal。重启后 `cross_process_resume / restored_without_prefix` 是正确的新 epoch 分类，但持久化业务 Session 缺少对确定失联孤儿事务的可信追加终态。
2. 本地 02:28 的另一次 Bridge 重启由用户主动切换默认对话模型触发，不是无原因崩溃；配置变更与电脑重启都应建立新 cache epoch，但它们的原因和任务恢复能力需分开表达。
3. 只有 Shell/WebView 重启且 Bridge、SessionManager、SessionModelHistory 与原 run 均存活时，重挂接后才有足够事实继续 exact continuation；Bridge/电脑丢失后不通过新增 model-only transcript 持久化伪造续接。
4. 该会话的同一用户轮工具续轮报告 98.82% cache read，但同进程且 final wire strict extension 的跨用户首请求仅 5.42%；两次明确跨进程请求为 2.58%。跨进程可解释两个新 epoch，不能解释同进程低命中。
5. 独立真实 API 严格扩展观测约 99.8%～99.95%，插入 Memory extraction/reconciliation 后仍为 99.95%。这些对照支持“前缀事实与 provider cache read 分开报告”，但当前未提供完整原始实验 artifact，不用于将同进程低命中归因给 111。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-08-30 | 诊断包是否可用且能否绑定运行身份 | 运行时 | bundle SHA-256 `27304744a202f1923b7372b415190e38dfda8a34e0a1a06b5d241fddd28dff60`；format v1/revision 9；commit `1cc60817f662ab13e23693ce36d44ee262d6f4f0`；`dirty=true` | ZIP CRC 通过，27 个 entry，无 unsafe/duplicate entry，唯一选中 Session 完整收入；未提交构建输入不可知，所以精确源码机制保留 identity gap | confirmed（包结构）/ strongly-supported（提交基线解释） | active | 本索引 |
| E-002 | 2026-08-30 | 01:36 长任务在电脑重启时是否留下孤儿事务 | 运行时 + 用户核验 | run `25e06aef-260d-4389-a8e1-5397453b4852`；tool event `tool_dd508a06e88e4e6992ddfad02f3bfbe2`；01:36:50→01:52:35 | 持久化事件停在 request/batch start，无 result/terminal；电脑卡死重启后进程身份变化且下一轮为 cross-process/new epoch | confirmed | active | 本索引 |
| E-003 | 2026-08-30 | 02:28 Bridge 重启的原因是否为无原因崩溃 | 运行时 + 用户核验 | 02:28 配置保存与 Bridge generation 变化 | 用户确认主动切换默认对话模型；日志在同时段记录模型配置保存与服务终止 | confirmed | active | 本索引 |
| E-004 | 2026-08-30 | 跨进程是否足以解释该 Session 的低 cache read | 运行时分段 | 同进程跨用户 `102144/1884672=5.42%`；跨进程 `6272/243049=2.58%`；同轮工具续轮 `98.82%` | 两个跨进程分段是合理 new epoch；10 个同进程 strict-extension 首请求仍低命中，说明还有独立 Provider/路由问题 | confirmed（统计）/ unconfirmed（同进程根因） | active | 本索引 |
| E-005 | 2026-08-30 | 真实 API 严格扩展是否能在相同类型 Provider 缓存中获得高命中 | 受控实验摘要 | strict extension 约 `99.8%～99.95%`；插入 Memory extraction/reconciliation 后 `99.95%` | 用户提供已完成实验的汇总结论；当前缺少模型、route、调用次数、原始 usage 与 harness 锚点，只用于约束归因 | directional | retained | 本索引（未归档原始 artifact） |
| E-006 | 2026-08-30 | 当前代码基线如何处理未闭合工具事务与上下文结果 | 代码 | commit `1cc60817f662ab13e23693ce36d44ee262d6f4f0`；`agent/src/agent/sessions/session.py`；`agent/src/agent/context/tool_projection.py` | 提交基线的 LLM-only 投影会规范化未闭合工具事务，原始事件保留；工具结果投影可用 placeholder/recovery 控制成本。因历史构建 `dirty=true`，该机制只作提交基线证据 | strongly-supported | active | 代码路径 |
| E-007 | 2026-08-30 | 其它 Agent/API 如何表达上下文续接、item 顺序和运行终态 | 外部资料 | OpenAI Responses API 官方文档，2026-08-30 核验 | 官方资料把响应状态区分为 completed/failed/in-progress/cancelled/queued/incomplete；手工管理历史时要保留并原样回传已返回 item/phase；缓存优化要求稳定内容在前，动态上下文在后 | strongly-supported | retained | [Responses create](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)；[model guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.5) |
| E-008 | 2026-08-30 | 运行中的 Agent thread 重连与 stale in-progress 应如何区分 | 外部代码/协议 | OpenAI Codex app-server 官方 README 与 `thread_lifecycle.rs`，2026-08-30 核验 | app-server 对仍加载的 running thread 使用 resume/rejoin，并以 turn/item 终态收口；官方实现会在没有 live in-progress turn 时把历史中的 stale in-progress 标为 interrupted，而不是仅凭历史状态继续宣称 active | strongly-supported | retained | [app-server protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)；[thread lifecycle source](https://github.com/openai/codex/blob/main/codex-rs/app-server/src/request_processors/thread_lifecycle.rs) |
| E-009 | 2026-08-30 | 暂时无法取得运行状态时是否应直接写失败/中断 | 外部资料 | Kubernetes Pod lifecycle 官方文档，2026-08-30 核验 | 官方状态模型保留 `Unknown` 表示无法取得状态，并把状态、条件和 reason 分开；只有后续取得明确生命周期事实或命中失联政策才进入终态。该实践只用于约束三值恢复裁决，不外推 Kubernetes 的具体超时 | strongly-supported | retained | [Pod lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/) |
| E-010 | 2026-08-30 | 需求 111 的实现是否满足恢复、隐私、状态和前缀边界 | 本地确定性验证 | 分支 `feature/111-agent-run-continuity-and-efficiency`；backend `2372 passed, 4 skipped`；frontend `672 passed`；Rust `218 passed, 1 ignored` + helper `6 passed` | runtime snapshot/journal、identity fence、orphan 三值裁决、并发幂等、安全业务基线、Skill/scope delta、命令双层状态和配置 restart reason 均有确定性回归；完整门禁全绿。未调用真实 LLM，provider candidate cache 数值未复验 | confirmed（本地机制与门禁）/ not-run（真实 provider candidate） | active | [progress.md](./progress.md) 与对应测试路径 |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
| --- | --- | --- | --- |
| 何时可以对孤儿工具事务写 interrupted | 已收敛：当前 runtime owner 保持 unknown；tool-specific adapter 的 running/completed/lost/unknown 优先；只有旧 owner 确定不存在且无恢复协议，或 adapter decisive lost 时 interrupted | 新增可持久恢复工具时，为该工具注册带身份校验的 adapter 并补矩阵测试 | 已解决当前能力边界；未来工具按协议扩展 |
| 同进程 strict extension 为何只有 5.42% cache read | Provider cache TTL/路由、线路处理、计费口径、还有未观测差异 | 独立任务的线路级请求对照与多次真实 API replicate | 不阻塞 111，不得在 111 中强行归因 |
| `semantic_status` 应由谁提供 | 已收敛：通用 CommandRuntime 只给 `not_evaluated/unknown`；只有领域 wrapper 可显式裁决 | 新增领域 command wrapper 时补语义状态测试 | 已解决当前通用 runtime 边界 |
| 上下文治理应设定何种量化目标 | 本需求不设 provider 命中率目标；本地候选以 strict prefix、contributor 去重、bounded result 与完整多维观测为准 | 若后续获真实 LLM 授权，按 §11.3 分段补 candidate provider 数据 | 不阻塞 111；真实 provider 数值未复验 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
| --- | --- | --- | --- |
| Shell/WebView-only 且 Bridge/run 存活时继续 exact continuation | E-006, E-007, E-008 | 当前 | 证明现有 model history 在 Shell/WebView 重挂接中并不存活 |
| Bridge/电脑丢失与模型/Provider 配置变更建立新 epoch | E-002, E-003, E-004 | 当前 | 需求 110 的隐私或 epoch 决策被新的正式需求覆盖 |
| 不为跨进程续接持久化 model-only transcript | E-001, E-002, E-006 | 当前 | 项目隐私决策正式变更 |
| 对确定失联孤儿事务追加 interrupted，可恢复时先 reattach | E-002, E-006, E-008, E-009 | 当前 | 发现持久化 request 并非权威事务事实，或现有工具无法提供任何可恢复身份 |
| 同进程 DeepSeek 低命中留给独立任务，111 不归因 | E-004, E-005 | 当前 | 新的受控证据确认本地 111 变量是同进程低命中的决定性原因 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
| --- | --- | --- | --- | --- |
| E-001～E-004 | 用户本机私有 diagnostics ZIP（精确路径有意不入库） | 未归档原件；仅保留本索引脱敏汇总 | SHA-256 `27304744a202f1923b7372b415190e38dfda8a34e0a1a06b5d241fddd28dff60`；ZIP CRC 已通过 | 含完整选中会话、工具参数/结果和日志；仅用户本机受控访问，不复制进仓库 |
| E-005 | 用户提供的实验汇总 | 未归档原始 artifact | 未校验 | 只保留聚合比例；不记录 prompt/response |
| E-006 | Git commit `1cc60817f662ab13e23693ce36d44ee262d6f4f0` | 仓库 Git 历史 | Git object 本地已校验 | 代码可查；历史构建另有未提交 overlay 缺口 |
| E-007 | OpenAI 官方文档 | 公开 URL | 2026-08-30 已打开核验 | 公开资料 |
| E-008 | OpenAI Codex 官方仓库 | 公开 URL | 2026-08-30 已打开核验 | 公开源码与协议 |
| E-009 | Kubernetes 官方文档 | 公开 URL | 2026-08-30 已打开核验 | 公开资料 |
| E-010 | 当前 feature 分支代码与本地门禁输出 | 仓库工作区、`progress.md` | 三阶段门禁按项目脚本执行并通过；实现尚未提交 | 不含 prompt、response、Skill/Memory/tool 正文或 credentials；只记录测试计数与结构化结果 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
| --- | --- | --- |
| 2026-08-30 | 需求 111 确认范围、诊断包结构/运行身份、用户补充事件原因、缓存分段统计、代码基线，以及 OpenAI/Codex/Kubernetes 官方实践 | 一致；明确不修改 110 隐私/new epoch 边界，不将同进程低命中归因给 111；保留无法取得状态时的 `Unknown`，只在有 live owner 或决定性失联证据时分别 reattach/interrupted |
| 2026-08-30 | 需求 111 实现与完整本地门禁 | 一致；A 类只读取存活 runtime snapshot/journal 并保持进程内历史，B/C 类建立新 epoch；orphan、model-only 隐私、Skill/scope delta、命令状态和 restart reason 均有确定性测试。真实 provider candidate 未运行，DeepSeek 低命中仍由其它任务调查 |
