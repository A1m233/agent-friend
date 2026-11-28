# 185 · Windows 沙箱执行与恢复可靠性 — 进度

## 总体状态

IN_PROGRESS（2026-11-29，实现及本轮验证完成，待用户验收）

用户明确要求“一次性给我干完这个需求，自己规划清楚”。继续完成统一记录、执行域与原生监督、后台恢复及跨层投影、完整验证，不再按首条链路作为结束点。已刷新并无冲突 fast-forward 到 `03a095667e6088723ef19e2f80f45dea1f06fb7e`；主干修复了下文历史记录中的需求 184 lint/format 问题。

本轮实现和项目门禁已完成。生命周期记录、准备前取消、原生 Job 所有者、沙箱/宿主执行域、迟到响应核对、后台恢复 API、前端进度和诊断投影已落地。最终后端 5111 passed / 6 skipped，前端 1668 tests 和生产构建通过，Rust 测试与 release check 通过。下面单独保留首条链路旧候选的历史记录；当前候选、原生证据和延期项各自独立记账。

用户明确回复“开始吧”，授权按已确认 [技术方案](./design.md) 实施。后续同范围工作沿用本次授权；原生能力不足而需要后端改动时，按方案的回溯边界处理。安装版和 frozen runtime 的 PV-1 继续延期，不作为本次合入门槛。

2026-11-29 用户说明没有隔离测试环境并确认先跳过：真实沙箱账户、不同本地 SID、UAC 与 Windows 10 原生验证列为 PV-2，本轮不因这些环境缺口阻塞交付。没有把未执行项改称通过，也没有操作现有 production 沙箱或安装。

当前实施基线：`03a095667e6088723ef19e2f80f45dea1f06fb7e`。首条链路历史基线为 `15a400d1cbae6def4118c6fe0e691573a5bad804`。原现场和早期调研基线保留在 [证据索引](./evidence-index.md)。

## 实现任务

- [x] 首条链路：迟到的明确未启动拒绝 → 核对解除阻塞 → 手动修复 → 独立新命令成功；runtime 与授权工具入口已覆盖，后端/账户修复使用替身。
- [x] RPC 超时归属和容量保留、查询/终止容量预留、旧连接通知隔离、未核实记录不随输出缓存裁剪。
- [x] 未核实旧命令参与新命令准入；初始化文件正常也不能绕过恢复核对。未发出的请求失败不创建未知资源记录，也不自动重放。
- [x] 不明资源在修复入口返回受阻；不将断线、迟到成功、根进程 closed 或 terminate 接受视作原生清理证明。
- [x] M0：建立安全隔离、可重复的宿主原生/协议资格专项，分别记录已验证与未验证能力；完整沙箱资格未通过。
- [x] M0 边界复核：准备取消和首因接口按源码核对；普通创建、NETCREDENTIALS_ONLY broker 与协议在本机实跑。不同本地 SID/UAC 真实故障注入按用户 PV-2 延期。
- [x] 复核 M0 推断：保留官方 0.147.0 payload，不修改 Codex 源码或 vendor lock；外层可观察事实独立验证，内部首因缺口明确保留。
- [x] M1：以 LifecycleStore 替换 `_uncertain_starts` / `_sandbox_process_ids` 两套恢复资格；私有目录、独占锁、原子落盘、完整性校验、容量上限与重启保守恢复。
- [x] M1：划分 Windows 沙箱/宿主执行域；Job 在进程创建时绑定，捕获初始化时的服务句柄基线，查询/终止保留容量。正常后代不随根退出被杀。
- [x] M2：后台恢复、重试键、版本化取消、唯一总预算、维护互斥、兼容 setup 有界等待、实际能力探针实现；真实账户/UAC 资格仍在 M0 验证缺口内。
- [x] M2：Bridge、前端、工具结果、输出证据 v2 及诊断 revision 25 / command-runtime-health-v3 接线；旧证据使用 legacy-unverified。
- [x] M3：本轮完整故障注入链与相邻场景回归、backend/frontend/rust 门禁和本机原生专项通过；真实沙箱/Windows 10 部分按 PV-2 延期。
- [ ] 用户验收。

## AC 验证

| AC | 当前证据与状态 |
|---|---|
| AC-1 生命周期边界 | 已实现统一记录及持久化；确定性存储、取消、API、恢复测试通过。磁盘错误阻止新副作用，首因不会被后续失败覆盖 |
| AC-2 未知启动核对 | 迟到拒绝、成功、查询、断线及矛盾事实分别处理；已确认未启动才可续接；未知原命令不重放，原生归属不明时明确阻塞 |
| AC-3 取消与残留工作 | 准备取消后父命令不启动，未结束子准备继续显示 stopping；迟到完成释放资源守卫。运行停止需资源证据，RPC 接受不算已取消。真实 UAC 取消未执行 |
| AC-4 恢复可达 | runtime 与授权工具入口的“超时 → 迟到拒绝 → 修复 → 新命令成功”回归通过；账户修复为替身 |
| AC-5 身份与目录状态 | 结构/目录失败、探针失败分支有确定性回归；探针实现检查真实用户名、读写策略、输出、退出和收尾。真实身份失效及 UAC 仍需隔离环境 |
| AC-6 并发影响控制 | 并发 admission、健康工作阻塞维护、沙箱故障不停止宿主命令已有测试；全局维护互斥和同数据根单 owner 防止多实例重建；不同身份原生资格待验 |
| AC-7 失联与重启 | 来源实例隔离、失联执行器回收、持久化请求去重和 interrupted 恢复有测试；未知旧业务不会由新实例改写为成功；仅 OS uptime 严格下降作为重启证明 |
| AC-8 跨层一致性 | 异步 API / 版本取消、store 重连/旧响应、工具事实及旧历史兼容已实施；前端全套 1668 tests 通过，无真实桌面视觉结论 |
| AC-9 故障证据 | 新证据包含首次失败、后续失败、执行与资源独立状态及恢复结果；标识哈希化，排除命令/环境/凭据/控制句柄；生产/解析机器契约同步，旧包可读 |
| AC-10 后端能力验证 | 当前官方 payload 的宿主/协议专项 5 cases 重新运行，全部 Job 清理为 0；真实普通创建、stdin、NETCREDENTIALS_ONLY broker 在本机通过。后者不是不同本地 SID 或 UAC 证明，完整资格仍待验 |
| AC-11 组合回归 | runtime/授权工具故障→恢复失败→迟到拒绝→手动修复→独立命令通过；准备取消和服务失联/宿主不受影响已补。业务协议与账户修复使用替身；真实沙箱整链未冒称通过 |
| AC-12 项目检查与平台证据 | backend/frontend/rust 最终门禁全部通过。Windows 11 Home 10.0.26200 为本机实跑平台；Windows 10 / 真实不同本地身份 / UAC 按 PV-2 延期；POSIX 路由单测不外推为 macOS 实机通过 |
| PV-1 安装版与 frozen runtime | 用户明确延期，未执行 |
| PV-2 隔离原生环境 | 用户 2026-11-29 确认先跳过，未执行；不阻塞本轮源码交付 |

## 实现日志

### 当前冻结候选（2026-11-29）

代码和测试输入绑定 [candidate-01.json](./artifacts/candidate-01.json) 与 [源文件 overlay](./artifacts/candidate-01.sources.zip)，共 49 个代码/测试文件；文档和历史 artifact 单独保留。所有文件 hash、artifact 清单和需求内本地链接已核对，无不一致。当前产品源码未升级 Codex、未更改依赖或打包配置。

本轮行为：准备前登记操作和取消入口；迟到结果关联原请求；业务结果与资源状态独立；未知副作用不重放；健康任务不因修复被停止；沙箱和宿主域分离；修复由后台操作持有重试键、阶段、版本和总预算。旧实例释放记录所有权后拒绝继续写入，非 Windows ACL 入口保持无操作。确定性回归覆盖了这些边界。

| 检查 | 当前冻结候选的结果 |
|---|---|
| 相关组合测试 | 389 passed（冻结前）；最后两处边界先重现 2 failed，修复后存储/准备模块 12 passed；最终整体由下一行核验 |
| `scripts/check/run.ps1 --stage backend -x --tb=short` | lint 1225 文件、类型检查 998 文件通过；5111 passed / 6 skipped，pytest 864.62s，阶段总计 873.1s。23 条第三方/测试 fixture warnings；退出时 bilibili_api 的 atexit 清理报告已关闭 event loop，进程 exit 0，门禁通过，未混入本需求修复 |
| `scripts/check/run.ps1 --stage frontend` | 最终消费者输入 lint/typecheck、201 文件 1668 tests、production build 通过（114.7s）；既有 CSS/chunk warnings 保留 |
| `scripts/check/run.ps1 --stage rust` | 367 + 1 + 7 tests 通过、2 ignored，release check 通过（99.6s）；Rust/契约输入之后未改，复用该证据 |
| `scripts/command-runtime/run.ps1 lifecycle-probe --report .../m3-owned-host-protocol-01.json` | 5 cases observed；所有 Job 最后 active=0；归档包含当时客户端、native owner、harness 和后端 lock 的精确字节 |
| 诊断消费者及 guidance | 生产契约与真实解析 handler 同步到 revision 25 / health-v3；消费者 fixture 覆盖闭合资源但业务结果未知；guidance audit 0 errors / warnings；adapter sync 无漂移、doctor 通过 |

中途的两次全量检查发现观察线程启动/关闭竞态、fixture metadata 参数兼容性问题，已修复并定向复验；中断运行不记为全量通过。随后为关闭后的记录写入和非 Windows 路由补了失败先行回归，再冻结当前候选并重新开始完整门禁。

PV-1 / PV-2 均为用户明确延期；本轮没有调用外部 LLM、启动桌面视觉验收、操作现有 production 身份、构建安装包或执行 frozen runtime 验证。代码完成和本机门禁不等于延期环境已经通过。

### 历史实施记录

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-11-28 | 进入实现；核对源码基线和已有固定后端 cache | 未升级 vendor、未调用 LLM、未初始化沙箱；先建立只使用临时宿主进程的协议/资源资格探针 |
| 2026-11-28 | 增加 lifecycle-probe 双端入口、原生 Job 保护、5 个真实协议/宿主 case 与 3 个 harness 测试 | [M0 观察记录](./m0-observations.md)；当前代码仅为资格专项及其测试，所有探针进程清理完成 |
| 2026-11-28 | 固定源码复核确认准备取消不提供收尾证据，refresh 路径可能丢弃首次错误 | 按已确认方案回溯边界，暂停依赖原生保证的 M1–M3；准备最小后端扩展范围，未擅自替换或修改后端 |
| 2026-11-28 | 用户质疑自维护 Codex 补丁，撤回首选分叉建议 | 上一条“需等待后端修改”的收敛不足，现继续官方后端与 AF 外层方案复核；保留原生验证缺口及既有实施授权 |
| 2026-11-28 | 用户明确要求按四步顺序实施，先跑通“故障 → 修复 → 新命令执行成功” | 开始增加真实 AF JSON-RPC/恢复编排回归；fixture 和 provisioning 替身均明确标注，不运行账户初始化 |
| 2026-11-28 | 首条回归先失败后通过；接通迟到拒绝核对和手动修复 | 原实现丢失迟到回复，repair 未进入；候选由原请求句柄提供明确拒绝事实，新命令使用独立 ID，原命令不自动重放 |
| 2026-11-28 | 扩展授权工具入口及保守阻塞、连接与缓存边界 | 全量检查发现既有可靠性测试仍引用被替换的内部类型，已同步测试；最终相关 184 tests 和类型检查通过 |
| 2026-11-28 | 补齐新命令准入守卫 | 初始化文件完整但旧命令未核实仍走恢复核对；覆盖完整/失效身份两种链路及未发出请求的失败分支 |
| 2026-11-28 | 完成三阶段检查尝试并分诊失败 | 前端与 Rust 通过；全仓 lint/format 失败定位于未改动的需求 184 artifact。全量 pytest 的通用取消时序用例单独复跑通过，保留未确认的不稳定性，不声称全量通过 |

## 首条链路历史范围（当前实现已扩展，保留当时记录）

本轮已修改 AF 的 `exec_server.py`、`runtime.py` 和 `recovery.py`。`test_sandbox_recovery_chain.py` 经真实 AF JSON-RPC 子进程客户端、恢复协调器及授权工具入口运行；fixture 返回可控协议事件，账户修复和状态探测为替身。它证明 AF 不再因丢弃迟到的明确拒绝而永久挡住修复，不证明 Windows 沙箱初始化或完整资源收尾已经通过。

仍未取得明确拒绝、或已经观察到执行事实的旧命令保持受阻。后续继续实现准备阶段取消与原生资源归属、跨重启持久化、后台恢复 API/前端状态和真实沙箱验证；本轮不通过清空记录或强杀共享服务绕过这些缺口。官方后端源码与 vendor lock 未修改。安装版及 frozen runtime 仍按 PV-1 延后。

当前影响分类为 `standard`，无依赖、package data、动态导入或打包配置变化。检查计划因已有 M0 脚本及证据文件属于未分类路径，保守选择 backend/frontend/rust 三阶段；不因本轮主要修改 Python 而跳过该计划。

## 首条链路历史检查记录

平台：Windows 11；源码为实施基线加当前未提交候选。检查期间未改产品依赖或安装状态。最终运行模块 SHA-256：

| 模块 | SHA-256 |
|---|---|
| `exec_server.py` | `62264b39ea4de63999fb9c77691090fdfedb5db2287c0798c7be619a74ef371d` |
| `recovery.py` | `9a63cc4eb9f3f8ec10e3587627549b53d6a664545afc66213af065236ae49f2e` |
| `runtime.py` | `309fc0c971016d1bc1f516fc1f59fe73ad6718835588b5c80fc4085f4d650d4b` |

| 检查 | 结果 |
|---|---|
| `scripts/test/run.ps1 agent/tests/test_exec_server_lifecycle.py agent/tests/test_sandbox_recovery_chain.py agent/tests/test_sandbox_recovery.py agent/tests/test_command_reliability.py agent/tests/test_command_runtime.py agent/tests/test_command_lifecycle_probe.py -q` | 最终候选 184 passed，23.14s；覆盖 runtime/授权工具入口 × 身份失效/完整的 4 条链 |
| `scripts/typecheck/run.ps1` | 986 个源文件通过；已同步旧测试对内部请求类型的引用 |
| `scripts/check/run.ps1 --stage backend` | 在全仓 lint 阶段停止：需求 184 的 `artifacts/python-spool-probe.py:50` 有 B023；该文件相对 HEAD 无改动。本轮产品与测试文件局部 Ruff check/format 通过 |
| 全仓 `ruff format --check` | 同一个需求 184 artifact 需要格式化，其余 1212 文件通过；未改写既有证据文件 |
| `scripts/test/run.ps1 -q --tb=short` | 5086 passed、6 skipped、1 failed，1080.20s。此运行在最后准入守卫及 3 条新增用例前启动；最终差异另由上述 184 条回归及完整类型检查覆盖，不把这次记录改称最终全量通过 |
| `scripts/test/run.ps1 agent/tests/test_runtime_dispatch.py::test_cancelled_tool_is_closed_before_same_process_followup -q --tb=short` | 全量唯一失败用例单独复跑 1 passed，10.53s；失败点是 1 秒 join 后线程仍存活。测试使用通用 `_BlockingTool`，不调用 CommandRuntime；测试和通用运行链无 diff。时序不稳定性根因未确认，未修改测试放宽期限 |
| `scripts/check/run.ps1 --stage frontend` | lint/typecheck、201 文件共 1664 tests、production build 全通过；既有 CSS/chunk warnings 保留 |
| `scripts/check/run.ps1 --stage rust` | Tauri Core 测试及 release check 通过；既有 dead-code warnings 保留 |
| `git diff --check`、需求内本地链接、M0 artifact hash | 通过；旧报告和源快照未改写 |

本轮没有执行 Windows 10、macOS、真实沙箱账户/UAC、真实桌面视觉或安装版验收。上述通过项不覆盖这些场景；整体需求维持 IN_PROGRESS，未提交、未合入。
