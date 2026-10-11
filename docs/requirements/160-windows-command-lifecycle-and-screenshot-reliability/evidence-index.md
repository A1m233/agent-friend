# 160 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)。
- 记录 2026-10-10 截图事故的既有诊断、固定版本源码和受控函数检查；不复制完整会话、用户图片或原始日志。
- 日期与时刻采用 UTC+08:00；历史观察不能当作当前进程状态。
- 最近对账：2026-10-11，已实施截图输出与图片展示、完成确定性检查和桌面交互复验；模型评测按用户选择暂缓，Rust 门禁在需求 159 构建占用结束后已补跑通过。历史结论按下方时序保留。

## 当前综合判断

本次首次未恢复的失败发生在命令准备链路：申请用户目录根可写，准备进程执行目录权限传播，启动确认在 60 秒后超时。现场文件句柄证明准备进程进入用户目录下开发工作树的依赖子树，运行时已结束对话但准备进程仍存活。

固定后端的启动取消不直接终止准备 helper，属于需要补齐的生命周期边界。内置截图脚本默认目录函数存在另一个已复现的变量冲突；这不是本次 60 秒启动超时的解释。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 |
|---|---|---|---|---|---|---|---|
| E-001 | 2026-10-10 | 命令准备超时且未交付图片 | runtime-observed | AF 0.4.1，源码 `2de2b4bded82b88475992a4ceae00b738518fb99`，约 01:38:58–01:41:49 | Skill 激活成功；一次 exec 请求携带 home 读写范围；60.265 秒后返回 `process_start_outcome_unknown`；后续图片目录查询为空 | confirmed | active |
| E-002 | 2026-10-10 | 准备进程正在处理用户目录的权限子树 | runtime-observed + code-supported | Codex 0.147.0，现场 helper PID 65484、父 PID 53804，仅作历史定位 | 日志记录 home 写 ACE 任务；两次 CPU 观察持续增长；只读句柄采样包含 home、工作树、node_modules 与 .pnpm 中的具体目录/文件，包含 WRITE_DAC 访问；固定源码以可继承 DACL 更新写根 | confirmed | active |
| E-003 | 2026-10-10 | 启动取消与准备 helper 生命周期脱节 | code-supported + runtime-observed | 上游 commit `be6e8eac029b183056b7e4402879f15d2c85f61b` | Starting 取消仅移除登记；helper 使用同步 status 等待；启动返回后才检查取消并终止业务进程；现场回合结束后 helper 仍存活 | confirmed（源码机制与残留事实）；具体取消 RPC 回执未单独保留 | active |
| E-004 | 2026-10-10 | 默认路径函数报 HOME 只读变量错误 | controlled-comparison + code-supported | 截图脚本 SHA-256 `a31a2242e41fd1c5471651afbcced660f98fbc884f11e531218348f700a3f826` | 使用安装包 PowerShell，通过 AST 仅提取并调用 Get-DefaultDirectory，返回 VariableNotWritable；未运行截图、未写入图片；当时 10 份本机内置脚本副本均与此摘要一致 | confirmed | active |
| E-005 | 2026-10-11 | 新需求基线与事故基线区分 | code-supported | 新分支基线 `a4a6b935a8c38fdf19a26ff727f7a9009726e256` | 从 origin/main 创建需求分支；事故版本到此基线的 command_runtime、tool_runtime、screenshot 资源及 codex.lock.json 无差异 | confirmed | active |
| E-006 | 2026-10-11 | 保留沙箱截图路径有历史可用性依据 | historical runtime evidence | [139 证据索引](../139-builtin-pdf-and-screenshot-skills/evidence-index.md) E-012、E-021 | 既有正式工具、审批与沙箱 smoke 曾输出可解码 64×64 PNG，E-021 记录 885 bytes、launch_seconds=0.734 | confirmed（历史记录范围） | active |
| E-007 | 2026-10-11 | 新版 legacy ACL 路径仍保留准备取消缺口 | code-supported | 官方 `rust-v0.162.1`，release published_at=2026-10-09T19:44:51Z | 检查相同两个上游文件，Starting 取消仍移除登记，setup refresh 仍同步 cmd.status()；没有本机运行该版本。结论仅限 legacy 路径，不能排除新版 MXC 路径 | confirmed（所检查代码路径） | active |
| E-008 | 2026-10-11 | 看图与向用户交付不是同一已实现契约 | code-supported | `view_image/tool.py`、`image_reading/service.py`、`session_files/tool.py` 与前端 ToolCard | view_image 校验并导入不可变图片资产，返回 ToolResult.images；session_files 仅 list/prepare 用户上传文件。未找到可据此宣称已具备显式生成图片发布闭环的公共入口 | strongly-supported | active |
| E-009 | 2026-10-11 | 当前 Codex Windows 有 MXC 优先路径，不能只研究 legacy | official-documentation + code-supported | 0.162.1 精确 commit `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`；官方 Windows sandbox 文档 | 官方说明兼容设备上的 consumer desktop 优先 MXC，legacy elevated 为回退；公开代码支持 WindowsMxc；Environment 复用 Arc<dyn ExecBackend>，LocalProcess 通过 process ID 管理多条命令，没有据此要求每条命令独立 exec-server | confirmed（文档与所查代码）；AF 集成未验证 | active |
| E-010 | 2026-10-11 | 对齐审批时必须分开权限范围和审批策略 | official-documentation + code-supported | 官方 Agent approvals & security；AF approvals/broker.py | Codex 区分 sandbox 与 approval policy，never 可搭配不同 sandbox 模式；auto_review 只审查本来需要审批的动作。AF session_auto 是现有边界内的逐请求自动决定，不等于取消沙箱 | confirmed（文档语义与本地机制） | active |
| E-011 | 2026-10-11 | 上游截图 Skill 不要求 home 写权限，也没有声明自动收窄保证 | code-supported | 当日读取 openai/skills main 的 screenshot/SKILL.md 与 take_screenshot.ps1 | 用户指定路径优先；未指定时使用 OS 默认位置；自检截图用 temp；默认位置失败时建议 escalated permissions。默认目录尝试 Pictures/Screenshots、Pictures，最后才回落用户根；脚本本身不申请工具权限 | confirmed（公开 Skill 及脚本）；无 Desktop 截图调用 trace | active |
| E-012 | 2026-10-11 | 版本升级和启用 MXC 是两个独立兼容面 | code-supported | 0.147.0 与 0.162.1 protocol.rs、0.162.1 file-system/lib.rs、mxc-sandbox/lib.rs，对照 AF runtime.py/tools.py | initialize 与 process/start/write/terminate 及 output/exited/closed 名称保留；Windows 字段内部改名但 wire 仍为 windowsSandboxLevel。AF 显式传 elevated 且依赖旧 readiness/ACL 准备；MXC managed network 要求 allowLocalBinding=true，与现有继承代理路径的 false 不同 | confirmed（静态接口差异）；未运行新版二进制 | active |

## 图片读取与用户展示补充（2026-10-11）

- 官方 [Image inputs](https://learn.chatgpt.com/docs/image-inputs) 将图片作为模型检查和截图验证的输入；该页面没有规定独立的图片发布工具或本地图片持久保存机制。
- [0.162.1 view_image 实现](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/core/src/tools/handlers/view_image.rs) 在沙箱文件权限下读取并校验图片，向模型返回 InputImage，同时发出 ImageView 开始/结束事件。因此“模型看图”不能被描述为对用户完全不可见；闭源 Desktop 如何渲染这些事件不由此源码确定。
- 本任务当前 Codex Desktop 提供的图片展示契约明确支持回复中的 Markdown 图片语法及本地绝对文件路径。这是当前客户端的能力契约，没有据此实测文件删除或重启后的持久性，也不能推断所有 Codex surface 相同。
- 方案影响：显式向用户展示不必等价于新增 `session_files` 发布动作。优先评估回复中的图片引用由宿主完成授权校验、资产快照与持久化后渲染，保留 view_image 的模型检查职责；这是待 review 的方案方向，尚未定稿或实现。既有 AC 对持久交付的要求不变。
- 随后的用户 review 明确选择由 harness 自动呈现 `view_image` 成功结果：默认折叠为“已查看一张图片”，展开缩略图、点击放大。这一决定覆盖上一条待 review 的“由回复引用驱动展示”候选，也不再增加独立发布动作。用户对 Codex 界面的描述作为期望交互记录，不冒充本次现场观察。
- 当前本地代码支撑：`ImageReadingService.snapshot` 已校验并导入资产，`conversation.py` 已在持久化 `tool_call_result.images` 中存储引用，bridge attachment reconciliation 已消费这些引用；但 `conversation_events.ToolCallResult` 不含图片字段，AG-UI encoder 仅发文本结果，前端 `ToolBlock` 与实时/历史投影未承载图片。因此需要补齐事件和投影链路，不能只给现有工具卡片加一个图片标签。
- 复用入口：`frontend/src/pages/chat/components/AttachmentImageViewer.tsx` 已提供项目图片放大组件，`MessageAttachments.tsx` 已使用它；本次无需新建放大弹层或引入新依赖。以上为源码核对，未启动桌面端或执行视觉验收。

## 实现验证补充（2026-10-11）

- `scripts/check/run.ps1 --stage backend`：lint 与类型检查通过，4089 passed、5 skipped，退出 0。保留第三方 asyncio 清理及 zip fixture warning，不将这些警告写成新功能失败。
- `scripts/frontend/test.ps1`：180 test files、1475 tests 通过。`scripts/frontend/lint.ps1` 的 ESLint、颜色、来源、token 和类型门禁通过；`scripts/frontend/build.ps1` 通过，已有 highlight CSS 与 chunk size 警告。
- `scripts/screenshot-smoke/run.ps1` 使用产品包 `20261009T153551Z-2de2b4bded82-44284a15` 的 Codex 0.147.0 与 native PowerShell，通过当前源码正式 ExecCommandTool / session_auto / Skill binding 截取 64×64 区域。使用已有沙箱配置，没有初始化、提权、LLM 或宽根申请。成功报告位于系统临时目录 `agent-friend-screenshot-smoke-0d5xi11c/report.json`：launch 0.141s、运行 1.734s、exit 0、PNG 3416 bytes；view_image 校验导入成功，删除原文件并清理回合后资产仍可读，SHA-256 `ba0551a90c2f84dbea34e1476752c983628c7bbacd0f97c498dd0d88722981ca`。
- 前两次 smoke 分别暴露测试 runner 的构造参数错误（未启动命令），以及临时 fixture 的 OWNER RIGHTS 继承导致沙箱创建文件后宿主不可读。第二次截图本身启动 0.656s、退出 0，复用已生成图片定位到 canonical_path 的 WinError 5；未原样重复截图。后续只给新建的空 fixture 根增加当前宿主 SID 的可继承权限，模拟产品正常会话目录的权限条件后完整链路通过。没有修改既有产品目录 ACL、历史 helper 或 vendor。
- Skill quick_validate 与 guidance audit：通过，0 errors / warnings；builtin manifest 改为明确记录兼容补丁，最终 LF 资源摘要为 `edb21da3a3f8942a2ce667301a9a71926b7fbb3658064c9956dea033e3f0b0e8`。后补 LF 归一化仅修正字节一致性，另跑 manifest/脚本局部测试。
- 桌面验收已获用户授权；安装版原先占用单例，经用户自行退出后启动本分支 Tauri。隔离 data/log root 为系统临时目录 `agent-friend-160-visual-20261011`；其中 `visual-fixture.json` 记录从上述真实截图导入的资产及人工构造的历史事件，session `0cc3c9b2-dadc-458e-9627-fe72dc80e186`。此 fixture 只验证桌面投影和交互，不代表真实模型执行、实时后端推送或完整产品 E2E。

## 稳定源码锚点

最终前端 gate：`scripts/check/run.ps1 --stage frontend` 退出 0，180 test files / 1476 tests，lint、typecheck 与生产 build 全部通过，耗时 220.5s。Rust stage 两次均在测试启动前被构建存储门禁拦截；最近一次盘点的 unknown/running 对象来自需求 159 的打包 operation，未干预该任务。安装版当前运行，先前保留的 dev 已自行结束，未将安装版用于验证当前源码。

行为回归补充：新增 C24-13、C17-07，现有 C24-03 作相邻对照；eval 158 项通过，图片读取相关 14 项通过，最终 lint/typecheck 通过。原事故用户请求为“听说你能截图，你来截个图我看看”；派生 fixture 保留此请求与关键回执，但不复制长会话和用户图片。原事故 scope 过宽与 helper 生命周期是 runtime 问题；unknown 后未重复截图是应保留的正确行为，不写成模型原先重试。图片 fixture 仅元数据，自动判分覆盖工具顺序、引用与终止行为；宽根参数和最终语义仍需真实 transcript 人工复核。

模型 provenance：原会话 model_config_change position 39833 指向 `custom/e460216e-71da-42cb-a252-03ecaf1aa2bd/8d0327f8-a306-412a-9796-ddb22aaa122a`、`effort:medium`，直到事故 run position 53609 无后续模型变更；当前配置只读解析为 `openai/gpt-6.1-sol` / Responses。原始自定义 endpoint 配置未独立存档，不把当前解析值当作历史配置逐字不变的证明。运行计划与 case-set digest 见 `eval-run-plan.json`，用户明确暂不调用，三项均 pending，无真实评测报告。

桌面复验补充：2026-10-11 13:45，当前 worktree 的 Tauri app PID 36096、lease PID 11664，实际 WebView2 `chat.html`。按用户纠正移除独立图片卡片外壳，使用原有 ToolActivity/ToolCard；单图仅一个折叠入口，结果区域缩略图、现有放大器关闭、刷新后恢复通过。证据在临时目录 `agent-friend-160-visual-20261011/reused-collapsed.png`、`reused-expanded.png`、`reused-interaction.json`。这是实际桌面 WebView 截图和交互、人工构造历史事件，没有真实模型调用或实时端到端声明。Windows 原生截图脚本未实现，未把 CDP 截图称为含系统边框的原生全窗口截图。dev 按用户要求保留运行。

- AF 历史源码：`2de2b4bded82b88475992a4ceae00b738518fb99`。
- `agent/src/agent/command_runtime/runtime.py`：`start_process` 等待 60 秒，未知启动登记；`terminate` 的终止确认路径。
- `agent/src/agent/command_runtime/exec_server.py`：`request` 等待超时移除 pending，不等同停止远端准备操作。
- `agent/src/agent/command_runtime/tools.py`：未知启动注册收尾回调，Skill binding 不支持 host_user。
- `agent/src/agent/tool_runtime/context.py`：`close_commands` 的回调和资源清理。
- `agent/src/agent/skills/builtin_assets/screenshot/scripts/take_screenshot.ps1`：默认目录函数与 CopyFromScreen。
- [固定版上游准备入口](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/windows-sandbox-rs/src/bin/setup_main/win.rs)：并发写根授权、等待任务、后置 deny-write carveouts。
- [固定版上游 ACL 实现](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/windows-sandbox-rs/src/acl.rs)：可继承 allow ACE 与 SetNamedSecurityInfoW。
- [固定版上游 helper 等待](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/windows-sandbox-rs/src/setup.rs)：refresh 同步等待 helper。
- [固定版上游启动取消](https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/exec-server/src/local_process.rs)：Starting 分支与 spawn 返回后的取消检查。
- [0.162.1 上游启动取消](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/exec-server/src/local_process.rs)与 [0.162.1 helper 等待](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/windows-sandbox-rs/src/setup.rs)：E-007 的候选源码核对，不代表正式升级验证。
- [Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)：官方文档说明进程组终止、子进程归属、nested jobs 与 breakaway 边界；只能作为方案依据，不能替代本产品真实沙箱兼容性验证。
- [当前官方 Windows sandbox 文档](https://learn.chatgpt.com/docs/windows/windows-sandbox)：MXC 自动选择与显式选择、系统兼容门槛、legacy 回退、网络及子进程生命周期限制。
- [当前官方审批文档](https://learn.chatgpt.com/docs/agent-approvals-security)：权限范围、审批策略、never 与 auto_review 的不同职责。
- [0.162.1 Environment 后端复用](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/exec-server/src/environment.rs)与 [LocalProcess](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/exec-server/src/local_process.rs)：公开本地执行路径依据，不能反推闭源 Desktop 的全部内部进程拓扑。
- [0.162.1 执行协议](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/exec-server-protocol/src/protocol.rs)、[沙箱 wire 字段](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/file-system/src/lib.rs)、[MXC 网络校验](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/mxc-sandbox/src/lib.rs)：升级风险盘点，不是协议实测通过。
- [公开截图 Skill](https://github.com/openai/skills/blob/main/skills/.curated/screenshot/SKILL.md)：E-011 是 2026-10-11 的 main 读取观察；原始引入版本仍以 builtin manifest 为准，不把 main 当不可变锚点。

## 身份与完整性

- vendor：`rust-v0.147.0`，Windows archive SHA-256 `a1c073ede409178502b1ff11bed635d2b644424352f9d4f3999de8272c6aebbd`。
- 现场 identity 与事故记录中的 setup helper SHA-256：`a4df86996dfbb218d96d73a80606d89b742dfa4ddd3470614e90dde89e3250a3`。
- 未独立重建上游二进制；版本与源码绑定依据产品 vendor identity、release tag 及对应 Git tree。
- 后补的需求基线不替代实际事故版本，不把历史观察改写为新版本复现。

## Artifact 映射与复查边界

| 证据 | 原始位置或查询 | 归档与完整性 | 敏感性与局限 |
|---|---|---|---|
| E-001 | `agent.paths.sessions_dir()` 下事故会话业务索引；`agent.paths.log_dir()/agent_bridge.log`，按上述时间窗筛选 | 当前会话已有工具输出；仓库仅保存脱敏摘要，未复制全文或新增原始文件 hash | 私有日志可能轮转；原会话 1.2 GB 量级，使用只读 SQLite 最近事件查询，不要求全量打印 |
| E-002 | 产品 `command-runtime/codex-home/.sandbox/sandbox.2026-10-09.log` 约 01:39:31；NtQuerySystemInformation、DuplicateHandle 与 GetFinalPathNameByHandleW 过滤历史 PID | 未保存二进制进程转储；保留上述路径类别与观察摘要 | 不读取文件内容、不注入进程、不修改 ACL；当前 PID 可能消失或复用，不可直接终止 |
| E-003 | `agent.paths.log_dir()/command-runtime-incidents/5e26b8fde06f49bd8e8f5341fe461c0b.json` 与上游源码 | 事故记录当时表明 timedOut=true、report missing、shared-log attribution 有缺口；未复制原始 JSON | 不据此声称每个底层 API 调用都已追踪；终止具体回执仍缺失 |
| E-004 | 本会话一次性 AST 函数检查，安装包提供的 pwsh | 脚本摘要见 E-004，错误为 Cannot overwrite variable HOME because it is read-only or constant | 首次尝试读取已清理的 run 快照未取得函数，不算有效复现；随后对哈希一致的仓库脚本函数检查才算有效结果 |

## 未决问题

| 问题 | 下一项有区分力的证据 | 阻塞情况 |
|---|---|---|
| 收窄目录后当前版本正式沙箱截图能否完成完整交付 | 历史小区域成功已见 E-006；需当前 Windows 正式入口截图，校验目标、解码与用户展示 | 阻塞 AC-1，不再以缺少任何历史实测为由假定必须增加宿主截图工具 |
| 当前 Codex 路径如何接入 AF，并覆盖准备取消 | E-007 仅限 legacy；按 E-009 评估新版协议、MXC 能力选择、截图桌面访问、网络和取消兼容性；不先锁定一命令一 exec-server | 已由用户确认移交 issue 070 对应后续需求，不阻塞修订后的 160；尚未实现或实测 |
| 模型查看图片和向用户持久交付是否已有完整共用入口 | 会话图片资产、输出链接与清理链路盘点 | 阻塞图片生命周期方案定稿 |
| 原请求已写入哪些 ACL、现场现在是否残留进程 | 单独授权的现场身份复核、只读 ACL 快照与归属核对 | 不阻塞需求；禁止据历史 PID 或缺失基线盲目回滚 |

## 决策影响

- E-001、E-002 支持收窄普通输出授权及启动前的确定性边界治理，不支持一刀切禁用合法宽范围任务。
- E-003 支持将准备阶段纳入取消和收尾；仅延长超时不能满足需求。
- E-004 支持必要最小兼容补丁；不把脚本 bug 当成本次启动超时根因。
- 2026-10-11 用户确认需求 160 与最小兼容补丁边界；尚未确认技术方案或授权产品代码实现。
- 2026-10-11 用户在方案 review 中要求权限处理对齐 Codex；撤回默认新增宽根人工审批、一命令一执行服务的预设方案。需求正文不曾确定这些实现，保持原确认状态；技术方案尚未落盘。
- E-009 收窄此前“升级不能解决”的表述：只能确认 0.162.1 的 legacy ACL 准备代码仍有该缺口；MXC 是需要单独验证的候选路径。只读系统版本查询为 Windows 25H2 build 26200.9457，满足官方列出的版本下限，但未做实际能力探测，不能宣称本机或 AF 已可用。
- E-011 不支持“Codex 绝不会申请整个目录”或“它已自动解决相同 ACL 事故”的结论；同样的 legacy 宽根授权仍需单独评估。提权重跑与给沙箱新增整个 home 可写不是同一种执行行为。
- E-012 支持把候选版本兼容验证与 MXC 接入分开评估；只更换 vendor 版本不会自动改变 AF 显式传入的 elevated 选择。内部 Rust 字段改名不是 wire breaking change，不据此夸大升级工作量。
- 2026-10-11 用户决定把 Codex 升级与 MXC 接入延期，先登记 [issue 070](../../issues/070-codex-upgrade-and-mxc-integration/README.md)，以后单开会话作为独立需求推进。已登记技术债和续办入口，本次没有创建后续会话或替换运行时。
- 拆分后的依赖核对：截图路径、脚本兼容及图片交付可独立推进；现有 160 AC-4、AC-5 的准备取消和并发隔离保证仍有底层缺口。建议将底层保证交由后续需求承接，但此范围调整尚待用户确认，原 AC 未删除、未标通过；不能把“无需先升级就能修截图”扩大为“原 160 全部验收不依赖底层修复”。
- 本轮复查 `ToolRunContext.close_commands`、`ExecServerClient.request` 和工具启动路径，确认 AF 可独立改善取消登记、未知状态与资源清理的表达，但这些改动本身不等于能终止上游准备 helper。复查 `ImageReadingService.snapshot`、conversation 工具事件写入及 bridge attachment reconciliation，已有图片校验、资产导入和事件引用保留可复用；显式用户交付仍需单独设计，不把模型看图自动等同用户发布。
- 2026-10-11 用户随后明确回复“确认”，批准上述范围拆分。已修订 requirement 与 issue 070：原 AC-4、AC-5 以移交记录保留，未标通过；160 保留图片生命周期、失败真实表达及现有授权模式。覆盖此前“范围调整尚待确认”的状态，不改变历史诊断。技术方案仍待形成与确认。

## 对账记录

| 日期 | 对账范围 | 结果 |
|---|---|---|
| 2026-10-11 | 既有诊断、来源身份、最新主干相关文件与需求范围 | 保存历史与当前基线区别；记录原始材料访问与缺口；需求 CONFIRMED，技术方案尚未落盘 |
| 2026-10-11 | 139 历史 smoke、上游 0.162.1 及图片资产链路 | 增补 E-006–E-008；收紧未决问题；候选仅做源码与文档核对，不冒充新 runtime 实测 |
| 2026-10-11 | Codex 最新官方 Windows 与审批文档、0.162.1 精确源码 | 增补 E-009–E-010，收窄 E-007 到 legacy 路径；按用户 review 撤回两个未经确认的方案预设，后续优先评估对齐 Codex |
| 2026-10-11 | 截图权限具体路径及升级风险 | 增补 E-011–E-012，区分公开约定与真实 Desktop trace、版本升级与启用 MXC；未替换安装或执行真实截图 |
| 2026-10-11 | 升级工作延期登记与当前需求依赖复核 | 新增 issue 070；区分可独立推进的截图闭环和仍待底层能力解决的 AC-4/AC-5；需求范围修订待确认，未改产品代码 |
| 2026-10-11 | 用户确认拆分后的需求回溯 | requirement 恢复为已确认的收窄范围，issue 070 同步承接记录；没有设计或实现产物需要回退，未修改产品代码 |

## Rust 门禁补验（2026-10-11）

- 用户要求核对 159 是否结束，并在可运行时补验；真实模型继续不跑。
- 标准 build-storage status 显示 159 的 rust-check 与 build-cache 均为 idle/measured、ownerPid 为 null，无 running/unknown 对象；已知占用 52.35 GiB / 80 GiB。此证据证明构建占用释放，不代表需求 159 的所有交付流程均完成。
- `scripts/check/run.ps1 --stage rust` 通过，exit 0，290.4s：cargo fmt --check；lib 327 passed / 1 ignored，app launcher 1 passed，installer helper 7 passed；cargo check --release 通过。保留既有 BUNDLE_ID / Blocked 未使用警告。
- 标准脚本收尾后，本 worktree rust-check operation `26996eeb8ea0467780cbacc86c90b1cf` 为 idle/measured，ownerPid 为 null。此前 Rust 阻塞已解除；没有更改预算、删除其它任务缓存、调用真实模型、提交或合入。
