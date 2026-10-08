# 157 · 长会话加载与执行冷恢复治理证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)。
- 记录本次真实安装版慢加载、执行冷恢复的源码机制、已有需求边界、后续方案和验证证据。
- 仓库不保存会话正文、凭据或完整用户数据；固定源的隔离副本和桌面截图仅保留在本机私有证据目录。
- 最近对账：2026-10-09。需求与方案均 CONFIRMED；实现与本轮验证完成。用户在获知取舍及缺口后要求合入主干并推送，按已披露边界接受交付；未执行项目不追认为验证通过。

## 当前综合判断

1. 安装版目标会话的原始文件为 1,223,014,741 bytes。本次服务端详情请求在首响应前耗时约
   37.5 秒，期间两次 business 查询各约 18 秒；第一次因源文件变化未发布缓存。后台恢复
   追加终态与同一源文件变化形成一致证据链。读取慢及重复工作已确认。
2. 对应源码在分页前构建完整 business 查询；过滤内部记录发生在 JSON 解析和 rollout
   引用恢复之后。分页限制为用户轮数，不能保证单轮大量工具输出的传输与处理规模有界。
3. 服务端最终成功返回，不能证明目标前端当时已结束加载。用户切走后另一个会话才产生
   restore-ready；该全局里程碑不能作为目标会话首屏时间。
4. `/usage` 的约 124 秒是 SSE 连接寿命，不是另一段会话加载耗时。后续只读请求成功属于
   一次可用性观察；当时并发进行文件构成统计，不能作为受控性能对照或稳定性结论。
5. 安装校验 85.6 秒与后端就绪约 42.8 秒有独立启动证据；本需求不接管其优化所有权。
6. 基线执行热路径复用 `_runtime_states`，冷路径经 `load_execution_session` 从头扫描并恢复提交链。
   157 候选已加入独立纯状态快照，冷命中保留完整原始字节 SHA 校验和 writer lease 后复核；
   E-015/E-016 已确认跨进程复用收益，同时记录首次建立、峰值内存和热回合收尾成本；不把
   fake 请求边界写成真实供应商响应。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-10-08 | 目标安装版身份与服务端慢加载 | runtime-observed | 0.4.0 / build 20261007T163032Z-8eeb988f6591-878a2034 / source 8eeb988f65910d18ced4a16a2b409d0fc2cc1d71 | 22:03:31 左右开始，22:04:08 返回；37,508.127 ms / 7,184,444 bytes；两次查询 18,010.5 / 18,470.364 ms，首次 source_changed | confirmed；恢复追加是触发因素 strongly-supported | active | [观测摘要](./artifacts/20261008-loading-observation.json) |
| E-002 | 2026-10-08 | 文件体积由什么构成 | local-read-observed | 同一目标文件，采样前后 size / mtime 一致 | 51,684 行；rollout_commit、model_transcript_item、rollout_request_prepared 占主要体积 | confirmed（物理统计范围） | active | [观测摘要](./artifacts/20261008-loading-observation.json) |
| E-003 | 2026-10-08 | 分页为何仍会处理完整历史 | code-supported | installed source 8eeb988f；初次检查 db61c29c；相关文件逐一 diff 无差异 | meta.py:306 先 query、:326 后分页；store.py:672 / :740 在解码后过滤；query.py:156 构建投影 | confirmed（源码机制）；阶段内部耗时占比未测 | active | 下文源码锚点 |
| E-004 | 2026-10-08 | SSE 与全局里程碑是否证明目标首屏耗时 | code-supported + runtime-observed + user-confirmed | runs.py usage；HistorySidebar.tsx；用户明确已切走 | SSE 长连接寿命不计为加载；restore-ready 缺 session ID，不能归到目标会话 | confirmed（证据边界）；目标客户端耗时 unconfirmed | active | [观测摘要](./artifacts/20261008-loading-observation.json) |
| E-005 | 2026-10-08 | 是否承接已知剩余问题 | delivery-records | 122 / 145 / 146 / 149 的 requirement 与 progress | 122、145 阶段合入且保留缺口；146 针对召回/发送；149 仅采集 | confirmed（文档记录） | active | 下文既有交付边界 |
| E-006 | 2026-10-08 | 当前是否仍无法从后端读取 | runtime-observed | 同 installed build；GET active / turn_limit=30 | 22:09:05 请求成功，服务端 10,718.821 ms；缓存 miss 后重建 | confirmed（单次成功）；性能比较 inconclusive | retained | [观测摘要](./artifacts/20261008-loading-observation.json) |
| E-007 | 2026-10-08 | 与启动优化的分工和用户决定 | human-confirmed | 当前聊天；已读“排查 agent-friend 启动缓慢”最新讨论 | 本需求先行；历史加载归这里，启动编排与安装校验归另一项；不新增取消入口 | confirmed（本会话用户决定） | active | requirement.md；未向另一聊天发送消息 |
| E-008 | 2026-10-08 | 执行冷、热路径为什么工作量不同 | code-supported | d1068625；manager.py:376、execution_loading.py:25、rollout.py:487 | 热路径复用进程内模型状态，冷路径读取完整 journal 并验证每个 commit；纯状态具备持久化复用的候选边界 | confirmed（当前源码机制）；本次冷/热实测归因 unconfirmed | active | 下文执行恢复锚点 |
| E-009 | 2026-10-08 | 既有发送优化是否已消除执行冷恢复 | prior-reproduction-supported | 146 E-020，2026-10-01 固定副本和 final runtime overlay | 召回派生投影已能跨 runtime 重建复用；当时第二轮执行态加载仍单列 13.166 s，与其后 2.722 s 发送准备分开 | confirmed（历史实验覆盖）；非本次安装版性能 | retained | [146 证据索引](../146-memory-recall-and-send-latency-governance/evidence-index.md) |
| E-010 | 2026-10-08 | 157 是否包括执行恢复缓存 | human-confirmed | 当前聊天用户回复“可以” | 扩大为两条交付线；动态事实发送时刷新，保留来源完整性验证，不缓存整份最终请求重发 | confirmed（范围决定）；技术协议待确认 | active | requirement.md R-8/R-9、AC-8/AC-9 |
| E-011 | 2026-10-08 | 技术方案与实现授权 | human-confirmed + code-supported | 本聊天方案声明后的“可以”；runtime/__init__.py:701、conversation.py:2628/5671、runtime/scheduler.py:490 | 用户确认完整原始字节验证的快照、独立展示索引、动态刷新和分别验收并授权实现；源码显示 run_terminal 晚于 terminal rollout commit，维护须覆盖最终追加 | confirmed（授权与当前顺序）；实现收益待测 | active | [design.md](./design.md)、[progress.md](./progress.md) |
| E-012 | 2026-10-09 | 固定输入与离线测量边界 | isolated-fixture + deterministic-test | 1,223,014,741 bytes；SHA-256 `3417e5cfaa3482ee3e7267d8649d0006e2fcd242a6ca5bc84eda225dd0fe00d8` | 原始文件只读复制并前后核验；cold-cache 冻结四个产品包、独立进程、断网，offline 使用 fake provider；harness 8 项测试通过 | confirmed（输入与测量机制） | active | `scripts/session-latency/cold_cache_probe.py`、`offline_probe.py`、`test_cold_cache_probe.py` |
| E-013 | 2026-10-09 | 候选自动化门禁 | deterministic-test | d1068625 + 当前 feature overlay；入口与最终文件 SHA 已归档 | backend 3964 passed / 5 skipped；frontend 1471 passed；Rust 313 passed / 1 ignored；随后新增动态测试、测量 harness 检查及最终 lint 通过 | confirmed（对应源码覆盖）；非安装态验收 | active | [检查记录](./artifacts/20261009-validation.json)、[progress.md](./progress.md) |
| E-014 | 2026-10-09 | 真实桌面能否看到分页与延迟正文 | real-tauri-dom + interaction + runtime-observed | dev PID 49100、bridge PID 41168 均晚于源变更；固定副本；最终 run desktop-final-20261009-002617 | 13 项通过、2 项复制因权限未验证；长回合 80→98 片段、工具首尾完整、GB 47→101 片段；8 个真实历史页最大 261,786 B，导航与服务端请求关联 | confirmed（列明的桌面路径）；around 跨页、真实复制及安装态未覆盖 | active | [脱敏桌面记录](./artifacts/20261009-desktop-verification.json) |
| E-015 | 2026-10-09 | 持久索引及执行快照的跨进程效果与资源成本 | serial-isolated-comparison | d1068625 与冻结 workspace 四包 overlay；五组各 3 重复；统一 harness `0f348e88…add7943` | 新进程 query 中位 8.257→0.961 s；bounded history 36.47 ms；execution 16.426→2.244 s；48 个业务摘要、24 个 exact 恢复摘要一致，15 组进程内外等价断言通过 | confirmed（同样本存储行为）；环境与样本范围内的时延观测 | active | [完整脱敏对照、源码清单与逐次结果](./artifacts/20261009-storage-comparison.json) |
| E-016 | 2026-10-09 | 完整本地准备、驻留热复用与终态维护成本 | serial-isolated-comparison | E-015 同一产品源码树；schema 3 offline harness `86966f07…cdc1dc`；四组各一条 A,A 序列 | runtime 重建的第二次准备至 fake 14.244→4.382 s；驻留准备 1.451→1.493 s、含收尾 1.624→2.843 s；8 fake、0 real LLM，原件未改 | confirmed（路径与完成）；时延仅方向性，非统计稳定或真实供应商 E2E | active | [脱敏发送对照与阶段记录](./artifacts/20261009-local-send-comparison.json) |

## 源码锚点

E-003 / E-004 的历史来源为安装版 `8eeb988f65910d18ced4a16a2b409d0fc2cc1d71`。
最初工作区 `db61c29cf1bd8efe3f977d9461c3c1880c0868b3` 的下列相关文件与其一致；新需求基线
`d1068625d81d380c464355ad139c028600a0dbc9` 仅增加独立 IM 修复，不改变本次读取链路。

- `agent_bridge/src/agent_bridge/routes/meta.py:289`：详情 query、revision 变化复读、然后分页。
- `agent/src/agent/sessions/store.py:672`、`:740`、`:895`：原始读取、解码后过滤及缓存发布。
- `agent/src/agent/sessions/query.py:123`、`:156`、`:254`：完整用户轮分页、投影构建及有界内存缓存。
- `agent/src/agent/sessions/rollout_encoding.py:99`：rollout 引用校验和恢复，不是压缩解压。
- `agent/src/agent/sessions/recovery.py:128`、`:315`：启动扫描及丢失 owner 的 run 终态补写。
- `agent_bridge/src/agent_bridge/app.py:215`：恢复任务独立在线程中执行。
- `frontend/src/stores/conversation.ts:805`：打开会话设置 loading，读取及投影后收口；旧响应按身份丢弃。
- `frontend/src/stores/sessionDetailReads.ts:30`：合并请求，每批至多一次尾随重读。
- `frontend/src/services/api/sessions.ts:98`：active view / turn_limit=30。
- `frontend/src/services/http.ts`：30 秒请求超时；此前还需等待本地服务就绪。
- `agent_bridge/src/agent_bridge/routes/runs.py:27` 与 `frontend/src/services/sessionUsage.ts`：usage SSE。
- `frontend/src/pages/chat/components/HistorySidebar.tsx:420`：restore-ready 属于窗口级首次恢复观察，不携带逻辑会话身份。

行号用于导航；commit 与文件路径是历史内容身份，后续行号漂移不改变历史来源。

### 执行恢复锚点

E-008 查阅版本为 `d1068625d81d380c464355ad139c028600a0dbc9`，这些是源码证据，不把用户
描述的冷/热差值直接归因于它们，也不把旧实验当成本次新测量。

- `agent/src/agent/sessions/manager.py:376`：热路径先复用 `_runtime_states` 并验证 writer lease；否则进入 `load_execution`。
- `agent/src/agent/sessions/execution_loading.py:25`：从头迭代事件，恢复 rollout 后仍耗尽输入以验证完整文件。
- `agent/src/agent/sessions/rollout.py:487`：依次校验消息摘要、提交关系、引用、历史摘要、上下文及 epoch。
- `agent/src/agent/sessions/rollout_items.py:13`：后续 commit 可引用更早 item；临时 replay 索引不能直接当作独立快照。
- `agent/src/agent/sessions/manager.py:767`：取得写者所有权后再次核对来源、修复工具事务，再比较已验证模型状态与业务事实。
- `agent/src/agent/sessions/model_context.py:134` 与 `agent/src/agent/conversation.py`：每次发送按当前时间、权限、入口、能力等产生必要差异，缓存不能绕过这些当前事实。

## 既有交付边界

- [122 progress](../122-session-loading-and-runtime-reuse/progress.md)：已交付分页和暖执行复用；明确首次
  query miss 仍全扫，冷只读投影与启动扫描需进一步方案，阶段合入不代表全部性能 AC 通过。
- [145 progress](../145-session-memory-and-storage-governance/progress.md)：内存改善和新存储编码已阶段
  合入；保留冷恢复回退、旧大历史维护及未验证项。原始样本未自动替换。
- [146 requirement](../146-memory-recall-and-send-latency-governance/requirement.md)：召回/发送轻量投影
  的已通过结论不能外推为聊天 business query 首屏已解决。
- [149 requirement](../149-desktop-startup-performance/requirement.md)：目标收窄为分段计时和诊断导出，
  明确不承诺提速。本次慢启动能分段归因，受益于该观测能力。

## 固定样本正式存储对照

桌面和项目检查结束后，五组串行执行；每组使用新的隔离目录，重复三次。每个重复先在新进程
执行首次读取及同 Store 重读，再保留派生文件、重建进程执行相同两次读取。源 SHA 与候选
源码清单逐次一致，原件未改。下表单位为秒，使用三次 first_call 的中位数：

| 路径 | 无派生文件首次 | 新进程再次读取 |
| --- | ---: | ---: |
| 基线完整业务 query | 8.052 | 8.257 |
| 候选完整业务 query | 8.878 | 0.961 |
| 候选有界首屏 history | 7.806 | 0.03647 |
| 基线执行恢复 | 18.126 | 16.426 |
| 候选执行恢复 | 13.699 | 2.244 |

基线没有这些派生缓存，“新进程再次读取”仍是完整恢复；不能把 Store 重读当作驻留 manager
的热复用。history 与 legacy query 的页面语义不同，261,654 B 是候选固定首屏的紧凑正文，
不含 bridge 增补字段；真实 HTTP 完整页上限另由 E-014 的 261,786 B 验证。文件复制与 hash
会预热 OS 文件缓存，所以这里的“无派生文件”不等于物理磁盘冷启动。

资源代价同样保留：业务索引 71,569,408 B，执行快照 56,806,784 B；分别由独立探针产生，
两者合计约 122.4 MiB，尚不含召回索引。执行快照首次维护中位 1.214 s，范围 1.155–2.384 s。
候选新进程命中时峰值 private memory 约 734–735 MiB，高于基线约 334 MiB；命中返回后约
307 MiB，基线返回后约 332 MiB。首次建立快照峰值约 826.5 MiB、返回后约 354 MiB。峰值和
返回后的占用不能混为一谈；也不能用返回后下降掩盖峰值成本。本地完整发送和终态维护另行
测量，存储层数字不含 writer lease 二次来源校验、动态上下文、UI/HTTP 或真实供应商。

## 本地发送准备与热路径成本

E-016 使用真实 SessionManager/Conversation 与历史召回，fake provider 不发网络请求；默认
人格、naive 上下文、无 active Skill/tools/attachments/结构化记忆库。动态事实的完整覆盖由
自动化回归补充，不能把此测量当作真实供应商 TTFT。每组只执行一次 A,A，下面比较第二轮，
单位为秒。reload 表示同一解释器内重建 runtime/manager/store，真正独立进程由 E-015 验证。

| 条件 | open/start | 发送准备至 fake | 合计至 fake | 合计至终态 | 已含的快照维护 |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline 重建运行态 | 12.486 | 1.758 | 14.244 | 14.417 | — |
| candidate 持久命中 | 3.204 | 1.178 | 4.382 | 5.492 | 0.905 |
| baseline 驻留复用 | 0.029 | 1.422 | 1.451 | 1.624 | — |
| candidate 驻留复用 | 0.029 | 1.464 | 1.493 | 2.843 | 1.138 |

持久命中的源 SHA 校验为 0.853 s，取得 writer lease 后再次校验为 0.887 s，均包含在
open/start 内。候选首次建立时合计至 fake 为 22.066 s，基线首次为 19.844 s，首建没有免费
提速。维护主要为状态转换和 JSON 编码，锁等待不到 1 ms，写入约 55–64 ms；终态数字已含
维护，不能再把维护重复相加。第二轮自然继承第一轮在隔离 journal 中的追加，验证增长后的
冷命中仍能完成续接；未在真实用户 journal 上写入。

发现候选每轮维护成本后，补跑同 harness 的 baseline warm 控制组，所有四组均保留。热发送
准备基本相近，但候选热回合收尾增加约 1.219 s，其中维护占 1.138 s。这是本期的明确代价，
不能用冷恢复收益掩盖。未做多次序列重复，差值不用于统计稳定性或其它样本的承诺。

内存采样是阶段瞬时 private 与进程累计高水位，不是可复位的阶段峰值。reload 两轮的累计
峰值 baseline 580.3 MiB、candidate 1064.1 MiB；第二轮终态为 577.2 / 630.0 MiB。全部关闭
并释放探针引用、GC 后为 558.9 / 605.8 MiB。warm 组第二轮终态 457.5 / 463.3 MiB，累计峰值
461.9 / 948.6 MiB，候选高峰主要在首轮建立。大部分峰值为暂态，但不能据两轮断言没有泄漏，
也不能把释放后约 47 MiB 差值全部归为暂态。源码支持整份 JSON 编解码与事件重建会产生
重叠临时对象；未做语句级内存归因，本期没有为降峰值放宽快照完整性或接受边界。

## 当前验证边界

E-014 来自真实 Tauri chat WebView，已用主进程和 bridge 启动时间确认源码新鲜度。最终 15 项
中 13 项通过，短/长消息两项复制因剪贴板读取权限仍为 prompt 而跳过，未写入系统剪贴板。
工具正文断言检查首尾 marker、逐层裁剪与实际命中，不能只凭请求成功或 DOM 有文字判定通过。
搜索命中的目标恰好在最新页，因此这里只证明定位与高亮；跨页 around 由接口/状态测试覆盖，
没有真实桌面端到端证据。引用只验证草稿选区与已加载来源，不含实际发送。

最终 report 的 URL 观测曾把 5 个 Vite history 模块请求混入历史页统计；按真实 bridge request ID
对账后为 8 个历史页，最大响应仍为 261,786 B，均未超界。原报告保持原字节，私有目录保留原
脚本、修正脚本与 correction.json；归档 JSON 标记 post-hoc correction。17 项离线 URL 分类
断言通过。修正版未重跑桌面，UI 步骤、时长、截图和复制权限缺口均不受此统计修正影响。

此前桌面 pilot 的失败来自验收脚本未展开第二层工具详情、滚轮命中浮动按钮/输入区、嵌套
滚动裁剪和 hydrate 后一帧时序。保留原失败报告，最终两层滚动及首尾命中通过，未因此改动
产品。真实截图仅保留在本机私有目录，不提交包含真实历史正文的 PNG。

## 未决问题

| 问题 | 现有缺口 | 下一项有区分力的证据 | 阻塞情况 |
| --- | --- | --- | --- |
| 原安装态那次接口返回后的最终呈现 | 用户已切走，缺少当时客户端 trace | 候选已有 E-014 关联链路，但不倒推历史缺失观测 | 保留未知，不阻塞候选验证 |
| 资源与时延取舍 | E-015/E-016 已量化：跨进程读取明显减少，首次建立、瞬时内存和热回合收尾增加 | 用户在结果披露后要求合入；未来若需进一步降峰值或收尾成本，可沿用本基线比较 | 已接受本轮交付，不隐藏回退 |
| 完整复制的真实桌面行为 | WebView clipboard-read 权限为 prompt；为保全原剪贴板未点击 | 自动化已覆盖完整取齐后一次写入和失败不写；用户手验 | 明确体验验收缺口 |
| 跨页 around 与未加载来源的桌面导航 | 本次搜索目标在最新页、引用目标已加载 | 协议/状态自动化已有；当前桌面结果只覆盖已加载目标 | 不外推端到端结论 |
| 启动与持续大历史竞争、多个 GB 增长斜率 | 固定 GB 与合成多会话不等于长期多 GB 压力 | 确定性构建期间追加回归已过；另由启动任务在整合后观察完整启动 | 不自动接管启动编排或扩大安装态操作 |
| 安装态 | 本次运行的是新鲜开发源码，未生成安装包 | 合入和推送不自动更新用户安装版；将来打包按独立流程验证 | 不纳入本次安装态结论 |

## 决策影响

| 决定 | 依赖证据 | 状态 | 重新打开条件 |
| --- | --- | --- | --- |
| 以旧大历史的打开链路独立立项，承接 122/145 剩余项 | E-001～E-005 | 用户确认 | 发现范围涉及新的项目级存储或恢复协议决策 |
| 安装校验与启动编排由另一项工作承接 | E-007 | 本需求已确认的边界 | 用户调整分工 |
| 本期不新增取消加载入口，只保障切换与失败重试 | E-007 | 用户明确确认 | 现有交互无法满足已确认目标时重新讨论 |
| 不设固定秒数目标，以行为治理和可比证据验收 | E-001、E-005、E-007 | 用户确认 | 后续用户明确提出数值门槛 |
| 157 扩为历史展示与执行冷恢复两条交付线 | E-008～E-011 | 用户已确认范围及方案并授权实现 | 新证据表明需要改变权威存储、损坏检测或运行时语义 |

## Artifact 映射

| 证据 ID | 原始位置/查询 | 归档位置 | 完整性 | 敏感性与访问说明 |
| --- | --- | --- | --- | --- |
| E-001 / E-004 / E-006 | 本机 `agent.paths.log_dir()` 下 agent_bridge.log / tauri.log.runtime；安装 active receipt / build identity | artifacts/20261008-loading-observation.json | MANIFEST.sha256 校验派生摘要；原日志未整体归档，会轮转 | 原始日志仅本机可访问；仓库摘要不含会话正文、逻辑 session ID 或本机绝对安装路径 |
| E-002 | 本机 `agent.paths.sessions_dir()` 的目标 JSONL；只读统计首部 type、UTF-8 字节数和行数 | 同上 | 当时未计算原文件 hash；仅有 size / mtime 前后不变；不冒充冻结 fixture | 不归档完整用户历史；后续固定副本需单独记录身份与私有位置 |
| E-003 / E-005 | Git 源码和已有需求文档 | Git + 本索引 | 精确 commit；文档为查阅时现状 | 无原始会话内容 |
| E-007 | 本聊天用户确认和明确引用的另一聊天 | requirement.md / 本索引 | 会话记录；无独立导出 | 仅归档与范围有关的决定，不复制完整聊天 |
| E-008～E-010 | 本仓库精确源码、146 已归档实验及本会话范围确认 | Git / 原 146 索引 / requirement.md | 原实验的输入与 hash 沿用 146，不改写其 provenance；本次没有执行新实验 | 不复制私有会话或模型上下文 |
| E-012 / E-015 | 本机私有 `agent-friend-dev/157-evidence/final-storage-*.json`；原源仅只读，临时副本已清理 | artifacts/20261009-storage-comparison.json | 原报告 SHA、五组同一 fixture/harness、两种源码逐文件清单、逐次计数与内存 | 无消息正文；最终固定桌面副本仍在私有 fixture 目录，不能当公开数据集 |
| E-014 | 同私有根 `desktop-final-20261009-002617/`、隔离 runtime logs | artifacts/20261009-desktop-verification.json | 原 report、截图、原/修正 harness 与 correction SHA；导航及服务端字段脱敏提取 | PNG 和原日志不进仓库；仓库只存计数、随机请求标识、摘要与阶段时间 |
| E-016 | 同私有根 `final-offline-*.json`，各组临时会话副本已清理 | artifacts/20261009-local-send-comparison.json | 四组原报告 SHA、统一 schema 3 harness、与 E-015 精确源码树一致 | 不含 prompt/模型输出/会话正文；fake 只是本地边界，不能外推真实模型质量与速度 |

2026-10-08 的诊断摘要是当时工具输出的派生记录，不是事发时冻结的原始日志，不能追认为
拥有那次完整前端 trace 或原始 hash。E-012～E-016 是随后独立取得的固定输入和候选验证，
拥有自己的来源与边界，不改写早期证据身份。较早 pilot 保留在私有根且不并入正式统计；
其集合 digest 不稳定、默认带空格 JSON 字节计数及并行负载局限均在 progress 说明。

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
| --- | --- | --- |
| 2026-10-08 | 用户确认、安装身份、日志、源码、既有需求与范围 | 首次建立；区分后端 37.5 秒、SSE 寿命、其他会话 restore-ready；纠正一次 HTTP probe 的客户端字节计数，采用服务端实发 7,184,444 bytes；未开始产品实现 |
| 2026-10-08 | 用户确认后的范围扩展与执行冷恢复源码 | 新增 E-008～E-010 及 R-8/R-9 的证据关系；历史展示 37.5 秒不充作发送前恢复基线；技术方案和产品实现仍未完成 |
| 2026-10-08 | 用户确认方案并授权开始实现 | 新增 E-011，进入并行实施；旧条目中的“方案待确认”为历史阶段记录，当前状态以本索引顶部及 design/progress 为准 |
| 2026-10-09 | 实现、全量门禁、隔离桌面与正式冷热对照 | 新增 E-012～E-016；归档原报告 SHA 和源码清单，区分存储/本地发送/桌面/安装态；保留首次建立、内存峰值、热收尾回退及手验缺口；runtime 已停止，桌面设置已恢复，未提交/合入/打包 |
| 2026-10-09 | 用户要求合入主干并推送 | 将本轮已披露边界视为用户接受的交付范围，progress 改为 COMPLETED；原始验证 JSON 中的“待验收/未提交”保留为采集当时的历史状态，不改写原始 artifact |
