# 184 · 诊断包生成与日志治理 · 证据索引

## 用途与范围

- 服务 [requirement.md](./requirement.md)，记录已有真实包观测、代码机制、外部实践和用户确认。
- 产品分析基线：`36605682fd49ecb6fc394926f1db08b4fdc3b4af`；历史事故按包内构建身份单独绑定。
- 最近对账：2026-11-28。需求与方案已确认，用户已授权实现；Windows 真实 Tauri 导出、取消、产物核对和 15 项隔离安装矩阵均通过。用户明确要求本轮先不执行 macOS 专项，不以寻找 Mac 作为本轮收尾条件；用户最终验收尚未确认。
- 不归档用户会话、日志正文、附件、截图、原始转储或本机账号路径。

## 当前综合判断

1. 已有样本包含约 1.02 GB、1115 万行后台输出日志；对应源码持续追加该日志，导出逐行脱敏并直接写临时文件，再读回压缩。日志数量及实现机制已确认，作为主要耗时解释得到强支持；由于缺少导出分阶段计时，尚不能分配各阶段耗时或承诺提速倍数。
2. 需求 183 的按需会话读取治理已合入本基线，但没有修改诊断归档、日志暂存和后台输出文件的相关实现，不能用其完成状态证明本问题已修复。
3. 其它值得治理的已知机制包括独立请求串行等待、会话 JSON 整体物化、附件原件未选入时仍做哈希检查、缺少任务进度与贯穿式取消。它们的机制有代码证据，实际性能贡献尚未测量。
4. 最新确认方向为一个标准导出入口、按需独立扩大日志范围、会话范围或检查项目。此前“默认完整”和“默认快速＋完整备选”的讨论均已被后续决定取代，不作为当前实施依据；范围透明、隐私和检查状态真实的要求继续有效。
5. 方案阶段的隔离日志暂存比较支持缓冲写入方向：相同 134,160,072 B 合成输入，三次中位耗时从 6.382 s 降至 0.724 s，输出大小和 FNV64 相同。首次 pilot 与依赖准备/其它 smoke 有 IO 重叠，保留但不作为主比较。此结果不含 ZIP、网络、完整性检查和 UI，也不代表真实包整体提速。
6. 现有最近 10 轮读取专项在约 17 MB 与 169 MB 两个合成会话上均只读 65,587 B，证明已有尾部范围读取机制可复用；仍需补取消、扫描/输出预算和大单记录边界。
7. 实施后的正式 collector 同输入优化 test profile 比较：约 128 MiB 日志的完整暂存从 6.879 s 降至 0.898/0.904 s，输出长度一致，阶段约 7.6 倍。此比较与默认少读历史的收益分开，不能外推整包倍数。
8. Windows Tauri dev 真实链路：约 256 MiB 合成日志，默认任务 0.819 s；主动全量日志加完整 20 轮为 14.959 s。默认仅从该日志读 5,242,881 B；三会话完整历史（约 80 MiB 大记录内容）加 32 MiB 原始附件仍保留近期日志预算，12.601 s 完成。取消点击至终态 46 ms；均为单机合成样本，不是 SLA。详见 E-017。

## 证据账本

| ID | 日期 | 问题或主张 | 类型与锚点 | 直接观察与局限 | 置信度／状态 |
| --- | --- | --- | --- | --- | --- |
| E-001 | 2026-11-23；11-27 核对文件 hash | 真实包的数据规模与覆盖 | runtime-observed；下方“历史样本” | ZIP 有效，CRC 通过；197 entries，解压总量 1,246,036,544 B。单份 runtime 日志 1,018,953,078 B，会话 99,732,317 B。缺少整体结束与分阶段耗时。 | confirmed／active（规模事实） |
| E-002 | 2026-11-23；11-27 复核 | 日志增长与导出成本机制 | code-supported；事故版本 `fe9981a0ec86e2abb4f7e865f12a0cd3262ad942`；[staged_log.rs](../../../frontend/src-tauri/src/diagnostics/staged_log.rs)、[supervisor.rs](../../../frontend/src-tauri/src/runtime_services/supervisor.rs) | 后台输出文件 append，未见此通道轮转；每行脱敏后直接向 File 写正文与换行。约 2230 万次写入调用是按行数推算，不是 syscall 或磁盘物理写入测量。 | confirmed／active（机制）；strongly-supported（耗时解释） |
| E-003 | 2026-11-23 | 会话导出本身的已知开销 | runtime-observed；历史样本 coverage 与 request observation | 147 轮、61,575 events；coverage duration 2901 ms，接口 total 3057.743 ms。仅覆盖会话接口，不含 Rust 后续解析、脱敏、归档，也不证明所有其它采集都很快。 | confirmed／active |
| E-004 | 2026-11-27 | 当前端到端治理缺口 | code-supported；基线 [diagnostics/mod.rs](../../../frontend/src-tauri/src/diagnostics/mod.rs)、[diagnostic_export.py](../../../agent/src/agent/sessions/diagnostic_export.py)、[diagnostics.ts](../../../frontend/src/services/diagnostics.ts)、[反馈面板](../../../frontend/src/pages/settings/components/FeedbackDiagnosticsPanel.tsx) | 4 个基础 HTTP 请求逐个 await，各自总超时 5 s；会话请求仅设置连接超时；会话响应整体 JSON 物化；附件哈希不以 include_originals 为前提；入口无进度/取消协议。未做现场卡死、内存峰值或并发复现。 | confirmed／active（源码机制） |
| E-005 | 2026-11-27 | 已有保护及待补边界 | code-supported；基线 `TemporaryPath::drop`、`write_archive_atomically`、`collect_install_integrity`、`shell_diagnostics/manager.rs` | 已有正常路径临时文件清理、ZIP64、最终 sync/rename、部分失败、Shell 导出超时等保护；当前入口未见磁盘预检或中断残留恢复。并发文件名竞争、崩溃残留属于需验证场景，未宣称现场发生。 | confirmed／active（现有保护）；unconfirmed（具体异常发生） |
| E-006 | 2026-11-27 | 相关历史边界 | code-supported；`a6f7081f0f93382fbf50379a63cfa74a366df68c`；需求 [183 读取清单 R-25](../183-unified-session-reading/read-audit.md) | 历史变更明确取消会话、日志和附件导出体积上限；183 明确保留必要的完整诊断导出。184 改默认采集范围需显式保留完整选择与覆盖语义。 | confirmed／active |
| E-007 | 2026-11-27 | 常见产品如何治理日志与诊断成本 | external-source；下方“外部资料” | 可借鉴轮转、范围预算、缓冲写入、临时文件及不覆盖发布等机制；没有同平台同数据性能比较，不能宣称它们必然比 AF 快。 | confirmed／active（来源行为） |
| E-008 | 2026-11-27 | 首次需求方向与授权 | user-confirmed；当前需求聊天 | 用户曾确认“默认快速、完整备选”及 184 号整体范围；双模式约定后来由 E-013 取代。技术方案和产品实现未获确认。 | confirmed／superseded（模式约定） |
| E-009 | 2026-11-27 | 缓冲是否减少日志暂存开销 | controlled-comparison；[测量数据](./artifacts/design-probes/controlled-results.json)、[源码与夹具锚点](./artifacts/design-probes/metadata.json) | 原生产 stager/redactor 提取到独立 Rust harness，仅临时文件 nonce 用替身；候选仅给暂存写入加 256 KiB 缓冲。同一输入三次交错顺序比较约 8.81 倍；单机、文件缓存已热、不含压缩。 | confirmed／active（本样例阶段差异）；directional（产品整体收益） |
| E-010 | 2026-11-27 | 已有近期会话读取是否随旧历史增长 | reproduction-supported；[已有专项结果](./artifacts/design-probes/session-range-smoke.json) | `scripts/diagnostics-smoke/run.ps1` 通过；旧轮次数从 2048 到 20480，读取量保持 65,587 B。每种大小一次，使用 Python ZIP，不代表桌面端完整流程。 | confirmed／active（样例读取量） |
| E-011 | 2026-11-27 | 默认采集的隐含校验成本与取消边界 | code-supported；基线 `install_contract::discover_active_payload` / `verify_candidate_payload_inner`、`routes/session_reads.py`；[Tokio blocking task 文档](https://github.com/tokio-rs/tokio/blob/master/tokio/src/task/blocking.rs) | 当前安装完整性采集会遍历 manifest 全部文件并计算哈希，不能当作便宜的状态读取；已运行的 spawn_blocking 不会被 abort 停止，必须逐层协作取消。仅机制确认，尚未测安装校验耗时。 | confirmed／active |
| E-012 | 2026-11-27 | 双模式是否是必要产品形态 | external-source；下方“采集入口形态补查” | VMware 明确提供 Light/Full；sos 提供高级参数；JetBrains 和 Signal 的官方常规流程呈现单一入口。说明多种形态都存在，不能由范围预算推导出 AF 必须有双模式；不是行业使用率统计。 | confirmed／active（资料事实） |
| E-013 | 2026-11-27 | 单一入口与独立扩大范围 | user-confirmed；当前需求聊天 | 用户明确要求“就按一个标准导出入口、按需扩大范围来，更新需求，继续完善方案”。替代 E-008 的模式约定；性能、日志生命周期、取消、清理、证据与兼容性范围保持。 | confirmed／active |
| E-014 | 2026-11-27 | 现有范围 UI 与任务订阅机制可复用 | code-supported；基线 [反馈面板](../../../frontend/src/pages/settings/components/FeedbackDiagnosticsPanel.tsx)、[runtimeCredentials store](../../../frontend/src/stores/runtimeCredentials.ts) | 已有最近轮数／全部轮次及原件选择；其它功能已有事件订阅、快照查询与取消入口，可作局部实现参考。诊断本身尚无后台任务状态；订阅与快照竞态仍需专门处理，不能直接复制后宣称可靠。 | confirmed／active（代码机制） |
| E-015 | 2026-11-27 | 归档发布的不可覆盖边界 | external-source；[tempfile NamedTempFile](https://docs.rs/tempfile/3.27.0/tempfile/struct.NamedTempFile.html#method.persist_noclobber)；基线 Cargo.lock | 现有锁文件含 tempfile 3.27.0，但产品未直接依赖。persist_noclobber 保证不覆盖已有目标，不保证所有平台上临时路径移除与最终发布组成一个原子操作；可能遗留同一文件的临时硬链接。采用前须核对依赖兼容性并设计残留清理，不能仅因调用该方法就声称所有异常已覆盖。 | confirmed／active（文档边界）；未采纳依赖 |

## 实施验证账本

| ID | 日期 | 问题或主张 | 证据与边界 | 状态 |
| --- | --- | --- | --- | --- |
| E-016 | 2026-11-28 | 同范围实现性能 | [优化 profile 数据](./artifacts/implementation-optimized-smoke.json)、[debug 数据](./artifacts/implementation-smoke.json)；正式 Rust collector/ZIP writer，历史基线来自固定探针。Python 169,441,221 B 全历史逐记录消费的 tracemalloc 峰值 242,671 B，非 RSS。 | 样例通过 |
| E-017 | 2026-11-28 | 真实桌面范围/取消/内存/临时材料 | [脱敏验收结果及源码 hash](./artifacts/desktop-acceptance.json)。真实 UI 点击，真实 bridge，实际 ZIP CRC/消费者/轮数/日志范围/原件 hash/合成凭据过滤核对。默认、全量、三会话+附件、取消 4 场景通过。RSS 每约 50 ms 采样，主壳峰值约 110–111 MiB；Rust 暂存目录峰值分别约 1.7/78.7/113.7 MiB，不含 Python 投影 spool、下载目录中的 ZIP 中间文件或采样间短峰。子进程 RSS 和含共享页，不等于独占内存。[独立 spool 测量](./artifacts/python-spool-measurement.json) 用真实 Store 和 TemporaryFile 写位置计量：同一 1 MiB 单记录样例，10/20 轮分别暂存 20,977,890 / 41,955,780 B，结束均关闭；这是分层逻辑字节，不是整条流水线的同时磁盘峰值。 | Windows dev 通过；macOS 未验证 |
| E-018 | 2026-11-28 | 生产包装层是否接通新接口 | 首次桌面导出 partial，流返回 session_export_failed；NotifyingSessionStore 缺 diagnostic_stream。路由 fixture 改为同款包装层后先复现失败，再补显式转发；76 项路由/流式/会话测试通过，重启新鲜 runtime 后 E-017 通过。首轮 863 ms 不计成功性能。 | 已修正并复验 |
| E-019 | 2026-11-28 | 检查范围与平台限制 | backend 完整 5060 passed/6 skipped，后续调整以对应专项及最终 lint/mypy 复验；frontend 最终 201 files/1655 tests+build；Rust 完整 366 passed/2 ignored+release check，追加空闲超时专项及 release check 通过。详见 progress；不包含真实模型质量判断。 | 源码门禁通过 |
| E-020 | 2026-11-28 | 包级验证与 Guide 基线 | immutable Guide r129/7bfd59a，兼容 0.4.1 ≤ version < 0.5.0，manifest digest 与 GitHub asset 一致；verify 通过。[包级验证记录](./artifacts/windows-package-verification.json)：同一 candidate 的 15 项 full-matrix 场景全部 passed，标准报告 verifier exit 0，绑定候选 hash、环境和解析 Guide 后的 package input。仅隔离 profile，未执行 production 覆盖升级。 | Windows 机器门禁通过 |
| E-021 | 2026-11-28 | 简化导出主流程 | [界面迭代验收](./artifacts/desktop-layout-acceptance.json)：会话、轮数和生成操作位于同一主区域，高级项默认折叠，问题说明保持直接可见；真实 WebView 中生成按钮在首屏、无横向溢出，键盘折叠与选项保持通过。实际生成所选会话最近 5 轮，ZIP CRC、消费者与覆盖信息核对通过；frontend 201 files/1659 tests、lint/typecheck/guards/build 通过。 | Windows desktop DOM / interaction / product E2E 通过 |
| E-022 | 2026-11-28 | 焦点刷新布局回归 | [修复前后 CDP 证据](./artifacts/desktop-focus-acceptance.json)：焦点刷新共享会话列表时，旧分支把条目替换成加载占位，列表约 210→62→210 px、按钮位移约 148 px。修复后保留缓存；3 次 focus、3 个真实成功响应、117 次采样中条目未卸载、选择保留，列表、输入框、按钮及滚动位置均不变。5 项新增测试先失败后随前端 1664 tests 通过；未自动化原生标题栏拖动。 | 焦点刷新机制通过 CDP 复验 |

## 历史样本与原始材料

Python spool 的一次性计量源码另存 [python-spool-probe.py](./artifacts/python-spool-probe.py)，仅将本机输入/输出路径改为参数；实际首轮数值保留在对应 JSON，不用后续运行覆盖。

- 原始包：`agent-friend-diagnostics-20261123-010413.zip`，由用户在当前聊天提供；保留在用户原始位置，不复制进仓库。
- 文件 SHA-256（2026-11-27 实际计算）：`0f61797059a06f2d88ded43568403cd922a5b4cf17c06e4d481d9e1bdc28f1b0`。
- ZIP 文件长度 71,040,779 B；压缩条目数据合计 70,998,197 B，两者口径不同。
- 包生成时间字段：2026-11-23 01:04:13 +08:00。该字段在导出开始时取值，不是完成时间；传输后文件时间不能替代导出耗时。
- 事故运行身份：Windows 10.0.19045 x86_64，产品 0.4.1，formal / ordinary / stable，build `20261121T181000Z-fe9981a0ec86-9f4286c9`，source `fe9981a0ec86e2abb4f7e865f12a0cd3262ad942`，dirty=false。
- 格式 v1 / schemaRevision 23；所有声明 capability 均被当时解析器识别，无 unsafe/duplicate entry，CRC 校验通过。
- outcome=partial：执行环境采集失败；另有历史退出证据不完整 warning。所选会话已包含；附件原件和原始 crash dump 未选入。不能把“ZIP 有效”解释成“所有来源齐全”。
- 日志流式统计：`runtime-bridge.log` 共 11,149,636 行，扫描中首末可识别时间为 2026-08-13 01:37:28 与 2026-11-23 01:04:21，绝大多数为 HTTP 访问记录。只保留聚合，没有存储正文。
- 检查入口：项目 inspector 读取原包并验证 CRC；随后使用 Python zipfile 读取 application/manifest、entry 大小、会话 coverage 和事件计数，流式统计指定运行日志；用 git show 读取包内 commit 对应源码。完整查询过程保留在当前聊天。
- 本仓库仅归档上述脱敏统计和来源 hash。原包含私密内容，复查须经原始持有者提供；这些统计不是可公开重放的性能 fixture。

## 外部资料

以下为 2026-11-27 实际读取的官方资料；源码 blob 标识固定本次读取内容，链接 main 可能继续变化。这些是设计参考，不是 AF 的速度验收证据。

| 来源 | 可核实做法 | 内容锚点与适用边界 |
| --- | --- | --- |
| [VS Code spdlogLog.ts](https://github.com/microsoft/vscode/blob/main/src/vs/platform/log/node/spdlogLog.ts) | 异步轮转；rotating=true 时 filecount=6，filesize=(30/6) MB。 | blob `762750562229444f364b59272bc26358fdf48d7e`；数量参数不能直接外推为整个应用所有日志的总容量。 |
| [Signal rotatingPinoDest](https://github.com/signalapp/Signal-Desktop/blob/main/ts/util/rotatingPinoDest.node.ts) 与 [日志收集](https://github.com/signalapp/Signal-Desktop/blob/main/ts/logging/main_process_logging.main.ts) | 按天轮转、3 份备份；启动按约三天窗口清理，单文件读取使用 300,000 条环形缓冲。 | blobs `b17383b5c3d481cd002144389694fa16b649993b` / `e268f81017d56e7d7df4dd00151268ee73ec5265`；环形缓冲限制保留内存记录，不等于免除整份文件扫描。 |
| [sos 官方手册](https://github.com/sosreport/sos/blob/main/man/en/sos-report.1) | 普通日志/命令输出默认末尾 25 MiB；journal 独立预算；支持 all-logs、since、并发和超时。 | 官方 main 文档，本次未固定 blob；范围与阈值不能直接作为 AF 默认值。 |
| [Replicated Pod Logs](https://www.troubleshoot.sh/docs/collect/logs/) | 默认 maxLines=10000、maxBytes=5000000，可指定时间窗口。 | Pod 日志 collector，非桌面会话导出契约。 |
| [Rust BufWriter](https://doc.rust-lang.org/std/io/struct.BufWriter.html) | 缓冲并批量处理重复小写入；显式 flush 以处理错误。 | Rust 官方标准库说明；不提供本样本的性能倍数承诺。 |
| [Docker Desktop 诊断](https://docs.docker.com/desktop/troubleshoot-and-support/troubleshoot/) | 官方提示采集可能耗时数分钟。 | 用于校准整体诊断耗时预期，不能替 AF 的低效实现辩护。 |
| [Codex feedback archive](https://github.com/openai/codex/blob/main/codex-rs/feedback/src/archive.rs) | 默认未压缩 200 MiB / 128 附件预算，读取前检查，受拥有的临时文件，sync 后不覆盖发布。 | blob `82a7e22f5935dc1bd2967bd341fcb75d3336537d`；仅此开源入口，不外推闭源 Desktop 全部行为。 |
| [Codex rollout archive](https://github.com/openai/codex/blob/main/codex-rs/feedback/src/rollout_archive.rs) 与 [daemon logs](https://github.com/openai/codex/blob/main/codex-rs/feedback/src/daemon_logs.rs) | rollout 归档使用 BufWriter；两类 updater 日志各只取末尾最多 256 KiB。 | blobs `c68bd9a99777fb2d1459503d7c884d7078309b6e` / `b5e274379fc3af2a0644cd1ce5ed56c4eb0417d9`；只适用于指定材料，不代表所有日志都按同一上限。 |

## 未决问题与下一项证据

| 问题 | 下一项有区分力的证据 | 影响 |
| --- | --- | --- |
| 脱敏、写盘、压缩、校验各占多少时间 | 固定样例走实际导出入口，保留阶段计时和输入/输出量；分别测试日志、会话和附件 | 冻结性能预算前需解决，不能从 ZIP 大小推算完成秒数。 |
| 默认范围怎样兼顾相关故障证据 | 构造“近期繁忙＋更早故障”“长会话近期片段”等样例，核对覆盖与读取量 | 决定默认范围、证据优先级及说明，避免默认包普遍需要二次补采。 |
| 完整导出的峰值内存及临时空间 | 多会话、长记录、大附件分段测量，固定硬件、版本和并发条件 | 决定处理与调度方案，避免仅把开销转移到另一阶段。 |
| 取消、进程中断和文件竞争是否正确 | 隔离目录中注入取消、磁盘耗尽、权限错误、重复请求及进程中断 | 决定生命周期与清理机制；现阶段不是已发生事故清单。 |
| 日志轮转会否干扰持有句柄的后台进程 | Windows / macOS 对应专项，核对输出持续性、文件占用与退出 | 方案需覆盖平台差异，不能用单平台通过替代另一平台证据。 |

## 决策与对账

- E-001～E-006 支持将导出、采集、日志生命周期与异常恢复作为一项完整需求治理。
- E-007 提供候选机制；具体依赖、阈值、接口及并发策略尚未决定。
- E-013 取代 E-008 的快速／完整双模式约定；更新后的 requirement 为当前范围权威。保留历史决定，不将旧方案改写成从未存在。
- 2026-11-27（需求确认时）：核对事故源码身份、当前主干相关文件差异、来源摘要和最终用户选择；当时无产品代码改动、benchmark 或候选实现。
- 2026-11-27（用户要求继续后）：补充 E-009～E-011；需求范围未改，技术方案尚待确认。临时 Rust prototype 只用于机制比较，不是产品实施。`design.md` 及实施阶段 `progress.md` 尚未创建。
- 2026-11-27（入口形态修订）：补充 E-012、E-013；按用户明确授权更新 requirement，恢复为已确认的单一入口需求。默认预算、接口、状态机及实现尚待设计确认；已有测量证据继续有效。

## 方案阶段测量产物

- [artifacts/design-probes/](./artifacts/design-probes/) 保存提取源码、缓冲候选、编译入口、输入生成配方、输入 hash、受控结果、pilot 和已有会话专项的脱敏汇总；[MANIFEST.sha256](./artifacts/design-probes/MANIFEST.sha256) 绑定归档内容。
- 编译：rustc 1.96.0，edition 2021，优化开启；宿主平台见 controlled-results。计时只包围 collect，输出 checksum 在计时后计算。pilot 的进程峰值工作集约 4～4.5 MB，仅为该独立进程采样，不是桌面总内存。
- 夹具约 16 MB 与 134 MB，由固定短行组成，少量合成凭据用于保持脱敏路径；大夹具和编译产物不入库。归档不含用户正文。
- 原始实验在 agent 自建临时目录运行。依赖准备完成后执行主比较，主机未独占；不将该阶段数值外推为 1 GB 真实诊断包的完成时间。
- 下一步仍需确认默认范围、独立扩展选项、资源预算及生命周期方案；后续集成性能应重新经实际诊断入口测量，并与同范围基线对照。

## 采集入口形态补查

以下页面在 2026-11-27 实际读取。官方文档描述的常规流程不是对产品所有版本和隐藏能力的穷尽证明。

- [VMware Aria Operations 支持包](https://knowledge.broadcom.com/external/article?legacyId=2074601)：UI 明确提供 Light（24 小时日志）和 Full（完整日志与图表），证明双模式有实际案例；其面向运维和多节点排查的约束不自动适用于 AF。
- [Red Hat sos](https://access.redhat.com/kb/3592)：默认诊断与 `--all-logs` 等高级参数，扩大范围可能显著增大包。
- [JetBrains PyCharm 问题报告](https://www.jetbrains.com/help/pycharm/reporting-issues.html)：常规“Collect Logs and Diagnostic Data”入口；更详细日志通过按类别的 Debug Log Settings 配置。它扩大的是采集细节，不能等同于已有日志的全量导出。
- [Signal Desktop 调试日志](https://support.signal.org/hc/en-us/articles/360007318591-Debug-Logs-and-Crash-Reports)：常规 View → Debug Log → 查看 → Submit；这套流程没有要求用户先选择快速／完整。
- [Zoom 问题报告](https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0060047)：普通报告可附日志；更详细调查由支持人员指导专门采集。用于说明按调查目的补充材料的形态。
- 设计影响：保留按目的扩大证据的能力，取消把更多日志、完整会话和昂贵检查绑定为一个“大模式”的拟议产品选择。最终决定由 E-013 承载。


## 实现授权与当前验证

用户明确确认单一入口、默认预算与旧日志 7 天保留，并要求写入设计后开始实现。[design.md](./design.md) 已 CONFIRMED，[progress.md](./progress.md) 持续记录工作区候选与检查结果。早期隔离 benchmark 不替代候选验收，历史“尚未授权实现”的记录只描述当时状态。
