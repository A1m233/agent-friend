# 118 长会话查询与取消 — 证据索引

## 用途与范围

- 档案归属：[需求 118](requirement.md)。
- 本索引记录：需求确认前已有的运行时观察、三次离线 profile、定点代码发现、已读官方参考、用户确认的范围约束、实现阶段的隔离合成对照、追加摘要修复所依据的既有诊断、用户另行授权的 K3 Responses 阶段复查，以及随后采纳的展示回退、自动化验证与真实Tauri展示验收。
- 不包含：生产原始数据、完整真实 query / 正文、用户或会话身份、完整运行标识或可直接访问生产数据的 probe。E-013 至 E-015 仅归档已有诊断；E-016 是随后单独授权执行的六次合成真实请求，不外推为桌面或摘要取消验收。
- 最近对账：2026-09-06。历史聚合材料与边界见 [collection-notes.md](evidence/collection-notes.md)；03:47 UTC 阶段候选合成复测见 [E-012 记录](evidence/synthetic-query-final-320mib-notes.md)，前一次 [E-011](evidence/synthetic-query-320mib-notes.md) 保留历史用途。20:34–20:36 旧运行态及摘要恢复诊断见 [E-013 至 E-015 摘要](evidence/summary-recovery-diagnosis-2026-09-06.json)。

## 当前综合判断

长会话查询中存在可消除的读取放大：history source 先读取再做 scope 过滤，不足时扩量重跑；列表 summary 又需要寻找最新 model config。单次排除无资格当前会话的离线对照，把 history load 从 148 次降为 73 次、累计读取约 689 MB 降为 9.2 MB，支持优先解决查询范围与重复读取问题，但不能据此承诺稳定时延。

事故前缀复查保留了原 trace 的 source 计数，memory retrieve 仍只有约 11.55 秒，未复现线上 136.628 秒。额外约 125 秒的来源仍不确定；不能归因为锁、GC 或某个单独组件。原进程前一次 1.683 秒走的是 history skipped 路径，也不是同路径基准。

取消入口存在 runtime 登记前的读取窗口，登记后历史检索也缺少贯通的取消边界；前端多个终态入口可能重复获取大详情。它们是不同边界的问题，不应用一个总耗时或单点修补混为同一根因。

E-012 绑定 03:47 UTC 阶段候选的 23 个源文件与 harness hash，在 17 个 session、335,720,327 字节的合成输入上观察到资格排除和查询复用减少读取：candidate 查询三次为 20.331 / 2.404 / 3.303 毫秒，对照旧 history adapter 为 4,560.892 / 1,664.209 / 1,675.045 毫秒；详情首次仍扫描大文件，后续复用显著减少工作。结果签名与详情内容检查通过，但 fixture 主要是内部执行记录、可见历史极少，两边又共享当时 store 实现，因此不能当作完整旧产品基线、真实长会话时延或 p95 结论。E-011 保留当时数值，其当时的最终候选用途由 E-012 覆盖；两次未控制 OS cache 和机器负载，时延差异不能归因为新增日志的性能回归。这些合成证据不解释原现场额外约 125 秒。追加 K3 与摘要恢复修复后，E-012 原报告及源码锚点继续保留，不再代表当前组合候选或其最终性能验证。

用户在 20:34–20:36 的真实长会话测试来自 `0bcf` 的 116 运行态、HEAD `fba5ea4`，不是 118 验收。两次摘要分别耗时 704 / 812 毫秒后记录 `LLMBadRequestError` 与 `llm_error` fallback，观察范围内没有新 compaction。取消 HTTP 返回 200；从 stream `RunCancelled` 到 terminal done 约 5.645 秒，但按钮点击及 HTTP 响应时间缺失，不能把该区间写成点击后的取消耗时。

既有零网络对照确认：同虚拟环境的 LiteLLM 1.83.14 在 K3 low `generate(max_tokens=4096)` 发 HTTP 前拒绝 `reasoning_effort`；单项 allowlist 对照使 low/high/max 原样进入 mock 并成功。另一条恢复缺口是最近活动 v2 checkpoint 的三个边界 ID 属于 model transcript，恢复时在 product events 中找不到；未观察到 rewrite 失活。这两项分别支撑参数边界与 checkpoint 身份恢复的追加修复，不能用来断言真实摘要质量、所有历史 checkpoint 或原现场额外约 125 秒已解决。随后组合修复已通过后端全门禁（2721 passed、5 skipped，另含 lint/typecheck），记录见 [progress](progress.md)；真实摘要与取消链路仍待验收，限定桌面展示结果由 E-023 补充；E-012 不继承为组合候选性能证据。

21:14–21:16 北京时间的 K3 阶段复查（E-016）实际发出六次合成 Responses 请求，总计 2308 reported tokens、无自动重试，全部完成。公共接口的工具闭环成功但正文 phase 缺失；原始 SSE 的四次请求覆盖 low 直答、带现有阶段提示的工具前消息、工具结果续接和 high 直答，四个 assistant message 在 added / done / completed 共十二处均未包含 `phase` 字段，实际 ResponsesState 解析无错误且保持 None。此次样本的缺失可定位到厂商返回数据，不能归为 SDK 或 UI 丢字段；不宣称 K3 在所有配置或未来版本都无阶段能力。官方当前 schema 与指南也未声明该字段或开关（E-017）。该批探针仅验证原生能力；随后用户采纳的展示回退见 E-022，K3 原生能力声明仍未改变。

后续协议核对（E-018）确认 OpenAI 官方 Responses 输出消息的 `phase` 是 optional，允许 null；因此 E-016 的字段缺失本身不违反该字段契约，也不否定 Kimi 官网关于 Responses 直连的声明。追加展示回退前，整轮折叠依赖原生阶段是 115 的产品能力条件，不能把它等同为所有 Responses provider 的必备能力。公开检索（E-019）未找到 Moonshot 官方 K3 缺 phase 的直接同类 issue；找到的网关 SSE 事件缺失与工具 schema 拒绝属于不同边界，不能替代本次证据。

用户继续质疑该展示条件后，E-020/E-021 分别核对 Codex 公开实现与本项目基线策略：Codex 的工具 item、回合终态、无phase正文完成回退相互独立，TUI按工具结构压缩展示，不设全轮native门禁；本项目基线把native条件同时套在commentary与普通工具执行区外，阻断了K3的统一执行区按钮。单工具/工具组详情折叠原本就不依赖phase。该基线符合115当时文档，但不是工具收纳的技术必要条件；E-021 作为调查历史保留，其当前行为适用范围由 E-022 覆盖。不据公开CLI/协议推断闭源Desktop完整折叠算法。

用户已采纳并实现 `native / inferred / none` 展示来源分离（E-022）。无原生阶段时，仅在可信成功终态、无运行错误或仍运行工具、正文阶段均缺失或 unknown 等条件满足后，按首次出现顺序选出最后非空逻辑 assistant item；其所有片段留在区外，其他正文与普通工具进入执行区。任何已保存的 validation=conflict（包括 legacy）均不回退；原始 phase、模型历史、消息身份和顺序不变。最终冻结代码通过正式 `scripts/check/run.ps1 --stage frontend`：lint/typecheck、135 文件 / 912 tests（包含新增文件32项）、production build 全绿，总64.6秒。此前31/911只属于前一版增量，最终验证以本次runner为准；本次无后端 / Rust 增量，不重复这些阶段。E-022 归档时桌面仍未验证，该历史记录保留。随后用户允许临时停止116并在完成后恢复，E-023 已在身份绑定的118真实Tauri WebView上通过最终11组UI检查：真实开关、复制按钮payload截获、引用控件点击与原mid/UTF-16范围保留等；合成内存消息不构成持久化、真实K3或真实取消链路E2E。第01/03轮各因probe误拦正常IPC而整体失败，分别保留为harness故障；第04轮退出0、passed/restored均true。118已标准停止并释放端口，116最终恢复且bridge/voice健康HTTP200，保留运行供用户使用。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 / 基础 | 锚点与摘要 | 置信度 | 状态 | 详细记录 |
|---|---|---|---|---|---|---|---|
| E-001 | 2026-09-06 | 原异常与前次快速路径是否相同 | 既有运行时观测 / runtime-observed | memory total 136.628 秒；前次 1.683 秒但 history skipped；取消 404 后处理仍继续。原完整日志未归档 | strongly-supported，限既有调查摘要；额外耗时归因 unconfirmed | active | [原观测摘要](evidence/prior-runtime-observations.json) |
| E-002 | 2026-09-06 | 事后数据能否重现事故路径 | 单次离线 profile / runtime-observed | 历史代码 `fba5ea4`；事后当前快照 7.539 秒、148 次 load；source 计数与原 trace 一致，未保存最终 ID hash | confirmed，限报告中的计数与耗时 | superseded：仅“事故前缀复查”用途被 E-003 取代；事后快照观察仍保留 | [聚合 profiles：E-002](evidence/recall-profile-aggregates.json) |
| E-003 | 2026-09-06 | 去除事后尾部是否改变判断 | 单次前缀修正 profile / runtime-observed | 当前会话截到 335,263,838 字节，去除 762 字节；retrieve 11.554 秒、history 11.045 秒、list 3.344 秒、load 7.083 秒 | confirmed，限该隔离样本 | active；supersedes E-002 的事故前缀用途，不是完全重现事故 | [聚合 profiles：E-003](evidence/recall-profile-aggregates.json) |
| E-004 | 2026-09-06 | 无资格会话的前置排除是否减少工作量 | 单次反事实对照 / controlled-comparison | 事后当前快照排除一会话后 retrieve 0.936 秒、load 73 次 / 0.085 秒；E-003 / E-004 的 12 项排序 ID hash 相同 | directional；指标记录 confirmed，稳定收益未证明 | active | [聚合 profiles：E-004](evidence/recall-profile-aggregates.json) |
| E-005 | 2026-09-06 | scope 与读取顺序怎样放大成本 | 代码 / code-supported | `f563938` 的 history source 先 list/load，scope 后过滤并可能扩量重跑 | confirmed，限代码机制 | active | [定点代码核对](evidence/collection-notes.md#既有代码发现的定点核对) |
| E-006 | 2026-09-06 | 列表为何受长历史尾部影响 | 代码与离线计数 / code-supported + runtime-observed | summary 耦合 model config 反扫；E-003 跨 138,471,636 字节、26,098 行 | confirmed，限代码及该样本 | active | [定点代码核对](evidence/collection-notes.md#既有代码发现的定点核对) |
| E-007 | 2026-09-06 | 取消为何需要覆盖准备与检索边界 | 代码与既有观测 / code-supported | runtime.submit 前两次 open；未登记 cancel 返回 404；检索缺取消参数，Exception 捕获是未来取消语义兼容风险 | confirmed，限代码机制；不宣称当前吞过取消异常 | active | [定点代码核对](evidence/collection-notes.md#既有代码发现的定点核对) |
| E-008 | 2026-09-06 | 详情刷新是否避免重复后端工作 | 代码 / code-supported | 多个终态路径触发详情 GET；序号丢弃旧响应，没有在途请求合并 | confirmed，限可达代码路径；事故次数未定量 | active | [定点代码核对](evidence/collection-notes.md#既有代码发现的定点核对) |
| E-009 | 2026-09-06 | 同类实现提供哪些边界参考 | 已读官方源码 / code-supported | Codex `3525845`：准备前生命周期、取消收口、SQL 分页；Mem0 `dae67f7`：候选与底层限额 | confirmed，限固定源码所示机制 | retained | [参考适用边界](evidence/collection-notes.md#已读外部参考的适用边界) |
| E-010 | 2026-09-06 | 如何约束当前需求与验收 | 用户确认 / human-reviewed | 时延只观察比较，不设置 1 秒 / 2 秒 / p95 硬门槛；分页本阶段只评估可行性，是否实现留待 design 确认 | confirmed | active | [需求](requirement.md)与本索引“决策影响” |
| E-011 | 2026-09-06 | 查询资格排除与复用是否减少合成大文件读取并保持该样本结果 | 隔离合成对照 / controlled-comparison | 固定 `f563938` 旧 history adapter，对照当前 22 文件 hash 绑定的未提交实现；17 session / 335,720,327 字节，每路径 3 次；结果签名与 detail events 相同 | confirmed，限报告数值和该 fixture 的一致性；真实场景收益 directional | superseded：仅最终候选用途由 E-012 取代；该次观察 retained，不替代事故证据 | [对照记录](evidence/synthetic-query-320mib-notes.md)、[完整统计](evidence/synthetic-query-320mib-report.json) |
| E-012 | 2026-09-06 | 03:47 阶段候选补齐观测后是否仍减少无关读取并保持该合成样本结果 | 隔离合成复测 / controlled-comparison | 报告时间 `03:47:01.236771+00:00`；23 源文件 hash、harness `3c435840`；每路径 3 次，results/detail/unchanged 三项为 true；file-operation 次数和字节与 E-011 相同 | confirmed，限报告数值、身份及该 fixture 一致性；生产收益 directional | retained；覆盖 E-011 当时最终候选用途的关系保留，不代表后续追加修复候选 | [当时最终复测记录](evidence/synthetic-query-final-320mib-notes.md)、[完整统计](evidence/synthetic-query-final-320mib-report.json) |
| E-013 | 2026-09-06 | 用户晚间长会话测试验证了哪个运行态、摘要和取消阶段 | 既有运行时诊断摘要 / runtime-observed | 20:34–20:36，`0bcf` / 116 / `fba5ea4`；摘要 704 / 812 ms 后 LLMBadRequestError、fallback llm_error、0 新 compaction；cancel 200，RunCancelled→terminal done 5.645 s | strongly-supported，限既有时间与身份摘要；缺完整原始日志 | active；补充 E-001 / E-007，不能作为 118 验收或点击后取消时延 | [派生诊断摘要](evidence/summary-recovery-diagnosis-2026-09-06.json) |
| E-014 | 2026-09-06 | K3 摘要调用是否在本地参数校验就失败 | 已完成零网络对照 / controlled-comparison | 同 venv：LiteLLM 1.83.14 / OpenAI 2.24.0 / httpx 0.28.1；low generate + max_tokens 4096 在 HTTP 前抛 UnsupportedParamsError reasoning_effort；仅允许该参数后 low/high/max 原样进入 mock 并成功 | confirmed，限本地拒绝与对照机制；历史摘要失败关联 strongly-supported | active；支持追加修复，非真实厂商或摘要质量验证 | [派生诊断摘要](evidence/summary-recovery-diagnosis-2026-09-06.json) |
| E-015 | 2026-09-06 | 活动 v2 checkpoint 为何不能按既有恢复检查定位边界 | 既有代码与元数据诊断 / code-supported + runtime-observed | 一个活动 checkpoint 的 covered / retained / source 三个 ID 均属 model_transcript_message，不在 product events；分别关联 synthetic turn_aborted、synthetic skill_filesystem_scope_expiry_delta、current_input；无 rewrite 失活 | confirmed，限该 checkpoint 与创建 / 恢复身份空间不一致机制 | active；不外推全部历史 checkpoint | [派生诊断摘要](evidence/summary-recovery-diagnosis-2026-09-06.json) |
| E-016 | 2026-09-06 | 当前 K3 Responses 无折叠是否源于下游丢失原生 phase | 授权真实合成请求与实际解析器核对 / runtime-observed + code-supported | 13:14–13:16 UTC；2次公共接口 + 4次原始SSE；均completed，总2308 tokens；low/high、直答/工具前消息/工具续接；12个消息生命周期节点均 phase_present=false，解析器无错误 | confirmed，限六次样本；稳定支持率和全配置能力未证明 | active；补充114/115历史阶段证据，非118摘要或桌面AC | [归档口径](evidence/k3-phase-20260906/notes.md)、[公共接口](evidence/k3-phase-20260906/public-smoke.json)、[原始SSE字段摘要](evidence/k3-phase-20260906/raw-sse.json) |
| E-017 | 2026-09-06 | K3 是否文档化了阶段输出或启用开关 | 官方文档实时复核 / external-reference | Responses页面与OpenAPI无 phase/commentary/final_answer；输入/输出message schema均无phase；Codex接入指南仅配置wire_api=responses，没有阶段开关 | confirmed，限该次官方文档内容；缺失声明不证明永不支持 | active；无依据通过增加未知参数启用原生阶段 | [官方Responses](https://platform.kimi.com/docs/api/responses)、[OpenAPI](https://platform.kimi.com/docs/openapi.json)、[Codex接入指南](https://platform.kimi.com/docs/guide/codex-kimi) |
| E-018 | 2026-09-06 | 不返回phase是否违反Responses字段契约 | 官方参考实时复核 / external-reference | OpenAI create参考HTTP200，ResponseOutputMessage明确 phase optional commentary/final_answer/null；Kimi指南宣称原生Responses直连，未找到phase或折叠承诺 | confirmed，限字段可选性和页面内容；不构成全部协议兼容认证 | active；细化E-016/E-017解释，不否定实测 | [OpenAI输出消息定义](https://developers.openai.com/api/reference/resources/responses/methods/create)、[Kimi官方直连说明](https://platform.kimi.com/docs/guide/codex-kimi) |
| E-019 | 2026-09-06 | 是否存在公开同类phase反馈 | 公开GitHub issue检索 / external-reference | 精确kimi-k3/phase/commentary/final_answer，及openai/codex、MoonshotAI相关范围未找到直接同类；OpenCode#40325（8月3日/Open）报告第三方网关缺output_item事件；Codex#41233（8月28日/Open）报告经CC Switch转换后Kimi Coding拒绝工具schema | confirmed，限检索结果及已读issue内容；不证明全网无人反馈，issue归因未获维护者确认 | retained；相邻兼容性问题，不是官方K3缺phase的证据 | [OpenCode#40325](https://github.com/anomalyco/opencode/issues/40325)、[Codex#41233](https://github.com/openai/codex/issues/41233) |
| E-020 | 2026-09-06 | Codex是否把phase作为所有过程展示与完成的前提 | 官方源码与协议 / code-supported + external-reference | 固定ac192cd7937b0d73edc6dffe009940ae53782dd4；工具按独立item与status呈现，TUI按解析命令结构分组/截短；None phase保留legacy完成行为；SDK明确final优先、无则回退最后unknown正文 | confirmed，限公开TUI/core/SDK；闭源Desktop整轮箭头算法未验证 | active；不支持“所有折叠都必须phase”的假设 | [App Server文档](https://learn.chatgpt.com/docs/app-server)、[TUI工具呈现](https://github.com/openai/codex/blob/ac192cd7937b0d73edc6dffe009940ae53782dd4/codex-rs/tui/src/exec_cell/render.rs#L186-L238)、[TUI完成回退](https://github.com/openai/codex/blob/ac192cd7937b0d73edc6dffe009940ae53782dd4/codex-rs/tui/src/chatwidget/protocol.rs#L299-L337)、[SDK正文选择](https://github.com/openai/codex/blob/ac192cd7937b0d73edc6dffe009940ae53782dd4/sdk/python/src/openai_codex/_run.py#L45-L57) |
| E-021 | 2026-09-06 | 本项目是否把工具收纳与正文phase过度绑定 | 基线代码及115契约 / code-supported | `f563938` 基线：assistantPhase.ts:49仅允许native；executionDisclosurePolicy.ts:17将门禁套在commentary OR toolActivity外；ToolActivity.tsx:64/129已有无phase的独立工具折叠；所链本地策略文件现已更新 | confirmed，限基线门禁机制；后续产品变更已获用户确认 | superseded：当前展示行为由 E-022 覆盖；原调查事实 retained | [原生能力判断](../../../frontend/src/stores/assistantPhase.ts)、[现执行区策略](../../../frontend/src/pages/chat/components/executionDisclosurePolicy.ts)、[既有工具折叠](../../../frontend/src/pages/chat/components/ToolActivity.tsx)、[115历史需求](../115-assistant-phase-execution-disclosure/requirement.md) |
| E-022 | 2026-09-06 | 无phase成功运行能否按逻辑消息形成展示回退，同时保留原生事实 | 最终实现与确定性验证 / code-supported + deterministic-validation | 最终三份改动源码与正式检查入口hash；任何保存的conflict均不回退；正式frontend runner退出0，总64.6s：lint/typecheck、135文件912tests（新增文件32项）、production build通过；probe仅语法通过 | confirmed，限最终代码与自动化；真实Tauri仍not verified | active；supersedes E-021 的当前行为适用范围，原调查保留；K3 native能力不变 | [验证与源码锚点](evidence/disclosure-118-validation.json)、[已确认方案](design.md#7-原生阶段与推断展示分离) |
| E-023 | 2026-09-06 | 展示回退在真实Tauri中能否正确开关、复制和引用，并完成运行态恢复 | 授权真实WebView合成UI检查 / runtime-observed + human-reviewed | 最终04轮退出0，11组checks，passed/restored=true、零LLM；原mid导航、复制payload与UTF-16引用范围通过；源3hash与E-022一致；118停止释放后116已恢复且健康 | confirmed，限真实WebView展示/交互与所记生命周期；无系统clipboard、持久化或真实模型E2E | active；补充E-022桌面范围，保留其历史not verified及01/03 harness失败 | [验收口径与六图](evidence/disclosure-desktop-20260906/notes.md)、[派生报告](evidence/disclosure-desktop-20260906/report.json) |

| E-024 | 2026-09-06 | 主干整合后的组合候选能否完成收口 | 最终门禁、真实K3隔离smoke与synthetic性能 / deterministic-validation + runtime-observed | backend2872/5、frontend1213、Rust全绿；真实三场景6请求，独立进程复用、HTTP取消后续聊；320MiB三次结果一致 | confirmed，限归档口径；非生产桌面E2E/p95 | active；覆盖E-012/E-022最终组合候选用途，保留旧记录 | [收口口径](evidence/closeout-20260906/notes.md)、[验证锚点](evidence/closeout-20260906/validation.json) |

## 未决问题

| 问题 | 当前边界与最有区分力的下一项证据 | 阻塞情况 |
|---|---|---|
| 原运行额外约 125 秒耗在哪里 | 离线记录无法重建原进程的竞争、缓存与积累状态；需要身份绑定的原进程阶段时间、读取 / 解析、取消与终态时间 | 不阻塞已确认的问题范围；阻塞确定根因声明 |
| 排除与读取限额的收益是否稳定，是否保持质量 | E-002 至 E-004 各为单次；E-011 / E-012 分别有三次同类合成 fixture 对照及正文 / 顺序 guardrail，不合并成独立总体样本。OS cache 未控且可见历史极少，仍需不同可见历史规模、并发及失效条件的隔离比较 | 不支持真实长会话的稳定时延、p95 或新增观测开销归因 |
| 分页是否适合现有投影与恢复语义 | 官方 SQL 分页只说明一种可行机制；需评估本项目存储、排序、搜索、跳转与恢复边界 | 是否实施留待 design 确认 |
| 历史输入与 harness 能否精确恢复 | 临时输入已删除；原报告含敏感标识，留存 probe 又晚于前两份报告。只有数据拥有者重新提供有界输入后才能核对 | 本归档不能独立复现，当前也不授权重跑 |
| 追加摘要修复后的组合候选是否通过最终验证 | E-024已补齐主干整合后的完整门禁、真实K3三场景与最终性能对照；E-023保留UI范围 | 已收口；用户授权合入推送，仍不外推生产桌面E2E或历史事故归因 |
| 晚间测试是否还有无法量化的等待 | E-013 未记录点击和 HTTP 响应时间；原始异常全文及 checkpoint 原文未归档，无法从摘要精确重放 | 不支持点击到终态时延或完整现场复现声明；本归档不新增真实实验 |
| K3 后续能否声明原生阶段能力 | E-016在六次请求未观察到phase，E-017无官方开关；更有区分力的是厂商新增文档或实际带phase的完整工具闭环 | 原生能力仍未启用；用户已采纳的展示回退使用inferred，不冒充native |
| 展示UI证据是否覆盖系统剪贴板与端到端历史行为 | E-023已通过11组真实WebView合成UI检查；复制在writeText边界截获，引用写入合成内存草稿，搜索只注入导航目标 | 不外推系统clipboard、持久化/重载恢复、后端搜索或真实K3/取消链路；本记录不增加实验范围 |

## 决策影响

| 已确认约束或方向 | 依赖证据 | 当前状态与边界 |
|---|---|---|
| 将长会话读取、scope 过滤成本列入本需求 | E-002 至 E-006 | 已确认需求方向；具体数据结构与接口尚未在此决定 |
| 覆盖准备阶段与检索阶段的取消，减少重复详情请求 | E-007、E-008 | 已确认问题范围；取消协议、合并粒度与实现方案交由 design |
| 保留时延观察比较，避免把一次快结果变成服务承诺 | E-001 至 E-004、E-010 | 用户已确认；没有 1 秒 / 2 秒 / p95 硬门槛 |
| 分页只先评估可行性 | E-009、E-010 | 是否实现待 design 确认，未承诺 SQL 迁移或本期分页交付 |
| 不给原 136.628 秒选择未经证明的唯一根因 | E-001、E-003 | 继续保留额外约 125 秒的证据缺口 |
| 用资格排除和查询复用的合成结果补充实现验证 | E-012；E-011 保留历史用途 | 当时的候选在该 fixture 下减少读取并保持比较结果；不扩大到后续组合候选、生产性能、评分完全相同、取消时延或日志开销结论 |
| 将 K3 参数兼容与摘要 checkpoint 恢复缺口回到 118 统一落地 | E-013 至 E-015；用户确认“可以，就按这个顺序去干吧”并要求回 118 | 实现与自动化已完成，等待用户验收；方案与交付状态由 requirement/design/progress 维护 |
| 不因使用Responses而自动声明K3具有原生阶段能力 | E-016、E-017；115原生能力边界 | 当前样本仍无阶段字段，K3能力配置不变；展示回退独立使用inferred来源 |
| 采纳无phase成功终态后的展示回退，并区分native与inferred | E-020、E-021、E-022；用户明确确认 | 已在118实现并通过前端自动化；原始phase和模型历史不变，E-023补充11组真实Tauri合成UI结果，整体验收状态由progress维护 |

## Artifact 映射与完整性

| 证据 | 原始位置 | 归档位置 | 校验与访问边界 |
|---|---|---|---|
| E-001 | 已完成调查中的本机日志 / ring buffer；本次未重新读取 | [prior-runtime-observations.json](evidence/prior-runtime-observations.json) | 派生摘要，完整原日志未归档；不得宣称可从此恢复全文 |
| E-002 至 E-004 | 原机器旧 worktree 的 `.tmp/cancel-recall-analysis/`，只在本机可访问 | [recall-profile-aggregates.json](evidence/recall-profile-aggregates.json) | 三份 report 的原文件 SHA256、字节数，以及留存 probe SHA256 均在 JSON；真实输入不入库 |
| E-005 至 E-008 | `f563938bd3f63c88a5e7a5b59825e3da33f06303` 中的源文件 | [collection-notes.md](evidence/collection-notes.md) | 固定 commit / 行号链接；本次只做定点核对 |
| E-009 | 两个固定官方源码 commit | [collection-notes.md](evidence/collection-notes.md#已读外部参考的适用边界) | 沿用已读来源；不从公开 CLI 源码推断闭源 Desktop 的请求合并实践 |
| E-011 | 本工作树 `.tmp/query-118-320mib.json`；临时路径只在原机器可访问 | [synthetic-query-320mib-report.json](evidence/synthetic-query-320mib-report.json)、[归档口径](evidence/synthetic-query-320mib-notes.md) | 纯 synthetic 统计；仅 CRLF→LF，全部 JSON 字段保持；原文件 / 归档 hash、22 文件校验与 harness 归档时锚点见记录 |
| E-012 | 本工作树 `.tmp/query-118-final-320mib.json`；临时路径只在原机器可访问 | [synthetic-query-final-320mib-report.json](evidence/synthetic-query-final-320mib-report.json)、[归档口径](evidence/synthetic-query-final-320mib-notes.md) | 纯 synthetic 统计；仅 CRLF→LF；报告内 UTC、harness hash、23 个源码 hash 与归档时实文件核对通过 |
| E-013 至 E-015 | 当前任务已完成诊断；原运行位于本机 `0bcf/agent-friend`，日志 / 数据仅本机可访问 | [summary-recovery-diagnosis-2026-09-06.json](evidence/summary-recovery-diagnosis-2026-09-06.json) | 派生白名单摘要，非原始日志或 probe；无用户正文、会话 / checkpoint ID、异常全文或完整 runtime ID；原始 artifact hash 不可用，不能精确重放 |
| E-016 | 本工作树 `.tmp/k3-phase-{public,wire}-20260906-recheck-01.json` 与 `.tmp/k3_phase_wire_probe_20260906.py` | [归档口径](evidence/k3-phase-20260906/notes.md)、[公共接口](evidence/k3-phase-20260906/public-smoke.json)、[原始SSE字段摘要](evidence/k3-phase-20260906/raw-sse.json)、[历史探针](evidence/k3-phase-20260906/probe.py.txt) | 仅合成输入的字段摘要，不保留原始SSE/隐藏reasoning；完整阶段prompt改为仓库路径/hash引用；原报告、归档hash与harness复用位置边界见notes |
| E-022 | 实施者已完成正式runner检查的结果、冻结前端源码与正式脚本；未保存完整命令输出 | [disclosure-118-validation.json](evidence/disclosure-118-validation.json) | 白名单派生验证事实、八项归档时源码 / 入口hash；不含私有用户数据或绝对机器路径，不将归档时hash冒充测量前后双检 |
| E-023 | 本工作树 `.tmp/disclosure-118/` 四轮报告、最终probe、runtime/source与两次恢复记录；仅本机可访问 | [派生报告](evidence/disclosure-desktop-20260906/report.json)、[notes与六图](evidence/disclosure-desktop-20260906/notes.md)、[最终probe](evidence/disclosure-desktop-20260906/probe.mjs) | 绝对路径改相对绑定，保留原hash与失败摘要；最终六PNG/probe原字节，不含真实用户数据；restored合成视图与116恢复分开记录 |
| 已归档 artifact | 本需求 `evidence/` | [MANIFEST.sha256](evidence/MANIFEST.sha256) | 共30项；E-024新增七项，历史K3探针原字节改扩展名并同步notes；文本UTF-8 without BOM |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
|---|---|---|
| 2026-09-06 | 三份原报告、留存 probe、聚合字段与 source HEAD；当前基线定点代码；用户确认的需求范围 | 建立 E-001 至 E-010。E-003 取代 E-002 的事故前缀用途；保留未复现线上时延、正文 / 顺序未比较、脚本历史身份和输入已删除等局限。归档未新增实验或生产访问。 |
| 2026-09-06 | 最终 320 MiB synthetic 报告、固定旧 adapter、22 个 candidate 文件及测量脚本 | 新增 E-011；报告自报测量前后源文件不变，归档时 22 项与当前源码一致。旧 artifact 不变，新报告仅规范换行，manifest 和本地链接通过。保留当前 store 共用、tokenizer 共同预热、OS cache 未控、评分不比较及仅三次重复的边界；两份前期 smoke 不入本批归档。 |
| 2026-09-06 | 冻结最终代码后的复测报告、固定旧 adapter、23 个候选文件及报告内 harness hash | 新增 E-012，覆盖 E-011 的最终候选用途；E-011 文件与原 hash 不变。原 JSON UTC 字符串、harness 与 23 个源文件逐项核对；file-operation 次数 / 字节与 E-011 相同。保留未控 OS / 负载和三次合成重复的局限，不从两次时延差值推断日志回归。 |
| 2026-09-06 | 用户晚间旧运行态测试、零网络参数对照、活动 checkpoint 身份诊断及追加修复授权 | 新增 E-013 至 E-015；仅归档当前对话中已有诊断，不重读用户全文、重跑实验、调用 LLM 或启动 runtime。E-012 原文件和精确源码锚点不变，新增范围后仅作为历史候选证据；K3 定点 127 passed 不标为组合修复的最终门禁通过。 |
| 2026-09-06 | 用户单独授权的K3阶段复查、官方文档及现有组合修复进度 | 新增E-016/E-017，六次真实请求均在已告知边界内，无重试、不读真实会话、不启动dev；字段摘要和harness归档。组合修复后端门禁状态与progress对齐；真实phase探针不替代摘要/取消/桌面验收，不启用K3折叠。 |
| 2026-09-06 | 用户追问Responses符合性、官网承诺及公开issue | 新增E-018/E-019，仅查询资料，无模型调用或产品修改。明确phase可选性及115自身展示条件；保留未找到精确同类反馈的检索局限，不将网关事件缺失或工具schema错误混为同因。 |
| 2026-09-06 | 用户质疑115展示前提，并要求对照Codex | 新增E-020/E-021；公开源码统一固定ac192cd，明确CLI/协议与闭源Desktop边界；核对本地native门禁同时禁用工具统一执行区。工具可独立收纳是候选调整依据，未知正文不静默改phase，未改产品代码或已确认方案。 |
| 2026-09-06 | 用户采纳展示回退、来源分离及真实Tauri验收；归档最终实现与正式前端门禁 | 新增E-022并覆盖E-021的当前行为适用范围，旧调查与此前对账原文保留。补齐任何保存的conflict都不回退后，正式frontend runner退出0：135文件912 tests（新增文件32项），lint/tsc/三类治理与build通过，总64.6秒；以此取代前版31/911的最终验证用途。本次无后端/Rust增量，不重跑。旧13项artifact保持；probe仅语法通过且116占用唯一lease，桌面仍not verified，未停止或接管用户运行态。 |
| 2026-09-06 | 用户授权临时切换116、118真实WebView展示与补充引用/复制验收、运行态恢复 | 新增E-023：最终04轮11组通过，零LLM，六原图与final probe归档；01/03的最终guard误拦分别保留为harness失败，02保留9组较窄范围。三源码hash未变，912门禁继续适用；IPC可能fallback，复制未接触系统clipboard，无持久化/真实K3/取消E2E。两批118均标准停止后恢复116，最终健康HTTP200且保留用户运行态。E-022历史not verified不改，旧14artifact原字节保留。 |

| 2026-09-06 | E-024最终主干整合候选、完整门禁、6次真实K3调用与320MiB对照 | 新增七项artifact，真实模型只用隔离合成数据；源hash逐项核对。原K3探针按原字节改为.py.txt并更新其notes，其他历史artifact不变。用户确认按顺序收口、合入推送与本地测试包，运行/质量/计费局限见E-024。 |
