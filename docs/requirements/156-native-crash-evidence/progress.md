# 156 · 实施进度

## 总体状态

COMPLETED

2026-10-09：需求实现与用户验收完成，用户在获知物理交互未覆盖项后明确要求合入主干、推送并产本地测试包，版本确认使用 0.4.1。最新主干已整合；后端、前端、Rust 门禁全部通过；Guide r86 已发布，兼容范围为 [0.4.1, 0.5.0)。0.4.1 新候选 `20261008T174219Z-e8ca6917782b-f89d4fc3` 的安装矩阵 14/14 通过，主干合并树的精确候选 verify 退出码 0 且 Guide 临时状态完整恢复。既有 17 场景、1800.455 秒桌面压力和符号复核证据继续保留；首轮旧候选 readiness 超时的原因及物理交互覆盖局限没有被抹去。

## 任务列表

- [x] 确认事故链、匹配符号并保全本机私有证据。
- [x] 创建并推送需求分支，确认需求和设计。
- [x] Windows Tauri 主线程所有权补丁、默认/tracing 单测及 debug/release 独立运行时回归；长时桌面另列。
- [x] 独立原生留证、退出关联、保留与错误状态。
- [x] 发布符号归档、身份校验与离线定位实现；新候选标准构建与精确符号归档完成。
- [x] 诊断 producer/consumer、UI opt-in 与兼容验证。
- [x] 受控原生异常、符号与清理验证。
- [x] 隔离真实 WebView、光标采样、并发原生查询及窗口重载的 30 分钟压力，受控窗口销毁与退出记录核验。
- [ ] 物理鼠标穿透、跨屏拖动与托盘退出手验（未执行；按已确认方案记录覆盖局限，用户知情接受当前交付）。
- [x] 项目 backend/frontend/rust 全量分阶段检查及 AC-7 补修后的受影响复验。
- [x] 新候选构建工作区清理后的符号复核与受影响打包矩阵；未删除共享 Cargo 缓存或执行广泛 GC。
- [x] 用户验收：2026-10-09 明确要求合入主干、推送并产本地测试包；接受已报告的验证边界。

## AC 验证

勾选表示已取得对应机器证据，不替代最终用户验收；证据边界见 [evidence-index.md](./evidence-index.md)。

- [x] AC-1：事故直接机制与精确构建绑定，所有权补丁针对同一 Clone/Drop 边界；首次计数损坏时刻仍未还原。
- [x] AC-2：机制回归、debug/release 独立 runtime 与精确候选 1800.455 秒桌面压力通过（104630 次光标事件、99 次重载、6964 次原生查询、59 次穿透 API 往返）；两屏 175%/125% 拓扑读取一致。物理点击穿透、混合 DPI 跨屏拖动与托盘退出仍未验证，用户在获知此边界后确认交付；不把未覆盖项写为通过。
- [x] AC-3：最终 17 场景验证 panic/native 证据留存；匹配 EXE/PDB 分别还原预设故障行，debug 完整源码快照缺口如实保留。
- [x] AC-4：正常退出、caught panic、exit-only、双死后 unknown 及 service-first/shell-first 关联通过，未用退出码冒充异常栈。
- [x] AC-5：真实候选构建工作区移除后，私有归档 EXE/PDB 与 release binding 校验成功；持久私有夹具副本仍可定位 UD2 到 `mod.rs:161`。结合已通过的 `test_symbol_archive_survives_build_cache_removal` 单测取得机器证据；共享 Cargo 缓存未删、未执行广泛 GC，debug 源码快照与部分 source-line gap 保留。
- [x] AC-6：真实 producer→ZIP→consumer、默认/opt-in、formal/local-test 身份、旧包及 12 组 AC-7 状态兼容 probe 通过；consumer 契约 62 tests 通过。
- [x] AC-7：不可写、缺失/损坏、保留/清理、worker 故障及交接整体超时验证通过；watcher-delay 的即时/持久降级、第二次免等待及迟到转储拒绝已覆盖。
- [x] AC-8：隔离异常、源码分阶段门禁、旧候选长时桌面与清理均通过，用户已确认交付。整合最新主干及 0.4.1 后，源码三段门禁、新候选 14/14 安装矩阵及主干合并树 verify 均通过；Guide 临时输入恢复完整。交互安装语言选择器未执行，不声明通过。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-10-08 | 需求、证据、设计落盘；分支 `feature/156-native-crash-evidence` | 文档 UTF-8、链接和六份本机归档 hash 已核对；尚未运行产品验证 |
| 2026-10-08 | Windows owner/token 补丁、独立回归入口 | 默认及 tracing 各 6 个所有权测试、5 个 debug runtime 场景通过；外部 target `agent-friend/build/runtime-regression-156` |
| 2026-10-08 | 发布符号事务与离线栈工具 | 首轮 package/symbol 单测 77 passed；真实事故展开 8 帧。后续增加源码树身份比对与 legacy 缺口检查，待复验 |
| 2026-10-08 | producer/consumer、独立 opt-in UI | consumer 59 tests 通过；frontend lint 与 1455 tests 通过，生产构建在执行；Rust exporter→ZIP→consumer 待统一执行 |
| 2026-10-08 | package-baseline 只读解析 | Guide r83 为已发布 immutable release，commit `56642a193855816dd4c326429b70ae73aef9194f`；verify 通过，snapshot `202cfde330c7dd48526da25979c0b510bdf0afe34e1f1088415c119fdf61185f`。公开知识需补原生留证、转储 opt-in 与排查边界，影响 data-and-privacy/troubleshooting；本模式未编辑公开库 |
| 2026-10-08 | 安装器分级与桌面验证路径 | `installer-contract`: packageRequired=true、full-matrix；源码 0.3.0 dirty，本机最新正式制品 0.4.0。生产 AF 正在运行，dev 身份门禁禁止并存；改用受治理 installer-smoke profile 验证，不停止生产实例 |
| 2026-10-08 | 16 场景真实 Windows 崩溃/故障夹具通过 | 正常退出、caught/uncaught panic、UD2、AV、主线程栈溢出、fastfail、强杀、worker 超时/失败、watcher 不可用/早退、存储不可写、双死后重启、service-first/shell-first。报告及 86 份夹具证据已复制到 research 私有 `native-smoke-16/` 并记录 hash |
| 2026-10-08 | 新转储精确离线定位 | UD2 展开 13 帧，`fixture_main → windows/mod.rs:139`；panic 展开 22 帧，预设 panic 位于 `windows/mod.rs:135`。精确 EXE/PDB 已独立归档；debug 夹具未记录构建时全树快照，未伪造该字段；缺少部分库源码行如实保留 gap |
| 2026-10-08 | 原生与诊断定向 Rust 测试 | 16/16 通过，包含实际磁盘 producer→ZIP→Python consumer、formal/local-test 构建身份、partial、扫描上限、退出原因保留及中断恢复 |
| 2026-10-08 | 全量与后续验证 | frontend 全量（178 files/1455 tests）、生产构建通过；package/symbols 79 tests 及后续 13 个定向测试通过；项目 adapter sync/doctor 与两项 Skill validator 通过；backend/Rust 完整门禁和隔离 candidate 正在执行 |
| 2026-10-08 | backend 完整门禁通过 | lint 946 files、mypy 796 source files、pytest 3942 passed / 5 skipped / 8 warnings；启用 no_external_http，未请求真实模型；阶段耗时 725.4s。第三方 atexit/coroutine 非阻断警告未混入本次修复 |
| 2026-10-08 | Rust 完整门禁通过 | `scripts/check/run.ps1 --stage rust`：fmt、全部单测、release 编译检查通过；217.9s。新原生/诊断测试在完整 gate 中再次通过，仅保留既有 dead-code warnings |
| 2026-10-08 | release 底层生命周期回归通过 | `tauri-runtime-regression/run.ps1 stress --release`：5 个独立 Windows runtime 场景全部 PASS，含 8 线程/800000 次 clone/drop、关闭中请求与退出后的后台句柄 |
| 2026-10-08 | 冻结 runtime doctor/capability smoke 通过 | 标准 `installer-smoke-build` 的新候选 `20261008T141926Z-db61c29cf1bd-821a8334` 已完成模块、资源、工具、会话、沙箱恢复及执行环境自检；最终 installer 构建及矩阵未完成 |
| 2026-10-08 | AC-7 超时边界补修 | 当前代码确认 handler 8 秒超时后没有失败投影，且迟到采集可能继续使用已复用的 panic 栈。补独立失败标记、停止后续捕获、degraded/partial 投影及迟到转储拒绝；新增 watcher-delay 夹具。此前候选 `20261008T141926Z-db61c29cf1bd-821a8334` 停止进入验收；构建结束且 Guide baseline 已恢复，后续必须新构建，不复用旧矩阵结论 |
| 2026-10-08 | AC-7 补修后全部复验通过 | 明确将共享/worker 局部 CONTEXT 对齐到 16 字节，避免结构字段变化导致捕获自身 AV。17/17 原生场景通过；watcher-delay 首次等待 8000ms、第二次 0ms、sequence 保持 1，即时及持久降级、迟到转储拒绝均成立；全量 Rust 331 passed/1 既有 ignored、release check 通过。consumer 12 组合兼容 probe、62 tests 与 Skill/adapter 检查通过；源码于 22:59:30 冻结 |
| 2026-10-08 | 最终原生夹具离线定位与归档 | 私有 `native-smoke-17/` 保存报告及 94 份带 hash 证据；匹配 EXE/PDB 在 `fixture-symbol-validation/.../native-fixture-g40dswlg/`。UD2 13 帧定位当前 `windows/mod.rs:161`，panic 22 帧定位 `:157`；debug 完整源码快照缺口继续明确标注。三份新 shell 入口已保存 Git 可执行位 |
| 2026-10-08 | 证据与机器 AC 最终对账 | 旧 16 场景保留为历史，当前以 E-013～E-015/17 场景及最终 Rust 331 passed/1 ignored 为准；确认 AC-2/5/8 仍未完成。新候选 `20261008T150046Z-db61c29cf1bd-e0f20e5a` 在构建中，未提前写入发布、矩阵或桌面压力通过结论 |
| 2026-10-08 | 静态证据持久复制完成 | 权威根改为 `C:\Users\example-user\agent-friend-private\research\156-native-crash-evidence`；211 文件、813984280 字节逐文件 SHA-256 一致，原 MSIX 缓存源未删除。复制 manifest 与排除的进行中矩阵/一次性探针见 E-016 |
| 2026-10-08 | 新候选标准构建与 release 私有入口验证完成 | 构建报告已生成，精确符号 GUID `7e9de118-7ffc-4fd7-9906-291ab575e045` / age 1；release 的 fixture/watch/dump/unknown 四种私有参数均返回 64，未写 fixture。证据位置和 hash 见 E-017/E-018 |
| 2026-10-08 | 安装矩阵环境纠正后进行中 | 首次启动因 MSIX 路径重定向被 preflight 拒绝，未执行安装；普通 WMI 启动探针确认 session 1 及正常路径，原标准矩阵已由普通进程执行，尚无完成结论。AC-2/5/8 保持未完成，冻结输入未改动 |
| 2026-10-09 | 首轮安装矩阵 13/14；同候选复验中 | `same_version_repair` 的 bridge readiness 超时，其余 13 项通过（含运行中升级、迁移、残留进程与占用重试）；服务日志截至初始化与 attachment reconciliation，不能据此断言原生崩溃或安装损坏。首轮报告、日志和构建报告已带 hash 保存在私有 `installer-matrix-attempt-1/`；源码、阈值及候选不变，第二轮标准矩阵在 `installer-matrix-attempt-2/` 执行，未绕过门禁；Guide 临时 baseline 在首轮结束后已恢复 |
| 2026-10-09 | 第二轮标准矩阵及精确候选 verify 通过 | 14/14 场景 passed，matrix/verify 两个退出码均 0，runner 为 completed/ok；标准报告已复制至私有 `installer-matrix-attempt-2/`，4 份报告/runner/日志均有 hash 清单。交互安装语言选择器未执行（本次未改语言）；首轮 30 秒 readiness 超时仍未定位，复验通过不抹去失败证据。Guide builtin 临时 overlay 的 git status 为空 |
| 2026-10-09 | AC-5 清理后复核完成 | 标准 runner 已自动清理候选构建工作区；真实 release 私有 EXE/PDB 与绑定校验成功，共享 Cargo cache 未删、未跑广泛 GC。全部输入来自持久私有副本的 UD2 再符号化得到 13 帧、`mod.rs:161`；debug 完整源码快照及部分源码行 gap 保留。结合 cache-removal 单测，AC-5 机器证据完成 |
| 2026-10-09 | 真实桌面压力已启动 | 首次 pulse 约 0 秒、cursor samples 57，仅证明已启动采样；长时结果仍在产生，AC-2/8 及用户验收保持开放 |
| 2026-10-09 | 30 分钟真实桌面压力与收尾通过 | 精确 candidate e0f20e5a、PID 48664/session `48664-1791477203651`；1800.455s、104630 cursor events、99 reloads、6964 native queries、59 passthrough round trips，页面错误 0；bubble 销毁，主程序 exit 0，已落盘 `tauri-exit`/`orderly-exit` 且 session completed=true/failures=[]。隔离目录/注册项/进程/28800、28900、CDP 64853 均已清理，package 锁释放；证据与局限见 E-023。未调用真实 LLM，未更新正式安装，未提交/合入；等待物理交互和用户验收 |
| 2026-10-09 | 用户验收、主干整合与版本同步 | 用户接受已报告验证边界并要求合入、推送及本地测试包，明确使用 0.4.1。查明已发布 0.4.0 的版本提交只在 release/0.4.0，未回合 main；按用户版本选择经标准 version-set 同步四处来源。实现 208d5334、主干同步 605a883c、版本 e8ca6917；合入 d671365a 无冲突 |
| 2026-10-09 | 0.4.1 源码门禁和公开知识完成 | backend：lint 968 files、mypy 812 files、4018 passed/5 skipped（no_external_http）；frontend：184 files/1474 tests、lint/typecheck/build 通过；Rust：332 passed/1 既有 ignored、fmt/release check 通过。Guide bb329af/r86 已正常提交、推送并确认 immutable，snapshot d2f9b2fd87b16ecb690eca3267725e1ab176df6a2f84d6fa319f3ce69553f486；不持久修改 builtin |
| 2026-10-09 | 0.4.1 安装矩阵与合并树最终校验 | f89d4fc3 标准构建、私有符号归档、14/14 矩阵通过。私有串联脚本曾因混合编码日志中断，在保留原日志后直接由精确 JSON 报告续跑，没有重建或重跑矩阵。442e 的 verify 正文通过，但 Guide finally 目录替换两次 WinError 5；已保全并逐文件恢复本次独占 overlay。9877 的主干待提交合并树与 P 内容一致，标准 verify 退出 0、进入前后 Git 状态一致；故障来源仅定位到 442e 目录替换，未臆断具体持有进程。证据见 E-024 |
