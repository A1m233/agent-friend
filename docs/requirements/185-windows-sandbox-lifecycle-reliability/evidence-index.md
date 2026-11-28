# 185 · Windows 沙箱执行与恢复可靠性证据索引

## 用途与范围

服务 [需求](./requirement.md) 和 [技术方案](./design.md) 的现状复核、方案比较和验证。当前包含诊断包、固定源码、公开参考、用户确认、[M0 宿主/协议观察](./m0-observations.md)，以及完整 AF 实现的冻结候选和本机门禁结果。故障注入回归中的后端协议与账户修复使用替身；真实账户/UAC、Windows 10 和安装版按用户决定延期，不将宿主专项外推为完整原生沙箱验收。

最近对账：2026-11-29。源码调研基线为 `36605682fd49ecb6fc394926f1db08b4fdc3b4af`；现场构建为 `fe9981a0ec86e2abb4f7e865f12a0cd3262ad942`，两者在最初已检查的 command_runtime 文件上无差异。最终实施基线与冻结输入见 E-040、E-046，验证结果见 E-047。

设计落盘时刷新并 fast-forward 到 `15a400d1cbae6def4118c6fe0e691573a5bad804`。上述调研与现场锚点保留；新基线对账见 E-027，不将新主干描述为历史诊断实际运行版本。

## 当前综合判断

- 现场发生了身份状态再次不完整和两次有界恢复失败；前一天修复成功不能证明随后一直健康。
- 原基线在启动超时后保留未知状态，而修复要求该状态解除，缺少主动核对与收尾路径。隔离检查复现了“手动修复等待，但修复回调未进入”；当前已补来源绑定核对和资源收尾。现场因果链高度吻合，不能把合成检查写成用户原机完整复现。
- 最近的宿主目录权限修复已包含在现场版本。当前问题不能按“旧补丁未安装”解释。
- 固定后端已有查询、启动中登记和终止处理；原基线未接入 `process/read`，当前已接入来源绑定查询。接口存在不等于所有阶段可可靠取消或清理，真实不同身份和提权边界仍按 PV-2 待验。
- 原基线使用固定后端的默认串行调度，长时间启动可以挡住查询与终止请求。当前候选启用并发 2，并限制 AF 普通在途请求为 1，为查询和终止保留容量；其余原生边界仍需分别验证。
- 原基线手动修复的前端 HTTP 等待为 310 秒、恢复协调器预算为 480 秒；当前改为独立后台恢复操作和短请求查询，重连与重复请求关联同一操作。原静态不一致不等于已证明现场恰好发生了 HTTP 超时。
- 首次原生错误和身份文件消失的具体原因尚未确认。上游有主动删除身份文件后重建的代码路径，但不能据此认定现场必经该分支。
- 稳定版 v0.172.0 已绑定精确 commit 并检查启动路径：准备阶段先于 Starting 登记，Starting 的终止仍仅移除记录。因此版本升级本身不足以证明准备可取消；该结论仅覆盖所读路径，不否定新版其他改进。
- 当前生命周期已覆盖命令启动前的文件可见性准备和修复操作；取消登记提前，命令结果与资源收尾独立。无法证明已结束的准备/提权工作继续明确受阻，不用 RPC 返回或根进程退出冒充清理完成。
- 用户先确认打通“故障 → 修复 → 新命令执行成功”，随后要求一次完成整个需求；当前实现与本机门禁完成，待用户验收。只修改 AF，未改 Codex 源码或 vendor lock。PV-1/PV-2 按用户决定延期。
- M0 实测默认串行会阻塞终止，并发后终止可达但普通查询仍可能排队；根命令 closed 后后代可继续存活。该结果仅限报告所用宿主路径，不外推到未执行的沙箱准备/UAC/Windows 10 场景。

## 证据账本

各行保留采集当时的观察与知识状态；E-001～E-039 的实施进度和检查局限属于历史记录，当前结果以 E-040～E-047 和 progress 为准。

| ID | 日期 | 主张与来源 | 观察、局限和锚点 | 证据基础／置信度 | 状态 |
|---|---|---|---|---|---|
| E-001 | 2026-11-28 | 现场版本和诊断覆盖 | 0.4.1 ordinary/formal，build `20261121T181000Z-fe9981a0ec86-9f4286c9`，source dirty=false，Windows 10.0.19045；ZIP CRC 正常，format 1 / revision 23，198 entries，无未知 capability。outcome=partial：执行环境采集失败、历史 native dump 生成失败；会话仅选择一项，日志和事故有各自留存边界 | runtime-observed／confirmed | active |
| E-002 | 2026-11-28 | 修复后再次失效 | `command-runtime-incidents.json`：11/26 22:59:37–42（UTC+8）repair/setup 成功，状态 incomplete→valid；11/27 20:30:07–20:31:07 start 超时，状态 valid→incomplete，users 文件 readable→missing，marker 仍存在。该记录无并发标记，logWindow 有新日志、共享日志非操作绑定等缺口 | runtime-observed／confirmed（状态变化）；首次原因 unconfirmed | active |
| E-003 | 2026-11-28 | 恢复重复等待 | `command-runtime.json`：11/27 20:36:12–20:44:12、11/28 00:54:18–01:02:18 两次 automatic_recovery failed；01:10:28 manual_recovery requested，快照未保留完成。手动请求尚未满 480 秒，不能称其已无限死锁。状态 repair_required / windows_sandbox_state_incomplete | runtime-observed／confirmed | active |
| E-004 | 2026-11-28 | 失败影响其他会话 | `agent_bridge.log` 在 11/27 20:31:07 记录另一会话 exec_command 的 process_start_outcome_unknown；所选会话后续命令得到 windows_sandbox_recovery_failed。首次失败会话未选入 bundle；不能由后一个会话倒推首次命令内容 | runtime-observed／confirmed（事件）；共享阻塞链 strongly-supported | active |
| E-005 | 2026-11-28 | 未知状态阻塞修复 | runtime.py 的 start_process 超时保留 `_uncertain_starts`，`_sandbox_idle` 拒绝这些状态；主要在 process/closed 通知时清除。recovery.py 的手动与自动路径均等 idle，force 仅绕过冷却。exec_server.py 超时删除 pending，迟到 RPC 返回不会再交给原调用方 | code-supported／confirmed；现场机制 strongly-supported | active |
| E-006 | 2026-11-28 | 等待机制的反事实 | 本会话以标准库加载实际 recovery.py/control.py，并从 runtime.py AST 提取实际 `_sandbox_idle`；注入单个未知启动，预算缩至 0.15 秒：手动修复超时，repair 回调次数 0；仅模拟明确关闭后的未知集合解除，repair 回调次数 1。无真实命令、LLM、账户或 production 写入；不是完整集成回归，未留下脚本文件 | reproduction-supported／confirmed（机制） | active |
| E-007 | 2026-11-28 | 既有修复及测试范围 | `e5d53cc1` 宿主目录访问修复、`1f84b2e4` 修复探针与证据、`9f7f9b68` 自动恢复均为现场 commit 的祖先。test_sandbox_recovery.py 验证未知启动不重放与 idle=false，以及忙碌等待超时不 provisioning；未覆盖该状态之后手动恢复的组合链。141 progress 明确保留真实现场/UAC 验收缺口 | code-supported／confirmed | active |
| E-008 | 2026-11-28 | 固定后端可观察与可取消边界 | Codex `rust-v0.147.0` 的 exec-server/local_process.rs 在 spawn 前登记 Starting；read 对 Starting/unknown 返回错误，对 Running 返回保留输出与终态；terminate 对 Starting 移除登记，spawn 后发现登记变化则 terminate 新 session。这个检查位于 spawn 返回之后，不证明准备 helper 已停止，也不能排除其间副作用 | code-supported／confirmed（源码） | active |
| E-009 | 2026-11-28 | 上游身份恢复可能改写现场 | 同 tag identity.rs::refresh_logon_sandbox_creds 先 remove_sandbox_users_file 再 require_logon_sandbox_creds；elevated/runner_client.rs::retry_runner_spawn_once 在特定登录/子进程错误后 refresh 再 spawn，一次恢复错误可替代最初错误。现场缺少首次原生码，不能确认此次执行了该分支 | code-supported／confirmed（代码）；现场归因 unconfirmed | active |
| E-010 | 2026-11-28 | 当前官方实现参考 | 官方 main 查询绑定 `18c09f20ce44d9e2dfb3c77b49a5800231c5ad47`；exec-server/local_process.rs 仍区分 Starting/Running、状态查询、终止与迟到 spawn 处理。只读源码参考，未运行该版本，不将其写成已解决 Windows 准备生命周期 | code-supported／confirmed（所读代码） | active |
| E-011 | 2026-11-28 | Windows 原生进程树管理 | Microsoft Job Objects 文档说明分组管理、后代默认关联及 breakaway 例外、按 job 终止。现有 Tauri 服务监督已使用 job；这不证明命令准备、提权 helper 或不同身份子进程全被同一 job 覆盖。适配前须验证实际归属及影响范围 | documentation／confirmed（平台契约）；AF 方案待验证 | active |
| E-012 | 2026-11-28 | 超时与重试原则 | AWS Builders' Library 区分调用方等待超时与副作用已发生，并讨论有限重试、退避和放大效应。用于检查恢复预算和重复执行边界，不将网络重试策略直接套到本机身份重建 | documentation／confirmed | active |
| E-013 | 2026-11-28 | 用户确认 | 用户要求完整审视链路、检查不合理之处、参考外部实践，并确认编号 185 与范围；允许调整后端接入与共享方式。安装版验证延期到后续打包后，允许合入后继续修复；其余范围确认 | user-confirmed／confirmed | active |
| E-014 | 2026-11-28 | 控制请求可能被启动请求阻塞 | 固定 Codex CLI 的 `--concurrent-requests` 默认 1；exec-server README 明确请求默认串行，request_dispatcher.rs 在 Inline 下直接等待请求完成。AF `_ensure_client` 未传该选项。上游并发模式另外为健康/终止类请求保留 control admission，但 process/read 仍走 ordinary admission；不能仅启用并发就声称所有查询不再饥饿 | code-supported／confirmed（配置与机制）；本次具体请求排队未采集 | active |
| E-015 | 2026-11-28 | 修复请求与执行预算不同步 | 现场版本和当前 frontend commandRuntime.ts 均为 HTTP timeout=310000ms；recovery.py 为 480 秒，Bridge setup route 同步等待完成。当前 setting_up 只在真正进入 setup 时设置，协调等待不在该阶段中。这些事实支持重审异步恢复状态及端到端预算 | code-supported／confirmed | active |
| E-016 | 2026-11-28 | 执行事实与连接归属分散 | CommandProcessSnapshot 默认 running，在 start RPC 确认前即登记；unknown 集合与 snapshot 终态分别维护。断线处理置 `_client=None` 并把未终态命令记为 runtime_lost，同时保留 unknown；新连接的 callback 未携带单独连接 generation。应验证旧事件、重连与保留记录的交互；未把未复现的竞态写成现场根因 | code-supported／confirmed（结构）；具体竞态 unconfirmed | active |
| E-017 | 2026-11-28 | 稳定后端候选与最新源码不同 | GitHub releases/latest 在查询时返回非预发布 `rust-v0.172.0`，发布时间 2026-11-24T00:03:55Z；其说明包含 provisioning 超时/管道错误区分、准备与命令生命周期结合、MXC SDK 迁移等变更。发布资产还包含独立 exec-server 和 sandbox service。仅列为能力比较候选，未下载、运行、替换 vendor 或验证其兼容性 | release-metadata／confirmed；候选效果 unconfirmed | active |
| E-018 | 2026-11-28 | MXC 不能直接覆盖本次 Windows 10 现场 | 当前官方 Windows 文档列出 MXC 的 Windows 11 原生能力门槛，并要求实际探测；同时指出网络本机绑定及前台退出后子进程收尾语义。现场 Windows 10.0.19045 不能由“新版优先 MXC”推断已经有解；必须单独考虑其有效执行路径 | documentation + runtime identity／confirmed | active |
| E-019 | 2026-11-28 | 当前官方 setup 资源所有权参考 | main @18c09f20 的 runtime_ownership.rs 在可能跨 service 生命周期的操作前持久化并 flush 所有权；provisioning_client.rs 区分服务启动预算与实际工作预算。仅作为恢复与清理设计参考，不能把 main 能力默认归入 v0.172.0 或当前 AF 安装包 | code-supported／confirmed（所读源码） | active |
| E-020 | 2026-11-28 | 稳定候选的准备取消仍需补证 | `rust-v0.172.0` tag 解引用到 commit `3d86e43b075adeba2193ec5c22ac92f55afd82e6`。该 commit local_process.rs:381–428 先 await 准备，再插入 Starting；749–775 的 terminate 在 Starting 时移除登记、缺失时返回 running=false。准备期间“未找到”不能证明未开始；未运行候选二进制，不宣称所有后端都存在同一实际阻塞 | code-supported／confirmed（所读路径）；原生行为待验 | active |
| E-021 | 2026-11-28 | 固定版原生资源收尾不等于 RPC 结束 | 固定 commit 的 setup.rs:332–371 以同步 cmd.status 等待非提权 refresh helper；879–974 的显式提权路径使用 ShellExecuteExW 并无限等待。unified_exec/backends/elevated.rs:94–107 用 spawn_blocking 做 runner 启动及凭据 refresh，丢弃外层 await 不证明该工作停止 | code-supported／confirmed | active |
| E-022 | 2026-11-28 | 根进程退出、管道断开与后代退出的不同语义 | 固定版 command_runner/win.rs:434–437 遇输入管道 EOF 退出输入循环；649–671 对正常根进程退出调用 preserve_descendants，对超时才调用 job 终止。现有 process/closed 不能直接当作所有后代均已结束；外层 Job 或监督层须验证其实际覆盖，不能按进程父子名称猜测 | code-supported／confirmed（源码）；AF 外层 Job 覆盖待验 | active |
| E-023 | 2026-11-28 | 准备操作先于命令句柄与取消登记 | 当前 tools.py:888–944 先做 read access / owned read-only workdir 准备，再分配 process_id；runtime.py:1226 的 fs/getMetadata 同样可能进入沙箱准备。工具取消回调在 start_process 返回后的路径注册，启动结果未知则在异常返回后登记收尾句柄。方案需把这些操作纳入同一资源与取消边界 | code-supported／confirmed | active |
| E-024 | 2026-11-28 | 修复预算传播不止前后端两层 | control.py::reserve_recovery_wait 固定增加 480 秒，tool_runtime/executor.py 把该值加入执行预算；recovery.py 又单独维护 flight 与 waiter deadline。任何预算调整须同步调用方、恢复操作、原生收尾与 UI，不能单改 310 秒或 480 秒常量 | code-supported／confirmed | active |
| E-025 | 2026-11-28 | 本地原生监督与持久化复用边界 | Tauri runtime_services/process.rs 已有 suspended 创建、Job 绑定后恢复、检查 Job 成员和有界终止，但它当前监督 Bridge 等服务，不等于 command runtime 已有同等归属契约。sessions/tool_execution_evidence.py 保留输出与 process_status，并要求 complete=false；这是部分输出证据，不可直接改称完整生命周期日志或跨重启执行恢复 | code-supported／confirmed | active |
| E-026 | 2026-11-28 | 整体方案确认与原生能力边界 | 用户在完整方案 review 后回复“可以”，确认统一生命周期、控制请求可达、后台恢复、原生资格前置验证和组合回归；保留共享服务，执行域分开管理。原生能力不足时先提出最小后端改动再确认维护影响；安装版验证继续延期。design.md 已落盘，尚无产品实现或原生专项通过证据 | user-confirmed／confirmed（方案）；原生保证未验证 | active |
| E-027 | 2026-11-28 | 设计落盘后的主干同步 | 初次检查计划因 origin/main 前进而拒绝旧基线；fetch 后确认分支无独有提交、需求 185 路径无冲突，再 fast-forward 到 `15a400d1cbae6def4118c6fe0e691573a5bad804`。相对原基线，command_runtime、tool_runtime、tool_execution_evidence、assembly 及 commandRuntime API/store 无 diff。诊断契约升至 revision 24；ServiceProcess 增加输出收集线程及关闭时 join，Job 创建/绑定主体未改变。设计补明收尾预算包含输出线程；未运行原生专项，不把新日志线程视为已验证的有界收尾 | code-supported／confirmed（diff）；原生行为未验证 | active |
| E-028 | 2026-11-28 | 实施授权 | 用户回复“开始吧”，授权按已确认设计实施。首先执行 M0；安装版延期以及后端能力不足时先确认最小变更范围的边界继续有效 | user-confirmed／confirmed | active |
| E-029 | 2026-11-28 | 真实协议调度与宿主资源边界 | Windows 11 10.0.26200、固定 Codex 0.147.0、未修改 AF ExecServerClient：正式观察 5 cases。默认串行 terminate 等待约 2854.729ms；并发 2 时约 0.434ms；两个普通长轮询饱和后零等待查询仍排队，而 terminate 约 0.421ms 返回。正常根退出 closed=true 时后代仍存活；强制服务退出 case 的宿主 fixture 则已退出。所有 probe Job 末尾活跃进程数为 0。见 m0-observations.md 和 report 02，不把单次时延或宿主行为外推为生产性能或沙箱保证 | runtime-observed／confirmed（所测入口） | active |
| E-030 | 2026-11-28 | M0 首因与准备收尾接口复核 | 再次读取固定 commit 的 local_process.rs:611–637 与 elevated/runner_client.rs:135–145。Starting 终止只移除登记；首次可刷新 spawn 错误进入 refresh 后，返回路径可能只剩 refresh/第二次 spawn 错误。AF 外层拿不到未上报首因，现有回执不提供准备已收尾事实。没有触发真实登录失败或修改产品身份；原事故具体首因仍未知 | code-supported／confirmed（接口缺口）；现场首因 unconfirmed | active |
| E-031 | 2026-11-28 | M0 harness 验证与归档 | 3 个针对性测试通过，覆盖环境隔离、既有报告不覆盖和失败 worker 原生清理；局部 Ruff check/format 通过。报告 01 为 pilot，粗计时、无独立源快照；报告 02 改用 perf_counter 并保存完整 harness/client/lock 字节快照及 hash。完整交付门禁尚未执行，未宣称可合入 | test-supported／confirmed（harness 范围） | active |
| E-032 | 2026-11-28 | 用户对自维护后端补丁的反馈 | 用户问“是说要在codex源码上处理？感觉不是很ok啊”。此前的建议确实指向修改 Codex 源码并维护构建；该建议未获批准，现撤回作为首选。没有修改过上游源码或 vendor lock | user-feedback + workspace state／confirmed | active |
| E-033 | 2026-11-28 | 纠正由接口缺口跳到分叉的推断 | E-029 仅验证宿主/协议，E-030 证明部分原生事实不由当前 RPC 返回；两者没有排除 AF 外层监督或官方升级路径。requirement 已允许无法采集的证据显式标记，因此把取得内部首因当作必须改上游的理由超出了已有证据。保留 E-029/E-030 观察，只撤回“必须修改 Codex”的推断，不取消安全收尾验证 | evidence-boundary review／confirmed（推断缺少支撑） | active |
| E-034 | 2026-11-28 | 官方候选仍应按能力分别评估 | 再读 v0.172.0 @3d86e43b 的 identity.rs 与 runner_client.rs：账户缺失/禁用/密码过期检查、服务 provisioning 路由及特定原生错误的处理有相应实现；普通进程阶段不完整不能用于否定该版本全部原生改进。未运行该候选，不能称其已解决现场问题；版本升级和 AF 外层治理须分别验证 | code-supported／confirmed（所读实现）；候选效果 unconfirmed | active |
| E-035 | 2026-11-28 | 首条恢复链实施授权 | 用户明确要求按已讨论顺序开始实现，优先完成故障、修复及独立新命令成功；不重新请求同范围实施许可 | user-confirmed／confirmed | active |
| E-036 | 2026-11-28 | 修复前的确定性失败 | 新增 JSON-RPC fixture 延迟返回明确 helper 启动拒绝；AF 等待超时后旧实现丢掉回复，手动修复报 wait timed out，repair 回调未进入。`test_sandbox_recovery_chain.py` 首次运行 1 failed，符合目标机制 | test-supported／confirmed；不是原机原生复现 | active |
| E-037 | 2026-11-28 | AF 侧恢复链候选 | RPC 超时保留请求句柄及容量，迟到回复只关联原请求；恢复前核对明确未启动拒绝，无输出/退出等矛盾事实才解除阻塞。不明资源立即报告 blocked，不重放原命令；旧连接不清空新连接，缓存裁剪不遗忘未核实资源 | implementation；验证见 progress | active |
| E-038 | 2026-11-28 | 验证范围与限制 | 覆盖 runtime 及带授权的 ExecCommandTool 入口；测试后端和系统账户修复为替身。pending、断线、迟到成功、未分类错误、输出/退出/closed 均不能被当成安全修复证明。真实 Windows 账户、UAC、跨重启和安装版均未据此验收 | test scope／confirmed；结果见 progress | active |
| E-039 | 2026-11-28 | 准入与最终验证 | 初始化文件完整不再绕过未知旧命令核对；未发出请求不留下未知资源记录。最终相关 184 tests、986 文件类型检查、前端和 Rust 通过。全仓 lint/format 受未改动的需求 184 artifact 阻挡；全量 pytest 唯一失败为通用取消时序用例，单独复跑通过，根因未确认。精确命令、源码 hash、全量与后续定向复验的边界见 progress | test-supported；完整门禁未通过 | active |
| E-040 | 2026-11-28 | 整需求实施授权及基线更新 | 用户要求一次完成整个需求；fetch 后无冲突 fast-forward 到 `03a095667e6088723ef19e2f80f45dea1f06fb7e`，主干已修复旧 184 artifact 的 lint/format 问题。官方 Codex payload 与 lock 保持不变 | user-confirmed + Git facts | active |
| E-041 | 2026-11-28 | Windows Job 与启动身份 | 原生测试在创建时绑定 Job，验证根退出保留后代、显式停止后 active=0、Unicode/stdin；使用虚构凭据的 NETCREDENTIALS_ONLY 调用验证 CreateProcessWithLogonW broker 保留精确 Job 归属。没有真实凭据校验、账户创建、网络访问或 UAC；不外推成不同本地 SID 通过 | runtime-observed，范围见 native tests | active |
| E-042 | 2026-11-28 | 当前生产客户端宿主专项 | [m3-owned-host-protocol-01.json](./artifacts/m3-owned-host-protocol-01.json) 记录官方 0.147.0 的 5 cases，均 observed、最终 Job 活跃数 0。串行 terminate 约 2855.152ms，并发约 0.438ms；两条长读饱和时查询排队、终止仍可达。正常根 closed 后后代仍活跃。源文件字节归档到同名 sources.zip | runtime-observed；完整 sandbox qualification 仍 not_established | active |
| E-043 | 2026-11-29 | 生命周期与恢复集成 | 持久化、早期取消、保留迟到请求、查询、执行域拆分、资源收尾、幂等后台操作、版本取消、API/store 重连及诊断 v3 已接通。普通命令取消仍按稳定 process/run 所有权，不把频繁输出版本变化当作拒绝停止自身任务的理由。完整结果和精确检查命令见 progress | implementation + deterministic tests | active |
| E-044 | 2026-11-29 | 原生环境延期 | 本机 Windows 11 Home 10.0.26200；未发现 Windows Sandbox、Hyper-V、VMware、VirtualBox 或 QEMU 入口。向用户确认后，用户说明没有隔离环境并要求先跳过。PV-2 明确真实账户/不同本地 SID/UAC 与 Windows 10 矩阵未执行，本轮不据此阻塞交付 | user-confirmed + environment inventory | active |
| E-045 | 2026-11-29 | 收尾竞态与回归修复 | 全量检查暴露观察线程登记和启动间的 close 竞态；已用同一锁覆盖登记/启动与关闭快照，并增加 barrier 驱动的确定性回归。后续相关 158 项通过。扩展 fixture 的 metadata 延迟分支曾错误要求旧测试必须传 path，已修复兼容性；最终组合 389 项通过后进入全量门禁 | test-supported；不是用户现场根因 | active |
| E-046 | 2026-11-29 | 冻结候选与跨平台边界 | 新增测试先重现了已关闭 owner 仍可写入、非 Windows ACL 准备误入 Windows 恢复两处问题；修复后存储/准备模块 12 项通过。[candidate-01.json](./artifacts/candidate-01.json) 和同名 sources.zip 保存本轮冻结代码与测试 overlay，后续验证结果绑定该输入；不把只做 POSIX 路由测试说成 macOS 实机验收 | test-supported；完整结果见 progress | active |
| E-047 | 2026-11-29 | 最终本机门禁 | 冻结候选后 backend 的 lint/format、998 文件类型检查及 5111 passed / 6 skipped 通过；frontend 的 1668 tests、静态检查与生产构建通过；Rust 367 + 1 + 7 tests、2 ignored 与 release check 通过。专项和精确命令见 [验收记录](./progress.md)。49 个代码/测试文件 hash 与 artifact 清单已核对；PV-1/PV-2 未执行，不冒称通过 | test-supported／confirmed（本机范围） | active |

## 公开来源

- 固定后端协议：[exec-server README](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/exec-server/README.md)。
- 固定后端状态与取消：[local_process.rs](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/exec-server/src/local_process.rs)。
- 固定后端调度：[request_dispatcher.rs](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/exec-server/src/server/request_dispatcher.rs)；[CLI 参数](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/cli/src/main.rs)。
- 固定后端身份重建：[identity.rs](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/windows-sandbox-rs/src/identity.rs)；[runner_client.rs](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/windows-sandbox-rs/src/elevated/runner_client.rs)。
- 当前源码对照：[local_process.rs @ 18c09f20](https://github.com/openai/codex/blob/18c09f20ce44d9e2dfb3c77b49a5800231c5ad47/codex-rs/exec-server/src/local_process.rs)。
- [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。
- 本轮原生接口核对：[Job 进程列表（包含嵌套 Job）](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_process_id_list)、[进程句柄与标识符](https://learn.microsoft.com/en-us/windows/win32/procthread/process-handles-and-identifiers)、[GetTickCount64](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-gettickcount64)、[CreateProcessWithLogonW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createprocesswithlogonw)。当前实现用存活句柄区分执行服务基线与后来业务资源；只以 uptime 严格下降判断已发生重启；NETCREDENTIALS_ONLY 测试不代表真实账户验证。
- [AWS Timeouts, retries, and backoff with jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/)。
- [Codex v0.172.0 release](https://github.com/openai/codex/releases/tag/rust-v0.172.0)。
- [当前 Windows sandbox 文档](https://learn.chatgpt.com/docs/windows/windows-sandbox)。
- 当前 main 的 [runtime_ownership.rs](https://github.com/openai/codex/blob/18c09f20ce44d9e2dfb3c77b49a5800231c5ad47/codex-rs/windows-sandbox-rs/src/runtime_ownership.rs) 与 [provisioning_client.rs](https://github.com/openai/codex/blob/18c09f20ce44d9e2dfb3c77b49a5800231c5ad47/codex-rs/windows-sandbox-rs/src/provisioning_client.rs)。
- 稳定候选精确源码：[local_process.rs @ 3d86e43b](https://github.com/openai/codex/blob/3d86e43b075adeba2193ec5c22ac92f55afd82e6/codex-rs/exec-server/src/local_process.rs)。
- 稳定候选身份与错误处理：[identity.rs @ 3d86e43b](https://github.com/openai/codex/blob/3d86e43b075adeba2193ec5c22ac92f55afd82e6/codex-rs/windows-sandbox-rs/src/identity.rs)、[runner_client.rs @ 3d86e43b](https://github.com/openai/codex/blob/3d86e43b075adeba2193ec5c22ac92f55afd82e6/codex-rs/windows-sandbox-rs/src/elevated/runner_client.rs)。
- 固定版原生收尾：[setup.rs](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/windows-sandbox-rs/src/setup.rs)、[elevated backend](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/windows-sandbox-rs/src/unified_exec/backends/elevated.rs)、[command runner](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/windows-sandbox-rs/src/bin/command_runner/win.rs)。

上述源码通过 GitHub connector 获取；Context7 用于发现协议文档，关键语义已回到固定版本源码核对。官方 `rust-v0.147.0` annotated tag object 为 `3ed6f04f6bf8b7c46299d1cb1ff99c74ce21a51d`，进一步解引用得到源码 commit `be6e8eac029b183056b7e4402879f15d2c85f61b`。`rust-v0.172.0` 当前仅记录 tag object `fc3036dba44468096953f0e832f3fecd3adc0228`；不能把 tag object 或 main 比较 commit 当成候选 release 的源码 commit。

后续锚点补充：E-020 已将 v0.172.0 tag 解引用到 `3d86e43b075adeba2193ec5c22ac92f55afd82e6`，并读取该 commit 的 local_process、runtime_ownership、provisioning_client、runner_client。该补充替代上段“仅记录 tag object”的知识状态，不改写此前只读取 main 与 release metadata 的历史。候选 request_dispatcher 本轮获取遇 transport error，没有据此新增候选调度结论；E-014 继续只绑定固定版已读取源码。

## 未决问题与下一项证据

| 问题 | 当前局限 | 最有区分力的后续证据 |
|---|---|---|
| 用户现场身份为何再次失效 | 首次原生登录/创建失败没有保留，日志不独占归属于该操作 | 同一操作的原生错误、身份刷新原因、前后状态和 helper 生命周期 |
| 取消准备/启动是否阻止迟到副作用 | AF 的早期取消和迟到收尾已有确定性覆盖；真实不同身份/UAC 未执行 | PV-2 中观察受控准备阻塞、取消后释放、子进程/副作用及清理结果 |
| 服务失联后旧工作如何收尾 | 已实现来源实例隔离、持久化和 Job 所有者，宿主专项通过；真实沙箱边界未验 | PV-2 中对不同身份进程执行服务失联和重启故障注入 |
| 是否需后端升级或改变共享边界 | 当前保留官方 0.147.0，拆分 AF 沙箱/宿主执行域；未做候选原生能力比较 | 仅在后续实际资格缺口需要时按同一保证比较官方候选 |
| 控制请求是否始终可达 | 当前预留查询和终止容量，确定性与宿主协议专项通过；真实准备阶段未验 | PV-2 中覆盖真实长启动与取消，保留各自预算和资源事实 |
| 修复状态是否与界面一致 | 后台 API、重试键、断线重连及旧响应隔离回归通过；未做真实桌面视觉验收 | 用户手验或后续明确授权的桌面验收，观察恢复进度和取消后的收尾 |
| 安装版能否满足同样保证 | 用户明确将这部分延期 | 后续打包后执行 PV-1，不作为本次合入前门槛 |
| 原生监督能否覆盖完整生命周期 | 创建时 Job 绑定及宿主后代归属已实跑，未证明真实不同身份 runner、提权 helper 的完整覆盖 | PV-2 隔离资格专项；不足时明确缺少哪项可观察保证 |
| 第一次原生错误能否完整保留 | AF 保留首次可观察错误；上游未返回的内部首因仍不可知 | 后续同一操作的上游原生错误证据；不能用共享日志时间窗口推断替代 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 |
|---|---|---|
| 系统性处理完整生命周期与恢复，而非单改等待时间 | E-002～E-010、E-013、E-026、E-040～E-047 | 实现和本机门禁完成；整体 IN_PROGRESS 仅保留用户验收，PV-1/PV-2 明确延期 |
| 必须区分未知业务结果、资源清理与环境恢复 | E-005、E-008、E-012、E-026、E-043 | 已实现独立事实、持久化与统一状态转换，未知业务不重放 |
| 不预设升级版本即可解决 | E-008～E-010、E-040 | 保留官方 0.147.0；未升级、分叉或修改 lock |
| 后端选型须同时满足准备可收尾和资源归属 | E-020～E-022、E-025～E-026、E-041～E-044 | AF 原生所有者与宿主专项已落地；完整不同身份/UAC 资格按 PV-2 延期，未知资源继续受阻 |
| 准备操作与预算传播需要跨入口处理 | E-015、E-023～E-024、E-026、E-043 | 准备前取消登记、唯一恢复预算和后台状态投影已实施并回归 |
| 安装版验证后移 | E-013 | 用户确认；后续需明确标注未执行，不冒充通过 |
| 隔离原生环境验证后移 | E-044 | 用户确认没有测试环境并先跳过；PV-2 不阻塞本轮源码交付 |

## Artifact 映射与隐私

- 原始诊断：用户本次提供的 `agent-friend-diagnostics-20261128-011318(1).zip`，仅在持有人本机微信接收文件目录可访问；不将私人路径、原包、会话正文、截图或凭据写入仓库。
- ZIP SHA-256：`aebfb562a38a445962325bb18f4a7867a49a14d44af4ac9092010341a180e35c`。本次首次结构检查包含 CRC；后续定向读取复用该检查，不把未再次校验误写为新证据。
- E-001～E-004 原始入口：`diagnostics/application.json`、`diagnostics/command-runtime.json`、`diagnostics/command-runtime-incidents.json`、`logs/agent_bridge.log`。会话时间按 UTC+8 转换。
- E-006 的命令和输出保留在当前 Codex 会话；无独立长期 artifact，后续实现阶段需固化完整组合回归。不得将后来新增测试冒写为当时已运行测试。
- 临时解出的截图在诊断结束时已删除；本需求不再处理桌宠问题。
- E-008～E-012 使用上述公开固定源码和文档链接；当前 main 比较仅绑定已读取的 commit，不以浮动 main 作为验收版本。
- E-029～E-031：[M0 观察记录](./m0-observations.md)、[正式报告](./artifacts/m0-host-protocol-02.json)、[源文件快照](./artifacts/m0-host-protocol-02.sources.zip) 与 [完整性清单](./artifacts/MANIFEST.sha256)。源快照是当时实际未提交 overlay，不冒充已提交候选。首次 pilot 保留其结果和 hash，但未归档的旧 harness 不声称可逐字重建。

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
|---|---|---|
| 2026-11-28 | 现场构建、历史修复、等待机制、官方协议、需求范围与用户修订 | 建立索引；区分机制复现与现场推断；安装版验证记为延期；无产品代码修改 |
| 2026-11-28 | 请求调度、修复预算、连接归属与候选能力 | 增补 E-014～E-019；“补查询即可恢复”的推断不成立；固定后端版本、浮动 main 快照与稳定 release 候选分别绑定；技术方案仍未确认 |
| 2026-11-28 | 继续方案调研：稳定候选精确源码、原生收尾、启动前准备与预算消费者 | 增补 E-020～E-025；不把升级、RPC 取消、管道断开或根进程退出视作完整收尾证明；沿用安装版延期，无产品代码、系统身份或安装状态修改 |
| 2026-11-28 | 用户确认整体方案，落盘 design.md | 增补 E-026 并更新当前决定；原生能力保留为 M0 待验证，安装版仍延期，尚未进入产品实现 |
| 2026-11-28 | 文档检查期间主干前进，重新核对基线 | 增补 E-027；保留旧调研锚点，按新基线的诊断契约与输出收集逻辑准备后续实施；只 fast-forward，无冲突解决或产品代码修改 |
| 2026-11-28 | 用户授权实施，完成 M0 宿主/协议专项与固定源码边界复核 | 增补 E-028～E-031；报告、源快照和 hash 已归档，全部 probe 进程回收。原生接口缺口触发方案回溯；没有包装验证、生产身份修改或后端替换 |
| 2026-11-28 | 用户质疑修改 Codex 源码，复核已有需求及推断边界 | 增补 E-032～E-034；保留实测事实，撤回首选分叉建议，不再把批准上游补丁作为继续工作的条件；未改生产代码或个人规则 |
| 2026-11-28 | 用户再次明确首条链路优先，开始产品代码修改 | 增补 E-035～E-038；修复前失败已重现，候选保留迟到回复并接入恢复核对；当前验证结果及遗留缺口统一记入 progress，不改写 M0 报告与源快照 |
| 2026-11-29 | 完成整体实现、冻结候选和本机门禁 | 增补 E-040～E-047；同步当前判断与实施状态，保留历史观察。用户确认 PV-2 延期；后端、前端、Rust 和宿主专项通过，待用户验收 |
