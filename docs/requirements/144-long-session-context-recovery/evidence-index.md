# 长会话上下文管理与原会话恢复 - 证据索引

## 用途与范围

- 归属：[需求 144](requirement.md)。
- 记录两次已调查故障、真实保存数据的离线计量、主干代码边界和官方实现对照。
- 本索引区分历史故障、官方参考和候选验证；各自结论仅适用于所列版本及场景。
- 原始会话、消息、工具正文、system/Skill 内容、模型路由及不透明推理载荷不进入仓库。
- 最近对账：2026-09-28。

## 当前综合判断

两次故障均在本地压缩的 `retained_context_over_budget` 分支暂停，而非摘要模型返回失败。第二次故障的真实保存数据能够重建与日志完全相同的预算值；删去大量历史后，保留的不透明状态仍使旧整段输入计量残留。源码与离线比较确认了该计量机制，安装包 dirty overlay 和最终 wire digest 的缺口仍保留。

当前用户轮的整体保护，以及“继续任务”复用原用户事件的行为，共同限制原会话恢复。需求 143 的 PDF 输入调整没有覆盖这个通用机制。修复应同时处理计量、当前任务压缩和恢复路径；不能由此推导“直接删除 reasoning 就安全”。

## 证据账本

| ID | 日期 | 主张 | 类型与锚点 | 置信度 / 状态 |
| --- | --- | --- | --- | --- |
| E-001 | 2026-09-26 事件；本轮调查汇总 | 案例 A 在摘要调用前被本地预算可行性判断中止。 | runtime-observed；构建源码声明 `b09cec4d1ce4926bf6c1b7b289ae45bd46ab27eb`，dirty=true；诊断 ZIP。 | confirmed（失败分支）/ active |
| E-002 | 2026-09-27 事件；2026-09-28 汇总 | 案例 B 的历史缩短没有释放旧整段输入预算。 | runtime-observed + controlled-comparison；源码声明 `2070fe95c06ccc9f086ba2d7b4898334ca1a6979`，dirty=true；真实数据离线重建。 | confirmed（指定源码的计量机制）；strongly-supported（运行实例归因）/ active |
| E-003 | 2026-09-28 | 主干继续入口不自动移动用户轮边界，143 未改通用 opaque 计量机制。 | code-supported；`2ededbf8246027c6679979e8d140a29bd2d963d5`。 | confirmed（代码行为）/ active |
| E-004 | 2026-09-28 汇总 | 官方实现支持任务内压缩、有效上下文替换和同一 thread 显式压缩。 | external-source；`openai/codex` 固定版本 `e72da2b53805894878023d01949a25a082e0a5cb` 及官方文档。 | confirmed（公开源码/协议范围）/ active |
| E-005 | 2026-09-28 | 本需求继承安全与一致性约束，明确调整完整当前轮保留边界。 | code/document-supported；主干需求 063、130、135、137、143。 | confirmed / active |
| E-006 | 2026-09-28 | 候选实现可在同一任务内压缩并保留目标、事务和冷恢复边界。 | deterministic-tests；基线加本需求工作区变更，验证明细见 progress。 | confirmed（指定 fake 场景的机制）/ active |
| E-007 | 2026-09-28 | 首轮真实最终回答正确，但摘要误把宿主压缩指令记作用户任务。 | real-provider-observation；`gpt-6-sol / medium`、Responses、6 请求；候选 01。 | confirmed（该合成样本）/ corrected-by-E-008 |
| E-008 | 2026-09-28 | 来源边界修正后，真实摘要及任务承接均通过人工与结构化核对。 | real-provider-observation；同模型/medium、Responses、6 请求；candidate-02-source.json。 | confirmed（指定合成样本）/ active |
| E-009 | 2026-09-28 | 桌面恢复入口、请求模式和运行状态呈现符合本需求。 | desktop DOM via CDP + desktop interaction；6d73/feature144，合成接口/事件，11 项检查。 | confirmed（UI 层）/ active |

## E-001：案例 A

- 诊断包格式 1、schema 18，78 个条目，CRC 校验通过；采集 outcome 为 complete，无 collector failure。
- 事件为北京时间 2026-09-26 02:39；容量 250,000、阈值 200,000、估算 203,474，最近实测输入 195,696。
- 本次运行 4 次模型调用、11 次工具调用，工具均成功；压缩报 `retained_context_over_budget`，候选删除计数 244，未调用摘要模型。
- 最早未恢复边界：Context budget → Harness compaction preflight。修复 owner 为上下文计量与压缩编排。
- 局限：ZIP 不含完整 reasoning 和最终模型 transcript，不能对该次事件独立确认与案例 B 相同的 opaque 细节。源码身份含 dirty overlay，不能将声明 commit 当作安装内容逐字节证明。

## E-002：案例 B 与离线重建

- 事件为北京时间 2026-09-27 23:35–23:37，安装 build `20260922T144420Z-2070fe95c06c-bc40b9a3`，版本 0.3.0 local-test。进程路径、active-install、package-build 与 build-identity 一致；并非已包含 143 的新构建。
- 容量 400,000、阈值 320,000、估算 323,140；最近三次实测输入依次为 303,984、314,212、315,576。3 次模型调用、10 次工具调用均完成，随后在本地压缩前检查暂停。
- 用户输入很短且无附件，工具结果合计 20,921 字符。`provider_reasoning_state` 为 conversation scope；高成本历史是触发背景，不能把本轮短输入认定为主要原因。
- 只读取指定会话 JSONL 尾部 40 MiB。从 rewrite commit 沿 append commit 重建至最后工具结果，共 387 项，与最终 commit 的 `item_count` 一致。最新输入 anchor 对应 382 项、315,576 token。
- 在内存中使用保存的消息、system、tools 和 usage anchor，以及声明 commit 的 `context/budget.py`、`usage_anchor.py`、`usage_components.py` 纯函数重算；无网络、真实 LLM、业务动作或源文件修改。

| 候选 | 估算 token | 保留的 opaque 数量 |
| --- | ---: | ---: |
| 完整 387 项历史加 system | 323,140 | 77 |
| 从本次真实用户输入起的 20 项加 system | 323,140 | 2 |
| 从最后运行时 user 边界起的 11 项加 system | 323,140 | 1 |
| 20 项候选仅在内存移除 reasoning_details | 75,361 | 0 |

完整估算为旧 anchor `315576 + 7564`。缩短候选仍保留匹配 opaque 时，独立估算整体计入旧输入 anchor，不能释放被移除历史。75,361 仅为隔离机制的反事实估算，不是真实 provider token，也不证明删除 reasoning 的协议安全性或模型承接质量。

最早未恢复边界为 Context accounting → Harness compaction preflight；当前轮保护和继续入口为放大因素。修复 owner 为预算证据处理、压缩候选及原会话恢复编排。

局限：未独立校验 `message_sha256`、`transcript_sha256` 或最终 wire digest；纯函数取自声明 commit，未直接提取安装包的 dirty 内容。因此这是与日志数值一致的真实保存数据重建，不是逐字节 provider 请求重放。只覆盖一个当前本机案例，不代表发生频率或所有 provider。

## E-003：主干与恢复入口

固定源码基线：`2ededbf8246027c6679979e8d140a29bd2d963d5`。

- `agent/src/agent/context/usage_anchor.py`：`estimate_full_input` 对缺少独立 token 上界的匹配 opaque 仍使用完整输入 anchor。
- `agent/src/agent/context/summarizing.py`：压缩可行性依赖受保护尾部；143 在附件 preflight 改为统一请求估算，未替代上述 opaque 机制。
- 前端 `MessageList.tsx`、`stores/conversation.ts`、`services/stream.ts` 经 bridge `routes/meta.py` 调用 `continue_after_tool_error`；恢复使用新 run、原用户事件和 `resume_task=True`，不添加新用户轮。
- conversation scope 的推理状态可跨恢复运行保留。已有事务保护不自动重放完成工具；这不等于模型未来不会发出语义重复的新调用。
- 新发一条文本的第一轮组装仍可能引用旧 prefix，不能把“再发一句继续”当成已验证的通用解法，也不能反推会话永远不可恢复。

## E-004：官方实现对照

以下结论限定于公开 core、CLI 和 app-server；未观察闭源 Desktop 的完整交互，不从这些源码推断 Desktop 内部结构。

- [app-server 手动压缩](https://developers.openai.com/codex/app-server#trigger-thread-compaction)：同一 thread 提供 `thread/compact/start`，通过压缩生命周期事件反馈结果。
- [本地压缩](https://github.com/openai/codex/blob/e72da2b53805894878023d01949a25a082e0a5cb/codex-rs/core/src/compact.rs)：对历史副本生成摘要；容量失败时可以缩减源输入再试；成功后重建有界用户消息与摘要组成的有效上下文，并更新用量。
- [历史完整性](https://github.com/openai/codex/blob/e72da2b53805894878023d01949a25a082e0a5cb/codex-rs/core/src/context_manager/history.rs)：移除工具项时处理配对关系；其中密文长度启发式不能视为其他 provider 的通用 token 契约。
- [任务循环](https://github.com/openai/codex/blob/e72da2b53805894878023d01949a25a082e0a5cb/codex-rs/core/src/session/turn.rs)：任务中途压缩成功后继续同一循环；失败仍可能结束当前运行。
- [远端压缩](https://github.com/openai/codex/blob/e72da2b53805894878023d01949a25a082e0a5cb/codex-rs/core/src/compact_remote_v2.rs)：成功后替换上下文，对保留内容和预算设限；不能概括为所有远端失败都会回退本地。

采用方向：任务内压缩、成功 checkpoint 替换有效上下文、重新计量、同会话恢复与有界失败。不能照搬专属服务端 compaction 协议、假定所有模型支持 opaque 重放，或声称任何超限都保证自动恢复。

## E-005：既有约束

需求 063 的当前完整用户轮保护需明确调整；135 的失败不丢历史、承接语义和权限边界继续生效。130 的防低估、137 的可行性与冷恢复、143 的输入投影身份继续作为约束。具体关联见 [需求依赖表](requirement.md#关键信息与既有约束)。

## E-006：候选实现的确定性验证

候选由本需求分支基线加工作区 overlay 构成，尚未提交；[candidate-source.json](candidate-source.json) 固定 52 个改动源码、测试和脚本的字节指纹，未变文件继承所列基线。它不是两次历史故障的运行版本。目标用例全部使用合成消息和不透明占位状态，不复制真实会话载荷。

- 预算测试区分整请求实测、缩短候选的旧上界和未知；完整事务替换后估算释放。
- Conversation 入口测试覆盖一个用户输入内的 8 次工具调用、中途压缩、取消、冷启动，以及新消息和编辑重发。
- 含合成 conversation-scope opaque 状态的完整恢复测试确认最终请求退出旧状态、保留当前目标；原工具结果不重放。
- Bridge 和前端测试确认恢复模式、原因和操作从实时结果与历史保持一致，新增字段不授予继续资格。
- 初次扩围曾发现空尾部检查点冷恢复失效、重复归纳耗尽共享额度及测试类型错误，修正后对应目标测试通过；完整 gate 状态持续记于 [progress.md](progress.md)。

这些确定性证据只证明指定机制和协议行为，不证明真实模型的泛化成功率或用户实际旧会话已被恢复。真实观察见 E-007/E-008，桌面见 E-009；用户最终验收尚未完成。

## E-007、E-008：真实观察与人工复核

E-007 的自动报告为 passed，仅指三次最终 JSON 与工具计数断言；人工复核发现两个摘要均把宿主的格式/压缩要求当成用户请求，故不作为 AC-11 完整通过。首个工具闭合场景只替换旧前缀并产生 v3，真实完整事务摘要由第二个显式恢复场景覆盖。后续 fixture 增加明确的合成预算压力，要求首场景 v4 并核对四类工具事实进入摘要源。原始报告留在项目外 `agent-friend-144-long-session-observation-20260928-01.json`，含候选源码指纹，未修改或覆盖。

E-008 在用户补充本轮授权后完成，使用 [第二候选](candidate-02-source.json)。两个真实摘要均正确描述“报告状态”为当前业务目标，未虚构用户要求压缩记录；完整工具事实进入摘要，A/B/C/D 的已完成/运行中/未知/待处理状态未混淆。三次最终回答结构化核对通过，工具共调用一次，冷恢复复用同一 checkpoint。六次请求全部返回同名模型，输入/输出用量合计为 38,388/1,470 token。报告 `agent-friend-144-long-session-observation-20260928-02.json` 位于项目外系统临时目录，SHA-256 为 `af61dcbb45214f50295ae784a88d3156547f92fa4706ca96d99dc39684dc32b5`；源码指纹与当前候选一致。

这是单模型、合成来源和合成压力下的观察，不是统计成功率，也未修改或恢复用户实际旧会话。报告中的自动通过和本段人工判断分别记录；未运行额外模型裁判。

## E-009：真实桌面 UI

Tauri dev lease 对应当前 worktree 与 feature144，启动于相关前端源码最后变更之后；7 个前端源码指纹与当前候选一致。对真实 `chat.html` WebView 进行截图、DOM 定位和点击；恢复请求携带 `recoveryMode=compact_then_resume` 及正确 sourceRunId，运行中无重复恢复入口，完成/失败/取消、刷新投影、配置入口与旧字段兼容共 11 项检查通过。

服务 API 和 run 事件为合成夹具，截图是实际 WebView viewport（不含系统标题栏）；证据仅证明桌面 UI/请求层，不替代后端持久化或真实 provider E2E。隔离 settings/data/log/keyring 路径，默认 WebView2 缓存 profile 沿用；未选择或读取原用户会话，没有真实 API 调用。本任务启动的实例已停止，端口释放。

报告及截图位于系统临时目录 `af144-desktop-20260928-011957`。`desktop-probe.json` SHA-256 为 `dee1ded00f537db867bcfb8343461c1b9d51b2359f792ac4dbc678fed8b92962`；同目录保留 `source-identity.json`、runtime 身份及 `status-after-stop.txt`。

## 未决问题

| 问题 | 下一项区分性证据 | 当前影响 |
| --- | --- | --- |
| 各 provider 的 opaque 状态能否在 checkpoint 边界安全退出 | 已增加明确能力声明与完整组协议回归；真实观察仍需授权 | 未声明能力的适配器默认拒绝新任务内重建，不按密文长度或 replay scope 推定安全 |
| 摘要输入本身超限时，如何有界缩小且保持承接信息 | 已实现完整来源分段、共享 3 成功段 / 4 请求上限及对应回归 | 确定性机制已验证；过大必要单元仍明确失败，真实摘要质量待观察 |
| 其他模型/路由与真实旧会话的承接效果 | 按新增模型或实际旧会话分别验证 | E-008 只覆盖指定模型的合成场景，不外推普遍成功或已恢复用户旧会话 |

## 决策影响

| 决定 | 依赖证据 | 状态 | 重新打开条件 |
| --- | --- | --- | --- |
| 独立需求 144，基于已合入 143 的主干 | E-002、E-003、E-005 | 用户已确认 | 发现依赖契约改变 |
| 一并处理计量、当前任务压缩和原会话恢复 | E-001～E-004 | 需求已确认 | 方案证明需要超出已确认范围 |
| 保留失败保护、历史原件与执行事实 | E-004、E-005 | 需求已确认 | 需要新产品决策时重新讨论 |

## Artifact 映射

| 证据 | 原始位置 | 归档情况 | 完整性与访问 |
| --- | --- | --- | --- |
| E-001 | 用户提供的 2026-09-26 诊断 ZIP | 本仓库仅保存脱敏汇总；原件未复制 | ZIP CRC 已验证；原路径保留在授权调查会话中，私有 |
| E-002 | 用户本机指定会话 JSONL、bridge 日志与安装 manifest | 未复制大型私有原件；离线结果汇总于本页 | item_count 和日志数值对齐；未校验完整 transcript/wire digest，原文件可能继续变化 |
| E-003、E-005 | 本仓库 Git 历史 | 固定 commit 可复查 | 不依赖本机临时 worktree |
| E-004 | 官方仓库固定 commit 与文档 URL | 稳定外部引用，无本地副本 | 文档可能更新；源码链接固定版本 |
| E-006 | 本分支代码、合成测试与标准检查命令 | 测试源码保留在仓库；汇总见 progress | 工作区 overlay，未声称已提交 clean candidate；无真实用户载荷 |
| E-007、E-008 | 项目外系统临时目录中的两份 long-session observation JSON | 本页保留脱敏结论、候选和第二轮报告 digest；原件不入 Git | 纯合成输入/结果；临时目录原件可能被系统清理，不是永久归档 |
| E-009 | 项目外 `af144-desktop-20260928-011957` | 本页保留结果、runtime 约束与报告 digest | 真实 WebView UI，合成 API；临时证据可能被系统清理 |

后续确定性回归使用合成消息、合成 opaque 占位值和重新生成的 digest，不复制真实用户载荷。未保存离线测量脚本的独立 artifact，当前记录只提供重建步骤和结果，不能声称已有可一键重跑的 fixture。

## 对账记录

| 日期 | 范围 | 结果 |
| --- | --- | --- |
| 2026-09-28 | 两次历史观测、离线重建、主干和官方来源 | 建立初始索引；保留 dirty overlay、wire digest 和真实模型验证缺口。需求 CONFIRMED；方案和实现尚未确认或完成。 |
| 2026-09-28 | 已确认方案与候选目标测试 | 补充 E-006。T1～T4 已实现；完整检查进行中，真实模型、桌面和最终验收仍有缺口。 |
| 2026-09-28 | 两轮真实观察与人工复核 | 第一轮发现摘要来源混淆，修 prompt 并加强 fixture；第二轮 6 请求通过，保留首次不合格记录及两份候选身份。桌面和最终验收继续单独记录。 |
| 2026-09-28 | 第二候选与桌面收尾 | 后端 3,688 passed / 5 skipped，前端 1,338 测试、Rust 全量检查通过；桌面 11 项 UI 检查通过，实例已清理。技术验证完成，待用户最终验收。 |
