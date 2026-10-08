# 156 · 崩溃修复与留证证据索引

## 用途与范围

- 归属：[requirement.md](./requirement.md)。
- 记录本次 Windows 安装版事故的身份绑定、离线分析、代码机制和观测能力缺口。
- 不保存用户会话正文、原始内存、凭据或完整系统报告。原始文件仅在本机私有归档。
- 最近对账：2026-10-09；源码门禁、17 场景原生夹具、新候选构建/符号、第二轮安装矩阵与 30 分钟真实桌面压力均已完成；物理交互及用户验收仍待完成。

## 当前综合判断

本次主程序在 2026-10-08 18:05:17（UTC+8）发生原生异常。精确匹配的转储、二进制、PDB 和源码把终止路径绑定到桌宠光标后台线程的窗口获取，以及 `tauri_runtime_wry::Context::clone` 内的非原子引用计数回绕保护。此保护通过 `UD2` 终止，不经过 Rust panic hook。

事故版本内，跨线程可用的 Tauri Context 会复制 Tao 的非线程安全 `Rc`。这支持优先消除该跨线程复制/销毁边界；当前小转储没有相关堆内存，无法复原计数首次被破坏的时刻或完全排除其它内存破坏。候选补丁已通过默认/tracing 所有权单测、debug/release 独立 Windows runtime 回归与完整 Rust 门禁；另已完成 E-023 的长时真实桌面验证；机器结果仍不替代物理交互手验。

最终 17 场景证明受支持异常能够留存并离线定位，也覆盖原先 16 场景遗漏的整体交接超时。新候选 `20261008T150046Z-db61c29cf1bd-e0f20e5a` 使用 2026-10-08 22:59:30（UTC+8）冻结后的实现，标准构建、精确 EXE/PDB 归档及 release 私有入口拒绝验证通过；第二轮标准安装矩阵 14/14 与精确候选 verify 已通过。首轮 `same_version_repair` 的 30 秒 readiness 超时原因仍未定位，完整失败证据保留，复验通过不构成原因解释。

候选构建工作区已由标准 runner 自动清理，真实 release 私有符号与绑定校验仍成功；持久私有夹具副本可再次离线定位 UD2。结合缓存移除单测支持 AC-5 机器证据，但实际没有删除共享 Cargo 缓存或执行广泛 GC。精确候选的 1800.455 秒真实桌面压力、受控退出、匹配正常退出摘要与隔离清理均已通过；物理穿透、跨屏拖动和托盘退出仍待手验。此前候选 `20261008T141926Z-db61c29cf1bd-821a8334` 因 AC-7 补修已退出验收，不能复用为最终候选通过证据。

## 证据账本

| ID | 日期 | 问题或主张 | 类型与锚点 | 摘要 | 置信度 | 状态 |
|---|---|---|---|---|---|---|
| E-001 | 2026-10-08 | 主程序确实崩溃 | runtime-supported；Windows Application Error 1000 / WER 1001；PID 30496 | 18:05:17 异常 `0xc000001d`；18:05:21 APPCRASH；模块为主 EXE，RVA `0x3bb579` | confirmed | active |
| E-002 | 2026-10-08 | 符号与事故代码精确匹配 | runtime-supported；build `20261007T163032Z-8eeb988f6591-878a2034`；PDB GUID/age | 安装 EXE 与构建 EXE 的 CodeView GUID/age 相同，代码与 unwind 数据相同；仅 NSIS bundle-type 标记有三字节变化 | confirmed | active |
| E-003 | 2026-10-08 | 故障由桌宠光标后台线程触发 | runtime-supported + code-supported；异常线程 11920；见下述栈与反汇编 | Windows StackWalk64 展开得到窗口复制链；调用闭包传入 `pet` 并休眠 16,000,000 ns，与事故源码 `spawn_cursor_feed` 一致 | confirmed | active |
| E-004 | 2026-10-08 | 直接终止来自 Rc 自增回绕保护 | runtime-supported + code-supported；RVA `0x3bb4b0` / `0x3bb4b3` / `0x3bb579` | 非原子 `inc [rsi]` 后 `JE` 到 `UD2`；异常寄存器与栈中保存寄存器排除了其它共用终止分支 | confirmed | active |
| E-005 | 2026-10-08 | 跨线程 Rc clone/drop 是计数损坏的优先解释 | code-supported；tauri-runtime-wry 2.11.2、tao 0.35.3 | Context 派生 Clone 且跨线程可用；嵌套 EventLoopWindowTarget 包含 Rc；AF 的后台采样实际触达该链 | strongly-supported | active |
| E-006 | 2026-10-08 | 现有产品留证不完整 | code-supported；事故 revision 与需求基线相关文件比较 | panic hook、正常退出记录已有；未有主 EXE native dump 收集/符号持久归档；诊断导出不纳入 OS 原生主程序转储 | confirmed | active |
| E-007 | 2026-10-08 | 简单升级或只改光标 caller 不足以作为修复依据 | code-supported + 上游资料；见下述来源 | 已查的稳定 2.12.1 仍存在 Context 复制原生线程状态的边界；官方修复 PR 尚未合入；AF 的 AppHandle、其它后台窗口调用和依赖内部入口共享此边界 | confirmed | active |
| E-008 | 2026-10-08 | 采集方式必须明确覆盖边界 | 官方 Windows API 契约；见下述来源 | LocalDumps 配置要求管理员；UEF 不覆盖 fastfail；MiniDumpWriteDump 建议由外部进程调用 | confirmed | active |
| E-009 | 2026-10-08 | 新离线工具可重现本次事故栈 | runtime-supported；`scripts/crash-diagnostics/run.ps1 symbolize`；本机 `symbolized-v2.json` | 精确绑定原转储/PDB及事故 EXE；展开 8 帧，包含 Context/Webview clone、get_webview；旧构建缺源码行明确记为 gap | confirmed | active |
| E-010 | 2026-10-08 | Windows 所有权边界具备确定性回归 | test-supported；`scripts/tauri-runtime-regression/run.ps1 unit`、`stress`、`stress --release`；候选工作区 | 默认/tracing 各 6 个测试；debug/release 各 5 个独立 runtime 场景通过，其中 8×100000 clone/drop；只证明这些场景，不替代长时/完整桌面验证 | confirmed | active |
| E-011 | 2026-10-08 | 首轮留证路径可捕获真实故障并定位源码 | runtime-supported；`scripts/native-crash-smoke/run.ps1`；私有 `native-smoke-16/report.json` | 历史 16 场景通过；UD2 13 帧定位旧 `windows/mod.rs:139`，panic 22 帧定位旧 `:135`；不覆盖后续发现的整体交接超时，原始报告保留 | confirmed | superseded by E-014 |
| E-012 | 2026-10-08 | 实现通过项目确定性门禁 | test-supported；本会话 `scripts/check/run.ps1 --stage backend/frontend/rust` 输出，见 progress | backend 3942 passed/5 skipped，frontend 178 files/1455 tests+生产构建；AC-7 补修后最终 Rust 331 passed/1 既有 ignored、fmt/release check通过；安装矩阵和长时桌面独立证据见 E-021/E-023 | confirmed | active |
| E-013 | 2026-10-08 | 整体交接超时可见，迟到上下文不再用于转储 | runtime-supported + code-supported；`native-smoke-17/watcher-delay/`；`windows/ipc.rs` / `dump.rs` | 首次等待 8000ms、第二次 0ms、sequence 1→1；早期 coverage unavailable/session degraded，后期补 capture-context-expired 且无 dump；共享及 worker 局部 CONTEXT 16 字节对齐修复有断言与全场景复验 | confirmed | active |
| E-014 | 2026-10-08 | 最终受支持原生故障可留存并绑定预设源码位置 | runtime-supported；`native-smoke-17/report.json`、两份 `fixture-final-*-symbolized.json` | 17/17 隔离场景通过，含 watcher-delay；UD2 13 帧定位 `windows/mod.rs:161`，panic 22 帧定位 `:157`，GUID/age 精确匹配；未记录 debug 全树快照且部分帧无源码行，均保留 gap | confirmed | active; supersedes E-011 |
| E-015 | 2026-10-08 | 诊断消费保持隐私和失败语义 | test-supported；本会话 consumer 契约与内存 ZIP probe、Rust exporter→ZIP→consumer 输出 | 62 tests 与 6 个新增错误码×默认/opt-in 的 12 组 probe 通过；unavailable/degraded/failed 消费为 partial，未伪报 dump；真实 producer 链保留 formal/local-test 身份与授权边界 | confirmed | active |
| E-016 | 2026-10-08 | 静态证据不再依赖 Codex 应用缓存 | artifact-supported；私有持久根 `persistent-copy-manifest.json` | 从实际 MSIX 缓存源复制 211 文件、813984280 字节，逐文件源/目标 SHA-256 一致；句柄解析确认目标位于用户私有目录、Git 外且非 Packages/LocalCache，源未删；进行中矩阵与一次性探针未纳入 | confirmed | active |
| E-017 | 2026-10-08 | 新候选已完成标准构建与精确符号归档 | artifact-supported；`build-20261008T150046Z-db61c29cf1bd-e0f20e5a.json`、同 build 的 `symbols.json` | 0.3.0 local-test / installer-smoke，构建报告含 installer SHA；EXE/PDB GUID `7e9de118-7ffc-4fd7-9906-291ab575e045` / age 1，记录 dirty 源码树 SHA；后续清理复核见 E-022 | confirmed | active |
| E-018 | 2026-10-08 | release 不接受受控私有运行模式 | runtime-supported；`release-private-modes-20261008T150046Z.json` | 精确候选 release EXE 的 fixture/watch/dump/unknown 四种私有参数均退出 64，fixture 输出目录为空 | confirmed | active |
| E-019 | 2026-10-08 | 安装矩阵的 MSIX 运行环境已纠正 | runtime-supported；`installer-matrix-attempt-1/normal-environment-probe.json` 及原 preflight 输出 | 首次 MSIX 重定向 preflight 拒绝发生在安装前；WMI 普通进程探针通过，Windows session 1、路径正常；后续标准矩阵结果分别记录于 E-020/E-021 | confirmed | historical |
| E-020 | 2026-10-09 | 首轮矩阵存在一次服务启动等待失败 | runtime-supported；私有 `installer-matrix-attempt-1/` 中矩阵报告、日志及 archive-manifest | 13/14 通过；same_version_repair 的 bridge readiness 在 30 秒门限内未就绪，日志仍在初始化，具体原因未证实；失败原件保留。相同 candidate、源码与阈值的第二轮通过见 E-021，不能解释或抹去本次失败 | confirmed | active; cause unresolved |
| E-021 | 2026-10-09 | 第二轮标准安装矩阵和精确候选校验通过 | runtime-supported；私有 `installer-matrix-attempt-2/` 中标准 smoke 报告、runner、matrix/verify 日志及 archive-manifest | 14/14 场景 passed；runner phase=completed/ok=true，matrix/verify 均退出 0，绑定同一 build/installer/input SHA。交互中英文选择器 not-run，本次未改语言；Guide builtin 临时 overlay 的 git status 为空 | confirmed | active |
| E-022 | 2026-10-09 | 候选构建工作区清理后证据仍可校验和定位 | runtime-supported + test-supported；`post-cleanup-release-symbols.json`、`post-cleanup-final-ud2-symbolized.json`、`test_symbol_archive_survives_build_cache_removal` | 真实候选工作区已移除，私有 release EXE/PDB 与绑定校验成功；持久私有 debug 夹具再次展开 13 帧到 mod.rs:161。共享 Cargo cache 未删、未跑广泛 GC；debug 全树快照及部分 source-line gap 保留 | confirmed | active |
| E-023 | 2026-10-09 | 精确候选真实桌面压力与正常退出留证通过 | runtime-supported；私有 `desktop-stress-final/` 报告、截图、匹配 session 摘要及 cleanup verification | 1800.455s，104630 cursor、99 reload、6964 native queries、59 passthrough API round trips，page errors 0；bubble 销毁、exit 0、tauri-exit/orderly-exit、session completed=true；独立核对隔离清理，物理穿透/跨屏拖动/托盘退出未验证 | confirmed | active |

## 事故身份与分析方法

- 产品版本：`0.4.0`；production、formal、ordinary。
- 事故源码：`8eeb988f65910d18ced4a16a2b409d0fc2cc1d71`，clean。
- 需求起点：`db61c29cf1bd8efe3f977d9461c3c1880c0868b3`。它不是事故运行的源码身份。
- 事故主进程 PID：`30496`；process session：`30496-1791424261078`。
- WER IntegratorReportIdentifier：`211098b1-3d76-4def-9d66-e3ae5ed93c1a`。
- CodeView/PDB GUID：`7d21b5ab-e74f-4802-ac98-12353e030e56`，age `1`，文件 `app.pdb`。
- 查询范围：本次 Windows Application 事件及对应 dump、同一进程会话的产品日志、精确构建二进制与符号。旧 dump 仅查了异常头，未将其它事故合并归因。
- 使用本机 Windows `StackWalk64`，以 dump memory、时间戳匹配 PE 的 `.pdata` 与映射代码提供读取/unwind 回调。下表是实际展开结果，不是扫描栈中疑似地址。
- 使用 MSVC 14.44.35207 所附 `llvm-symbolizer.exe` 符号化，`dumpbin.exe /headers`、`/disasm /range:...` 校验 CodeView 和故障分支。Tauri 优化产物只给出部分函数名，无完整源码行信息。

| 栈帧 | 主 EXE RVA | 符号/绑定 |
|---|---|---|
| 0 | `0x3bb579` | `tauri_runtime_wry::Context<T>::clone`，故障 `UD2` |
| 1 | `0x77cd96` | `tauri::webview::Webview<R>::clone` |
| 2 | `0x74b4fb` | `tauri::manager::AppManager<R>::get_webview` |
| 3 | `0x4b6ad5` | 优化后的线程闭包；`pet` 字符串及 16ms 循环绑定 `spawn_cursor_feed` |
| 4 | `0x38a859` | `FnOnce::call_once` vtable shim |
| 5 | `0xf5cc9c` | Rust Windows thread start |

返回地址符号化使用 RVA 减一；之后的系统栈帧为 kernel32 和 ntdll 的线程启动函数。

`Context::clone` 的多个保护分支共用 `0x3bb579`。异常时 RSI 与栈中保存的原 RSI 不同，RDI 也已被更新，证明执行到非原子计数路径；RBX 仍为零，排除了后续以 RBX 作为有效计数地址的路径。ZF 为 1，与 `inc` 后结果为零一致。结合控制流，确认 `0x3bb4b0 → 0x3bb4b3 → 0x3bb579`。对应堆块不在 dump 的 memory streams 中，不据此臆测其 weak count 或分配历史。

相关源码入口：

- 事故 revision 的 `frontend/src-tauri/src/lib.rs:911`：`spawn_cursor_feed`。
- `tauri-runtime-wry-2.11.2/src/lib.rs:257`：Context Clone；`:440`：DispatcherMainThreadContext；`:452` / `:456`：unsafe Send/Sync。
- `tao-0.35.3/src/platform_impl/windows/event_loop.rs:169`：runner_shared；`event_loop/runner.rs:27`：`Rc<EventLoopRunner<T>>`。
- `frontend/src-tauri/src/runtime_observability.rs`：panic hook；`diagnostics/mod.rs`：日志导出边界。
- `scripts/package/run.py`、`releases.py`、`workspace_gc.py`：构建清理、发布制品和缓存保留边界。

### 技术方案调研的外部锚点

以下来源于 2026-10-08 核对，只支撑具体机制或能力边界，不替代本次转储证据：

- [Tauri runtime-wry 2.12.1 固定版本源码](https://github.com/tauri-apps/tauri/blob/tauri-runtime-wry-v2.12.1/crates/tauri-runtime-wry/src/lib.rs)：Context 仍直接持有主线程对象。
- [Tauri PR 15411](https://github.com/tauri-apps/tauri/pull/15411)：核对时 open，head `2d51238d2597fcb677fd52b7179e5fbee25fd75f`；讨论主线程所有权以及 display handle 生命周期。
- [Tauri issue 15408](https://github.com/tauri-apps/tauri/issues/15408)：依赖内部的自定义协议也能在主线程外复制同类 Context。
- [Microsoft：收集用户态转储](https://learn.microsoft.com/en-us/windows/win32/wer/collecting-user-mode-dumps)：系统 LocalDumps 的部署权限与注册表范围。
- [Microsoft：SetUnhandledExceptionFilter](https://learn.microsoft.com/en-us/windows/win32/api/errhandlingapi/nf-errhandlingapi-setunhandledexceptionfilter)：入口在故障线程执行，存在栈等限制。
- [Microsoft：fastfail](https://learn.microsoft.com/en-us/cpp/intrinsics/fastfail?view=msvc-170)：不依赖常规异常 handler 的终止路径。
- [Microsoft：MiniDumpWriteDump](https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/nf-minidumpapiset-minidumpwritedump)：外进程采集建议与调用限制。
- [Microsoft：PDB 身份术语](https://github.com/microsoft/pdb-rs/blob/main/docs/terminology.md)：GUID/age 与文件内容完整性属于不同校验边界。

## Artifact 映射

权威本机私有 research 根：`C:\Users\example-user\agent-friend-private\research\156-native-crash-evidence`。已通过文件句柄核验实际目标不在 `Packages/LocalCache`，且位于 Git、worktree 和打包缓存之外；原始文件不随需求文档提交。事故六份原始材料位于其 `incident-20261008-180517/`，其中 `manifest.json` 保存初次归档位置、时间、大小与 SHA-256。下表哈希均为 SHA-256。

历史复制源实际为 `C:\Users\example-user\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\agent-friend-research\156-native-crash-evidence`，来自 Codex MSIX 对先前 `%LOCALAPPDATA%/agent-friend-research/` 写入的重定向。211 份已完成静态文件共 813984280 字节已复制并逐文件核验源/目标 hash；源文件全部保留。新根 `persistent-copy-manifest.json` 的 SHA-256 为 `2315791b35a42dc153daa12596aabc1e6e6086133c129fdc33b49fc6d0b05648`，记录逐文件路径、大小与 SHA。当时进行中的 `final-matrix-runner.json`、`final-installer-matrix.log`、`final-installer-verify.log` 及一次性 `normal-environment*` / `run-final-matrix*` 探针/runner 未纳入该次复制；两轮矩阵现分别在下表 attempt 目录归档，不回写这份历史清单。

| Artifact | 原始位置类型 | 归档文件 | SHA-256 | 敏感性 |
|---|---|---|---|---|
| 本次转储 | Windows 当前用户 CrashDumps，PID 30496 | `agent-friend.exe.30496.dmp` | `72c6f9907ebd49631d9ae504d080592e7d25f496df675be275c8459c1410f2f2` | 可能含用户内存；仅本机 |
| 安装 EXE | production 安装根 | `installed-agent-friend.exe` | `fd11100433ce3003a894c5f9457cff65bc340667c25b15a08bbf86a30b820096` | 本机构建产物 |
| 构建 EXE | 对应 worktree Cargo release 缓存 | `build-agent-friend.exe` | `9f1f04fbe9c2e0d4aba09fdfad1b2bf8fe5ca10d4cacdb5ad6a92833f9e4f9f7` | 本机构建产物 |
| 匹配符号 | 同一 Cargo release 缓存 | `app.pdb` | `cc7c38bbf46e350fabbc03399fc17279c6e3e96b7413f938f74ed20b5b2da7b3` | 含构建路径；仅本机 |
| 构建身份 | production 安装根 | `package-build.json` | `68bf7c16c3e9d03878d0b0ac08d19aa779929b738efd0663b1b09375dfbac7fd` | 本机元数据 |
| WER 报告 | Windows 系统 ReportArchive | `Report.wer` | `85eb08f965ef060a89a622e69f145a59c8cb1da9cda3dee26a5c6df4c93d305b` | 含进程/路径信息；仅本机 |

受控验证使用上述权威 research 根，与真实安装和数据隔离。下列摘要/清单已核对 hash；`native-smoke-17/archive-manifest.json` 保存 94 份夹具证据的相对路径、大小和逐文件 hash，原始 dump/EXE/PDB 仍仅在本机私有区。

| Artifact | research 根内路径 | SHA-256 / 身份 | 用途与局限 |
|---|---|---|---|
| 历史 16 场景报告 | `native-smoke-16/report.json` | `8f5ff4eaa58a64d7e55a3f985b7947f2b303708180233d56c69b2f22a55f9db5` | E-011 历史保留，不覆盖 watcher-delay |
| 最终 17 场景报告 | `native-smoke-17/report.json` | `b3fc3d28133698a940a021f343bd286cb26f966b183b5505d61f53147e1f5be8` | 17/17 passed；Windows debug 隔离夹具 |
| 最终归档清单 | `native-smoke-17/archive-manifest.json` | `7c241a01a6a627c1f13b47fc0694e27356a5839960be7fef894bb3f324cadfc2` | 94 份证据；包含 watcher-delay 的早/晚 coverage 与 handoff-observation |
| 最终 UD2 定位摘要 | `fixture-final-ud2-symbolized.json` | `7c7f821fe40048c698ae6f0d5707e2b9a2f3859f98b7689215f7f150426e8ac5` | 13 帧；`fixture_main` 当前行 161 |
| 最终 panic 定位摘要 | `fixture-final-panic-symbolized.json` | `f458be9d7ca5ee37c1af97db9b8bebf81a5937c03ca46880af8123ab51d78b1f` | 22 帧；预设 panic 当前行 157 |
| 最终夹具符号 | `fixture-symbol-validation/_symbols/windows-x64/development/native-fixture-g40dswlg/` | GUID `244810ab-2223-4431-9626-291e901e4a9f` / age 1；文件 hash 见 `symbols.json` | EXE/PDB 精确绑定；`sourceTreeSha256=null`、`sourceSnapshotStatus=not-recorded-at-build-time`，不能替代正式包来源凭证 |
| 候选 release 私有入口验证 | `release-private-modes-20261008T150046Z.json` | `10bfd32c840d160a9f8f123f82b9d4894d657a7be68a2f64aecaebf9e146e4fa` | 四种私有参数均退出 64，未写 fixture；仅验证此精确候选 |
| 首轮矩阵归档清单 | `installer-matrix-attempt-1/archive-manifest.json` | `973939304d8effc542f0e7cab5939b50914e9b03c437e07b0cc19f3a4131c23d` | 保留 13/14 及 readiness 失败报告/日志；未覆盖 |
| 第二轮标准矩阵报告 | `installer-matrix-attempt-2/smoke-20261008T150046Z-db61c29cf1bd-e0f20e5a.json` | `70e1fac49b785ead3595da888ab1fabd3e405c456294d394c203f0bfa23d2201` | 标准报告原样复制、源/目标 hash 一致；14/14 passed，语言选择器 not-run |
| 第二轮矩阵归档清单 | `installer-matrix-attempt-2/archive-manifest.json` | `ac63d023cae353c38b3f8673c7557232f28ee364f4dffc64a07843745a5ba2a8` | 4 份报告/runner/matrix/verify 日志、41032 字节；精确候选 verify 退出 0 |
| 清理后 release 符号校验 | `post-cleanup-release-symbols.json` | `0cd817f5dc0c69b4fb834b83b4c2a6b063bca59e7835f021c2ec89d7aede270b` | candidateWorkspaceRemoved=true，sharedCargoCacheDeleted=false；真实私有归档及 release binding 成功 |
| 清理后私有夹具离线定位 | `post-cleanup-final-ud2-symbolized.json` | `7c7f821fe40048c698ae6f0d5707e2b9a2f3859f98b7689215f7f150426e8ac5` | 全部输入来自持久私有副本；13 帧到 mod.rs:161，与原定位摘要一致；debug 来源/源码行 gap 保留 |

候选构建与发布符号在各自标准产物目录，未混入原事故归档：

- 构建报告：`C:\Users\example-user\AppData\Local\agent-friend\packaging\work\4aa1f81a6164\94c042b090eb\installer-smoke-reports\build-20261008T150046Z-db61c29cf1bd-e0f20e5a.json`，SHA-256 `ebcf5ccdaab0e8e2905534f0b054ba25fa736335a412bc7bcdf66f9f14f4223a`。installer SHA-256 为 `1cef38a94de7a02387704ebebcc31da413b90b3ae0a11f8991aa86e2af1c4158`。
- 精确符号归档：`C:\Users\example-user\agent-friend-releases\_symbols\windows-x64\installer-smoke\20261008T150046Z-db61c29cf1bd-e0f20e5a`。`symbols.json` SHA-256 为 `c778be6228bb5254cbd50b0b86e1d2cd58d338537d772f958029c3c8dc3344fc`；GUID `7e9de118-7ffc-4fd7-9906-291ab575e045` / age 1，EXE SHA-256 `107bc3c66ec78f48f863e48c4d6141fb2e89bb2f30e1eda77bed817a7abc4ff9`，PDB SHA-256 `6675fcbc48b593146937061a5a8f16ae444ac5bde00be40e1ebbc491504a73a1`。
- 源码身份：commit `db61c29cf1bd8efe3f977d9461c3c1880c0868b3`、dirty=true、`sourceTreeSha256=5ac3141f865f89f6c76508752d1d900043503eb31f2acef9d005fd73fb26d7d5`；标准构建报告的安装行为输入 SHA-256 为 `463e00f54c57d638ddf521ab71425a33610ed0ef2dee4a6dcb3e42f7d36662c1`。这是 local-test installer-smoke 候选，不是正式发布包。

### 最终候选的真实桌面验证

- 私有证据目录：`desktop-stress-final/`，归属于当前权威私有根；`evidence-manifest.json` 记录 11 份文件、180550 字节，SHA-256 `a060e4ddef410a2b6d487a2f7f844b03302bc58f0b696481a671d742e2ec6be2`。
- 运行构建：`20261008T150046Z-db61c29cf1bd-e0f20e5a`；PID `48664`；session `48664-1791477203651`；普通 Windows session 1、installer-smoke profile、bridge/voice 28800/28900；没有发送对话或调用真实 LLM。
- 2026-10-09 00:33:34 至 01:03:34（UTC+8）实际观察 1800.455 秒：1741 ticks、104630 cursor events、99 pet/chat reloads、6964 native queries、59 passthrough API round trips；page errors 为空。
- 两个实际 monitor 为 2560×1600 @175% 与 2560×1440 @125%；开始/结束拓扑一致。这里只验证读取与 API 往返，未验证物理点击穿透、混合 DPI 跨屏拖动或用户托盘退出。
- 受控 bubble WM_CLOSE 观察到销毁，再向其所属主线程 WM_QUIT；主程序退出 0。对应 `*-999998.json` 为 `process-exit`，`orderlyReason=tauri-exit`、`dump.errorCode=orderly-exit`；session `completed=true`、`failures=[]`。两条 service-first/exit 1 发生在测试收尾时，不据此声称运行期服务崩溃。单独核对了最终摘要，未用清理成功代替落盘确认。
- `cleanup-verification.json` 由普通 Windows 进程核对：隔离 install/data/settings/logs/move 和注册项不存在，owned processes 为空，28800/28900/CDP 64853 无监听，package 锁已释放。没有对正式安装执行更新或停止操作。
- `chat-webview.png` 仅为隔离聊天 WebView：空白会话与已连接状态；不冒充桌面全屏、物理穿透或多屏拖动证据。

## 未决问题

| 问题 | 当前边界 | 下一项有区分力的证据 |
|---|---|---|
| Rc 计数如何首次损坏 | 并发复制/销毁仍为优先解释；当前事故 dump 缺堆内存，机制回归不补写历史事实 | 若未来再发同类事故，以新留证收集到的上下文区分；本次不宣称已还原首次损坏 |
| 桌面修复的实际稳定性与兼容性 | 机制及 debug/release 回归、1800.455 秒精确候选真实桌面压力通过；104630 cursor events、99 reloads，无提前退出 | 物理鼠标跨窗穿透、混合 DPI 跨屏拖动与用户托盘退出仍待手验；不能外推为所有长期稳定性问题均消失 |
| 哪些异常能可靠留存 | 17 场景已覆盖声明路径；fastfail/强杀仅 exit-only，任意断电/堆写入历史仍无法保证 | 新候选安装态确认生命周期；未覆盖平台/线程不外推 |
| 首轮安装矩阵的 readiness 超时 | 首轮 same_version_repair 在 30 秒门限内未就绪，原因未定位；同候选同阈值第二轮 14/14 与 verify 通过 | 若后续再现，用初始化阶段时序区分慢启动来源；不把复验成功写成首轮根因已确认 |

历史 16 场景夹具的 EXE/PDB 保留在私有 research 的 `fixture-symbol-validation/_symbols/windows-x64/development/native-fixture-dekt-gbs/`。EXE SHA-256 为 `1b9a8ad25afb0cc7668ab81d45fa441c6f5324494274d027ec70162d6021bb80`，PDB 为 `579e70cb58049772b18fdcc65947149f60b568dc4c1ffc8a456b9f1d9628fd12`。最终 17 场景使用上表 `native-fixture-g40dswlg`，没有覆盖旧归档。两次 debug 夹具均未记录构建时完整源码快照；精确故障位置由 EXE/PDB/转储绑定，不能写成 clean commit 构建。后续安装候选由标准 package runner 生成完整身份。

## 对需求与方案的影响

- E-001～E-005 绑定原事故直接终止机制；E-010/E-012 支撑候选所有权修复的机器验证，但不证明原事故首次计数损坏时刻，也不替代 AC-2 桌面验收。
- E-006 记录原观测缺口；E-013～E-015 支撑原生采集、失败可见、离线定位和隐私导出的机器 AC。E-011 仍保留为历史，不将 16 场景当成整体超时已覆盖。
- E-016 保全静态证据；E-017/E-018 证明新候选标准构建、精确符号归档及 release 私有入口拒绝。E-022 与已有缓存移除单测支撑 AC-5 机器证据，实际清理仅覆盖候选工作区，不扩大为共享缓存或广泛 GC。
- E-021 完成安装矩阵与精确候选 verify，E-020 的首轮未定位启动超时继续保留；E-023 完成 30 分钟真实桌面、受控退出及隔离清理。交互语言选择器未执行；AC-2/8 只剩相应物理交互与最终用户验收边界，不提前标为全部验收完成。
- 本次事故原因和通用留证能力需分别验收，不能用留证功能替代本次修复有效性的证据。

## 对账记录

| 日期 | 范围 | 结果 |
|---|---|---|
| 2026-10-08 | 历史运行身份、转储、符号、需求状态与归档 hash | 原始证据已本机保全；需求已确认；未写入技术方案、未实施修复、未执行候选验证 |
| 2026-10-08 | 上游修复状态、Windows 采集约束、发布与导出集成 | 新增 E-007/E-008；方案进入待用户 review，未把候选设计写成已实现或已验证 |
| 2026-10-08 | 用户批准实施后的首批验证 | 新增 E-009/E-010；旧事故符号归档在私有 research 根，独立于构建缓存；正式 release 自动归档仍待新包实测 |
| 2026-10-08 | 最终 17 场景、AC-7 补修与源码门禁 | 新增 E-013～E-015，E-011 标为 superseded 并保留原报告/符号；实读 8s/0ms 观测、早晚失败投影与当前行 161/157 定位，核对关键 artifact hash；最终 Rust 331 passed/1 ignored、consumer 62 tests+12 probes |
| 2026-10-08 | 新候选等待安装与桌面验收 | 旧候选退出验收；记录新 build `20261008T150046Z-db61c29cf1bd-e0f20e5a` 正在构建，AC-2/5/8 保持未完成；本轮只更新 progress/本索引，未修改需求、方案或冻结输入 |
| 2026-10-08 | 持久归档与候选构建完成后对账 | 新增 E-016～E-019；211 份静态证据离开 MSIX 缓存并逐文件验 hash，源保留；核对新候选构建报告、精确符号与四种私有参数退出 64。安装矩阵在普通进程执行中，AC-2/5/8 保持未完成；仅更新 progress/本索引 |
| 2026-10-09 | 第二轮矩阵、精确候选 verify 与清理后符号 | 新增 E-021/E-022；第二轮标准报告复制至独立 attempt-2，核验 14/14 与两个退出码 0；保留 attempt-1 的 13/14 失败及未定位原因。AC-5 机器证据完成，明确未删除共享 Cargo cache；真实桌面仅启动采样，AC-2/8 与用户验收保持开放 |
| 2026-10-09 | 最终桌面压力、退出及生命周期对账 | E-023：30 分钟和受控退出通过，真实正常退出摘要已取得，无需额外退出 probe；11 份证据带 hash 持久保留，隔离环境清理完成。AC-2 的物理交互及 AC-8 的用户验收继续开放，源码/候选保持冻结，未更新正式安装 |
