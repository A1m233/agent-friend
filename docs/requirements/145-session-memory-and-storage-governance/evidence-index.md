# 145 · 证据索引

## 范围与当前判断

归属 [需求](requirement.md)。本索引保存 2026-09-29 诊断与 2026-09-30 方案调查的来源、局限和判断；[盘点](inventory.md)记录 AF 的持有边界。[技术方案](design.md)已获用户确认，用户随后授权实现；当前实施与验证状态见 [progress.md](progress.md)。方案选择不等于候选效果已得到验证。

启动恢复任务结果持有完整会话的机制已复现；两个大历史对应运行态也实际驻留。历史中内部恢复记录占主要体积。这些证据支持将重复持有、驻留生命周期与记录增长共同治理，但不能证明全部进程内存均来自这些路径。

官方源码支持将历史读取与执行加载分开、按活动状态管理卸载，以及在完整恢复边界之后读取后缀。其适用前提必须在 AF 中重新证明；既有 AF 提交链不能直接跳到最后一条记录。

## 证据账本

| ID | 主张 | 来源、版本与覆盖 | 置信度 / 状态 |
| --- | --- | --- | --- |
| E-001 | Bridge 是本次大内存的主要进程 | 2026-09-29 Windows 本机两次进程采样、父子关系、命令行与安装身份 | confirmed / active |
| E-002 | 两个大历史的内部恢复数据占主要体积 | 指定两份真实 JSONL 的逐行聚合；未输出正文 | confirmed（该样本）/ active |
| E-003 | 启动恢复结果在 lifespan 存续期间保留完整会话 | 精确源码函数的隔离复现、安全服务替身、真实单会话只读加载、弱引用与进程内存 | confirmed（持有机制）/ active |
| E-004 | 两个会话存在驻留执行状态；一个已无运行任务 | 当前实例 runtime-snapshot；manager/recorder 引用链 | confirmed（采样时）/ active |
| E-005 | 与上述机制相关的安装模块等价于检查源码 | 安装包提取模块字节码与基线源码编译结果比对 | confirmed（已比对模块）/ active |
| E-006 | Codex 区分只读存储与执行加载，并有活动保护的卸载机制 | 2026-09-30 官方 app-server 文档及固定源码、测试 | confirmed（公开 app-server）/ active |
| E-007 | Codex 只在可独立恢复的上下文边界停止逆向读取 | 固定版本 ModelContextScan、LocalThreadStore、相关测试 | confirmed（指定路径）/ active |
| E-008 | 冷历史压缩需要独立维护互斥、写者保护与失败处理 | 固定版本 rollout compression/maintenance 实现 | confirmed（指定路径）/ active |
| E-009 | Claude CLI 区分持久 transcript、恢复语义与模型上下文压缩 | 2026-09-30 官方 sessions/how-it-works 文档 | confirmed（文档行为）；进程回收策略 unconfirmed / active |
| E-010 | Python 分配追踪应与进程指标及对象生命周期证据分开使用 | Python 3.12 官方 tracemalloc/asyncio 文档 | confirmed（工具能力）/ active |
| E-011 | 固定 A/B 输入中完整恢复约 2.1 GiB，而当前上下文仅 37/39 项；只读投影约 287/257 MiB | 2026-09-30 冻结前缀、SHA-256、12 个独立子进程；[详细测量](investigation-20260930.md) | confirmed（指定源码、样本、路径）/ active |
| E-012 | epoch envelope 与上下文存在大量完全相同的重复值 | 同一冻结样本逐行字段哈希聚合；[统计](evidence/20260930-aggregate.json) | confirmed（重复量），非候选节省率 / active |
| E-013 | 历史前缀恢复依赖旧 rollout；终态 callback 已清理；记忆队列正文不在内存 Queue | 2026-09-30 基线源码补充核对；[边界与反证](investigation-20260930.md) | confirmed（代码路径）/ active |
| E-014 | LangGraph Postgres checkpoint 分离版本化 blob 与 pending writes | 官方源码 `07b33185eab893be2ed031eedae52f09314bf77c`；[来源与适用性](investigation-20260930.md) | confirmed（指定实现），不证明 AF 候选 / active |
| E-015 | 精简执行恢复显著降低固定样本私有内存，冷恢复耗时回退 | [候选原始指标与统计](evidence/20260930-candidate-metrics.json)、[测量源码清单](evidence/20260930-measurement-source-manifest.json)；A/B 各 3 个独立 Python 3.12.14 子进程 | confirmed（隔离源码路径）；速度验收 pending |
| E-016 | 固定 A/B 的完整恢复结果与基线逐字段一致 | 同一候选指标中的 replay_equivalence：固定旧 rollout.py 与新版结果 dataclass 全字段 JSON SHA-256 比对，均 exact | confirmed（两份旧格式输入，decoder 共用） |
| E-017 | 显式维护保留逻辑历史，A/B 体积分别减少约 69% / 71% | 同一指标中的 maintenance_preview、growth；逐提交间隔、类型和累计字节；只读预览，未替换原件 | confirmed（指定输入），非全应用内存结论 |
| E-018 | 启动扫描结束后保留 Task/汇总也不保留 Session | [生命周期隔离测量](evidence/20260930-startup-summary.json)：两个弱引用均释放；业务动作替身；另有真实 reconciler 单测 | confirmed（所有权机制），非完整 Bridge 启动验收 |
| E-019 | 活跃豁免、闲置重建、版本引用和失败发布有确定性回归覆盖 | 测试映射、项目检查与剩余验收见 [progress.md](progress.md) | 状态随实际检查记录；不替代用户验收 |

## 历史诊断锚点：E-001～E-005

- 时间：2026-09-29，北京时间约 03:00～03:10；历史记录来自当前聊天的工具输出，不是 09-30 的重新测量。
- 安装身份：产品 0.3.0、production profile、ordinary/local-test；build `20260927T175435Z-f22fbb6d4df7-33f95989`，声明 source commit `f22fbb6d4df7db3f600a7bb5c35d73e05419abf2`、dirty=true，source tree SHA-256 `94f5262215ff03dabcc10cd1ff549299268803d39f6a59322ad6bd11783ee129`。
- 模块比对：`agent.sessions.store/manager/session/rollout/model_history/recovery`、`agent_bridge.app`。提取 PyInstaller 内嵌 PYZ 中的 Python 3.12 code object，与基线源码编译结果比较字节码、常量、变量/闭包、参数和异常表，忽略源码文件名和行号元信息，结果一致。不能据此宣称整个 dirty 安装树与 clean commit 相同。
- Bridge PID 26660，命令为 runtime 的 `bridge --config-mode desktop`；约 6.83 GiB 工作集、9.36 GiB 私有提交内存。桌面主进程约 63 MiB，语音后端约 27 MiB。不同指标不可相加为物理内存，子进程工作集之和也不是去重后的总占用。
- 只读 runtime-snapshot 显示 A、B 均 resident=true，B 无 running run。A 当时仍有工具执行，未停止或重启它。

| 样本 | 文件字节数 | rollout_commit | model_transcript_item | rollout_request_prepared | 逐条 JSON 对象大小合计 |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 1,061,402,707 | 677.00 MiB / 2,640 条 | 194.76 MiB / 29,036 条 | 100.75 MiB / 755 条 | 1,736.3 MiB |
| B | 933,172,388 | 629.58 MiB / 2,190 条 | 164.04 MiB / 46,589 条 | 76.26 MiB / 676 条 | 1,749.3 MiB |

前三类加上少量 segment 约占 A 的 96%、B 的 98%。大字段包括重复 epoch manifest、active event UUID 列表、消息正文投影与 witness。对象大小使用逐条解析后的递归 `sys.getsizeof`，每条内部去重；这是结构大小测算，不是 live heap 归账，不包含分配器及全部运行时副本。

### 隔离复现

读取本机大历史 A，仅在子进程内执行实际 `store.load` 与 `restore_rollout`；阻止外部网络，不调用 LLM、不执行恢复修补或业务动作。因包初始化引入无关依赖，使用命名空间包直接导入真实存储与恢复模块；不是完整应用启动验收。

| 阶段 | 私有提交 MiB | 工作集 MiB |
| --- | ---: | ---: |
| 模块导入后 | 166.5 | 185.8 |
| 完整加载后 | 2,030.2 | 2,044.8 |
| rollout 恢复后 | 2,040.7 | 2,055.7 |
| 仅从子进程 Session 删除旧 rollout 引用后 | 1,234.8 | 1,247.8 |
| 子进程实验对象全部释放后 | 181.6 | 197.0 |

中间步骤不是安全产品修复：只说明移除一类引用后仍有其它对象/分配器保留，不能直接据此在产品里删除历史事件。

第二个实验执行源码 AST 提取的原始 `_make_lifespan`；其它服务使用无副作用替身，恢复扫描替身只读取 A 并返回与真实报告相同的 `session` 持有形态。任务 done 后通过弱引用检查存活，结果如下：

| 阶段 | 私有提交 MiB | 工作集 MiB | Session 存活 |
| --- | ---: | ---: | --- |
| lifespan 前 | 166.1 | 185.8 | 尚未加载 |
| 恢复任务 done、GC 后 | 2,041.3 | 2,056.8 | 是 |
| lifespan 退出、GC 后 | 184.9 | 201.6 | 否 |

真实扫描返回所有 `ReconciliationReport(session=...)` 的代码路径已另行核对。此实验复现持有机制，未对完整启动扫描进行反事实修改，也未直接读取 live heap 的引用图。

失败/局限：首次导入被缺失 dateutil 阻塞；首次 lifespan 探针因禁止 loopback 导致 asyncio 自管道创建失败。后续仅允许 loopback 后运行成功；失败结果未并入性能数字。真实 A 会话在调查中继续增长，各次读取不是同一冻结 fixture；后续正式比较须先固定私有副本及其哈希。

## 外部实践：E-006～E-010

### Codex 官方协议与实现

官方文档：[app-server](https://developers.openai.com/codex/app-server)。2026-09-30 搜索并全文获取，记录读取与恢复的分离、loaded/list、unsubscribe 及无订阅且无活动后的卸载；文档当时给出 30 分钟宽限。**不把该数值直接变成 AF 的默认值。**

固定公开源码版本：`openai/codex@b1e72963c3b71a9265a551e54beff078384efed9`，通过 GitHub main commit API 解析并下载指定文件；只适用于公开 core/app-server/store，不能推断闭源 Desktop 的内部实现。所读文件的 SHA-256 见 [来源清单](evidence/reference-manifest.json)。

| 问题 | 已核对来源 | 观察与 AF 适用性判断 |
| --- | --- | --- |
| 闲置卸载 | [thread_lifecycle.rs](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/codex-rs/app-server/src/request_processors/thread_lifecycle.rs)、[unsubscribe tests](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/codex-rs/app-server/tests/suite/v2/thread_unsubscribe.rs) | 同时观察订阅和活动状态；测试覆盖重新订阅取消卸载、工具等待时不卸载、完成后重新计时。AF 应验证活动保护与竞态，但桌宠/IM/后台任务不能直接等价为浏览器订阅。 |
| 存储与运行态分离 | [thread-store README](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/codex-rs/thread-store/README.md)、[LiveThread](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/codex-rs/thread-store/src/live_thread.rs) | 历史追加、元信息更新与活跃持久化有不同入口；冷线程元信息不依赖恢复完整运行态。可用于检查 AF 的通知/摘要消费者；不构成迁移 SQLite 的决定。 |
| 恢复所需后缀 | [ModelContextScan](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/codex-rs/rollout/src/model_context.rs)、[LocalThreadStore model context](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/codex-rs/thread-store/src/local/model_context.rs)、[tests](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/codex-rs/thread-store/src/local/model_context_tests.rs) | 分页历史逆向扫描遇到具备 replacement history 和 window number 的边界才可停止；信息不完整及 legacy 路径保留全量恢复。AF 必须先定义并验证可独立信任的检查点，不能跳过当前提交链。 |
| 写入背压与读取反例 | [recorder.rs](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/codex-rs/rollout/src/recorder.rs) | 写命令通道容量有界；但通用 load_rollout_items 仍把逐行读到的项目收集为 Vec。逐行读不等于内存有界，队列条目数也不等于字节上限。AF 要分别检查读取和最终持有。 |
| 冷文件维护 | [compression.rs](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/codex-rs/rollout/src/compression.rs)、[maintenance.rs](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/codex-rs/rollout/src/maintenance.rs) | 压缩冷文件有扫描节流、并发约束、写者锁和维护互斥；压缩格式可透明读，追加前物化。只支持把压缩视为独立磁盘治理手段，不能拿压缩比证明解码对象内存下降。 |

### Claude Code 官方行为

[Sessions](https://code.claude.com/docs/en/sessions) 与 [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works)，2026-09-30 阅读；页面为滚动文档，未绑定源码版本。

CLI 会持续保存 transcript，恢复会话与分叉有不同身份语义；未完成工具在恢复时保留未知/中断事实，而不是自动把动作重做。上下文满时减少旧工具输出、进行摘要，文档也说明摘要可能遗漏细节。这支持将持久历史、当前模型输入和执行事实分开讨论。文档不足以证明其堆对象释放、缓存预算或 GB 级 JSONL 读取实现，因此不据此确定 AF 的卸载算法。本轮未使用非官方反编译源码作为设计规范。

### Python 测量边界

[Python 3.12 tracemalloc](https://docs.python.org/3.12/library/tracemalloc.html) 支持分配来源、快照差异及当前/峰值统计，追踪应尽早启动；[asyncio Task](https://docs.python.org/3.12/library/asyncio-task.html) 提供任务结果接口。AF 的验证应把 Python 对象/分配、弱引用存活与 OS 进程指标分开记录。不能用后来启动的追踪器声称覆盖此前全部分配，也不能把 tracing 开销忽略为产品占用。

## 决策影响与未决问题

以下前七项保留方案调查时的判断演进；其后的确认记录说明哪些问题已形成方案，哪些仍缺实测。

- E-003 直接支持优先消除启动任务结果中的完整历史持有；具体返回值与异常处理方案待确认。
- E-004/E-006 要求驻留回收同时考虑执行、审批、命令与重连，不能按 UI 是否可见简单淘汰。
- E-002/E-007 要求同时处理持久记录重复与恢复持有；当前 AF 检查点是否能成为独立校验边界，尚未完成设计。
- E-008 是冷数据维护候选，不是本期已经决定引入压缩格式。
- 尚缺：全部进程内存的对账；冻结 fixture 的可重复分阶段基线；闲置回收预算与冷重建延迟；旧数据兼容处理与损坏/中断测试方案。
- 当前数字不足以直接承诺“全应用低于某固定 MiB”，正式阈值需在设计确认前通过固定样本校准。
- E-011 已补充固定样本隔离基线，收窄上述测量缺口；仍没有全应用候选、idle 淘汰或旧格式迁移效果证据。E-012 支持优先调查变化值引用，E-013 要求先补齐历史前缀访问而非直接删除旧事件。

### 2026-09-30 方案确认

- 采用小型启动报告、流式完整验证、精简当前运行态和按需历史前缀读取；不直接跳过旧提交链。E-003/E-011 支撑优先级，E-007/E-013 限定正确性边界。
- 采用明确使用关系与后端活动共同保护的闲置卸载。E-006 的活动保护、身份核对及测试可借鉴；AF 全局推送不等于逐会话订阅，必须适配自己的加载、桌宠、IM 和后台任务入口。初始闲置池 256 MiB / 8 个 / 10 分钟是本需求选择，不是照搬参考产品参数。
- 用户明确当前打开、加载、对话中的会话不计闲置预算；活动占用仍需优化和单独列账。固定样本恢复增量峰值 512 MiB 是性能目标，不是活跃会话配额。
- 采用版本化变化值引用与可验证身份增量；E-012 提供重复量证据，E-014 提供状态与版本分离的实现参考。不引入 LangGraph/Postgres，也不把版本化等同于内容哈希去重或外部副作用 exactly-once。
- 兼容只保证新版读取、恢复和接续旧数据，不保证旧版使用新格式。显式维护仍需输入版本、互斥、临时结果校验与失败恢复；借鉴 E-008 的维护安全边界，不据此决定引入压缩格式。
- 尚待实现验证：实际内存与增长改善、查询缓存堆记账、回收竞态、维护中断恢复、冷恢复耗时及全进程残差。现有基线与参考证据不能代替这些候选验证。

## Artifact 与对账

- 原始会话、完整 prompt、工具正文及本机 session ID 不入仓库。A/B 只表示本次诊断样本，不是可公开下载的 fixture。
- 历史工具输出保留在本聊天；本文手工整理其统计，未假称存在完整 JSON 运行报告或冻结数据哈希。
- 原始一次性探针位于本机 `%TEMP%/af-memory-diagnosis-20260929.py`，含本机绑定，未复制入仓库；临时文件可能被清理。本文保留方法、实际代码锚点、输出与限制，后续应建立参数化隔离 harness。
- 官方源码使用固定 commit URL 与下载内容 SHA-256，临时下载缓存不作为唯一复查入口；官方文档采用访问日期与 URL，滚动内容变更时须重新核验。
- 2026-09-30：与需求确认范围对账完成。仅需求与脱敏调查材料落盘；未形成已确认 design，未开始实现或修改用户数据。
- 2026-09-30 补充：固定真实样本已归档到本机私有研究目录；新增基线 manifest、原始脱敏指标与重复结构统计，不覆写 09-29 provenance。公开来源增加 LangGraph 固定版本；调查结论与方案建议继续分开。
- 2026-09-30 后续确认：用户同意整体方案并补充活跃会话预算豁免与单向兼容；已同步 requirement 与 CONFIRMED design。先前“未形成 design”描述保留为历史状态；本次无代码、运行实例或用户数据变更。

## 2026-09-30 实施候选：E-015～E-019

固定 A/B 的内容哈希与基线相同；分别使用独立子进程，每个路径 3 次。下面为私有提交内存，不是工作集或整个 AF 的内存。

| 指标 | A | B |
| --- | ---: | ---: |
| 基线恢复后私有内存中位数 | 2,123.97 MiB | 2,061.02 MiB |
| 候选恢复后私有内存中位数 | 297.07 MiB | 277.91 MiB |
| 降幅 | 86.01% | 86.52% |
| 候选峰值减导入基线，三次最大值 | 137.59 MiB | 119.55 MiB |
| 基线冷恢复中位数 | 9.366 s | 9.640 s |
| 候选冷恢复 min / median / max | 9.719 / 10.852 / 11.803 s | 12.837 / 13.170 / 14.448 s |
| 冷恢复中位数变化 | +15.87% | +36.62% |
| 完整恢复结果比对 | exact、全字段哈希一致、37 项模型内容 | exact、全字段哈希一致、39 项模型内容 |
| 只读维护前 / 后 | 1,112,568,824 / 345,564,976 bytes | 933,172,807 / 270,936,641 bytes |
| 相邻 commit 间新增字节中位数，旧 / 新 | 330,274.5 / 47,469 | 331,979 / 32,710.5 |

增长比较把同一批旧事件按原顺序送入实际 RolloutEncoder；旧格式按 Event.to_jsonl 规范化，因此与原文件字节数有少量空白差异。间隔以 rollout_commit 结束，不等于一轮用户对话；单次巨大业务或工具内容仍可能主导增量。完整逐 commit 数列留在私有研究目录，仓库保存各类型统计、四分位位置累计值及区间 min/median/max。维护预览额外验证完整逻辑历史 digest 与有效 commit 一致；只在合成数据上测试发布/失败发布，不替换 A/B 或用户原文件。

冷恢复仍要完整校验原始历史，并为可被后续 commit 引用的旧 transcript 写临时索引。这是新增工作的代码依据，尚无剖析证据把耗时回退全部归因于临时 I/O。OS 缓存、机器其它应用未控制；B 测量时短暂执行了 lint/format，完整测试与构建在 B 完成后才启动。保留较快的中间候选数据，不选择性丢弃较慢的最终三次结果；小样本不能估计 p95。512 MiB 增量峰值目标通过，但冷恢复速度保护线尚待用户评估，不能标为 AC-07 全通过。连续发送禁止重新调用完整 store.load 的机制已由 test_warm_execution_never_loads_history_and_preserves_turns 固定。

启动所有权探针执行真实扫描、业务加载和 summary 构造，仅将可能产生业务副作用的 reconcile_session 替换为 report-only stub；完成后故意保留 asyncio Task 与返回 tuple。A/B 两个 Session 弱引用均死亡，私有内存由导入后的 164.31 MiB 回到 174.29 MiB，峰值 290.26 MiB。此项证明汇总不保留会话，不代表完整启动服务的峰值；实际对账语义由单测覆盖。

源码身份以 base commit 加未提交 overlay 的逐文件 SHA-256 表示。测量入口、store、rollout、execution_loading 与 rollout_items 在最终 A/B 期间未变；之后只修正订阅队列对重复对象的字节记账、补相应测试、格式化 manager，并为新 shell 入口设置 LF/可执行位。最终交付清单另存，不能用后生成的清单冒充早期候选身份。私有源码 ZIP 保留可恢复 overlay；公开证据不含会话正文、工具输出或原始 session ID。

尚未验证：真实安装包替换后的整体内存、mac/linux 文件锁与发布行为、真实桌面多窗口交互及全进程残差。未调用外部 LLM，未构建或安装新包，未重启现有 AF。查询缓存仍采用既有序列化估算上限；旧样本已显示它与进程增量并非 1:1，不能称为实际堆硬上限。

最终检查已完成，结果与源码绑定见 [validation](evidence/20260930-validation.json) 和 [verified source manifest](evidence/20260930-verified-source-manifest.json)。后端 3715 passed / 5 skipped、前端 1340 passed，Rust 阶段通过；用户验收与上述验证缺口仍保留。

### E-020 · 逐次发送延迟补充

用户纠正后按标题定位固定样本 B；安装版热发送到 transport request_started 为 28.218 s，其中自动记忆召回 18.297 s。当前候选仍复现 A/B 查询缓存互相淘汰和重复扫描，因此已有内存结果不能证明发送路径改善。详见 [只读诊断](evidence/20260930-send-latency-diagnosis.json) 与 progress 的缺口记录。
