# 桌面应用启动：证据索引

## 用途与当前结论

服务 [需求 134](requirement.md) 的诊断来源、外部参考和方案判断。此文件记录证据，不代替已确认的 [技术方案](design.md)；方案确认不等于候选机制已经验证。

- 现有事故与 run 结束清理高度吻合，仍缺少最终进程终止回执和 dirty 构建的完整源码输入。
- 当前 MAA skill 已要求默认保持模拟器可见运行；尚不能确认事故实际加载的全文与当前仓库版本相同。
- Codex 的受管后台终端与操作系统正常打开桌面应用是不同能力，不能把二者的生命周期混为一谈。
- 正常打开应用后再独立发现实例、ADB 和业务状态，从现有 skill 结构上可行；原生 fixture 隔离已验证，真实 MuMu/产品退出尚未验证。
- Windows 的父级 Job Object 继承必须纳入验证，仅移除 run 结束回调或更换启动命令不足以证明脱离产品清理。

## 证据账本

| ID | 主张与来源 | 证据基础 / 置信度 | 状态与限制 |
| --- | --- | --- | --- |
| E-001 | 诊断包显示 2026-09-16 22:51:36 以 host_process_start(streaming) 启动 MuMu；23:08:47 仍有进程；23:09:47 run 完成；23:12:08 用户反馈已关闭。均为北京时间 | runtime-supported / confirmed（事件顺序） | active；关闭原因见 E-002，不能由先后关系单独确定 |
| E-002 | 构建来源提交 fcab8eb172360408244c95fddde4d709f744d32f 中，command_runtime/tools.py:845 登记句柄，conversation.py:1109 调用 close_commands，tool_runtime/context.py:187-197 逐一终止；tools.py:1887-1906 调用 runtime.terminate | code-supported + E-001 / strongly-supported（事故归因） | active；构建 dirty=true，缺最终终止回执，未做受控复现 |
| E-003 | 本产品调研基线 47dfe3ee7d1563713fbbe8b0b2c2b5c852c06d9e；frontend/src-tauri/src/runtime_services/process.rs:24-60 为服务设置 KILL_ON_JOB_CLOSE 并分配进程 | code-supported / confirmed（代码机制） | active；需要验证每个候选启动位置的实际 Job 归属 |
| E-004 | maa-daily-skill 提交 7d4d37fe9e189272966987881a9cd606fae317d8，maa-daily/references/mumu-windows.md:34-64 要求普通可见启动、独立就绪核验及默认保持运行；SKILL.md 支持既有实例 | code-supported / confirmed（当前指引）；directional（候选兼容性） | active；不是事故所加载正文的版本证明，也不是实际运行验证 |
| E-005 | Codex 官方 app-server 文档提供 thread/backgroundTerminals/list、terminate、clean；command/exec 另有命令会话控制 | official-documentation / confirmed（公开能力） | active；不证明闭源 Desktop 的应用启动实现 |
| E-006 | Codex 提交 da18000cae9884ab45f83b2d07fbd5a220a1de39，core/src/session/session.rs:1617 将 unified_exec_manager 放在 session services；core/tests/suite/unified_exec.rs:2852 有 unified_exec_keeps_long_running_session_after_turn_end；process_manager.rs:1795 管理终止；process.rs:648 Drop 终止进程 | code-supported / confirmed（源码与测试定义） | active；未运行上游测试；上述持续进程测试跳过 Windows，不能外推为 Windows GUI 存活证明 |
| E-007 | Microsoft ShellExecute/IShellDispatch2.ShellExecute 提供桌面 Shell 的打开语义；SHELLEXECUTEINFO 的 hProcess 并非所有成功启动都存在 | official-documentation / confirmed（API 契约） | active；API 成功不证明窗口、Android 就绪或脱离父级 Job |
| E-008 | Microsoft Job Objects 文档说明 CreateProcess 子进程默认继承 Job、breakaway 受父级配置约束，Job 可统一终止进程 | official-documentation / confirmed（平台规则） | active；具体产品运行树尚需真实验证 |
| E-009 | Microsoft 的 ShellExecuteFromExplorer 示例通过已有桌面 ShellFolderView 的 Application 对象取得 IShellDispatch2；对象位于桌面 Explorer 进程，区别于在调用方直接创建目标进程 | official-documentation / confirmed（示例机制）；directional（本产品隔离） | active；本地真实 Job 隔离和 MuMu 兼容性尚未验证 |
| E-010 | 本产品基线中 ApprovalBroker 已统一管理授权模式和取消；push_subscriber.rs 仅转发事件；Windows crate 已提供 Shell/COM 能力 | code-supported / confirmed（现有结构） | active；支持复用审批并用薄原生助手，不证明新增实现已完成 |
| E-011 | 原生 fixture 经现有 Explorer 打开，目标不在助手的 KILL_ON_JOB_CLOSE Job 中；助手退出、Job 关闭后仍存活，参数空串/空格/引号/尾反斜杠/元字符往返一致 | runtime-supported / confirmed（fixture） | active；第二次 helper SHA-256 `436021a29754dfde6b046481fa0885c74ce28ef6dc5819619b214d5c3f387890`，不代表 MuMu 或产品 UI 端到端验证 |

## 可复查来源

- E-002：[源码基线](https://github.com/A1m233/agent-friend/tree/fcab8eb172360408244c95fddde4d709f744d32f)，本地已用 git cat-file 与 git show 核验。
- E-003：[服务进程管理](https://github.com/A1m233/agent-friend/blob/47dfe3ee7d1563713fbbe8b0b2c2b5c852c06d9e/frontend/src-tauri/src/runtime_services/process.rs)。
- E-004：[MuMu 指引](https://github.com/A1m233/maa-daily-skill/blob/7d4d37fe9e189272966987881a9cd606fae317d8/maa-daily/references/mumu-windows.md)。
- E-005：[官方 app-server 文档](https://developers.openai.com/codex/app-server)，2026-09-17 前后本次调研读取；动态页面，未来需重新核验。
- E-006：[会话结束后保留进程的测试](https://github.com/openai/codex/blob/da18000cae9884ab45f83b2d07fbd5a220a1de39/codex-rs/core/tests/suite/unified_exec.rs#L2852)、[进程管理器](https://github.com/openai/codex/blob/da18000cae9884ab45f83b2d07fbd5a220a1de39/codex-rs/core/src/unified_exec/process_manager.rs)、[Drop 清理](https://github.com/openai/codex/blob/da18000cae9884ab45f83b2d07fbd5a220a1de39/codex-rs/core/src/unified_exec/process.rs#L648)。本产品 vendor 当前固定为 Codex 0.147.0；此处最新上游参考不代表已升级产品依赖。
- E-007：[ShellExecuteExW](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shellexecuteexw)、[IShellDispatch2.ShellExecute](https://learn.microsoft.com/en-us/windows/win32/shell/ishelldispatch2-shellexecute)、[SHELLEXECUTEINFOW](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-shellexecuteinfow)。
- E-008：[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。
- E-009：[Microsoft 的 Explorer 委托启动说明](https://devblogs.microsoft.com/oldnewthing/20131118-00/?p=2643)、[FindWindowSW](https://learn.microsoft.com/en-us/windows/win32/api/exdisp/nf-exdisp-ishellwindows-findwindowsw)、[get_Application](https://learn.microsoft.com/en-us/windows/win32/api/shldisp/nf-shldisp-ishellfolderviewdual-get_application)。文章直取曾遇到 403，后经官方域名检索取得包含完整相关方法与对象位置解释的索引内容；未将检索结果视为本机实测。
- E-010：基线 47dfe3ee7d1563713fbbe8b0b2c2b5c852c06d9e 的 `agent/src/agent/approvals/broker.py`、`frontend/src-tauri/src/push_subscriber.rs`、`frontend/src-tauri/Cargo.toml`。

## 原始材料与覆盖范围

- E-001 来源是用户在当前诊断会话提供的私有诊断包，导出时间 2026-09-16T23:29:33.985915800+08:00；不复制到仓库，不保留用户目录、完整对话、账号标识或截图。
- ZIP SHA-256：`6a88f2467ffc94a74a886b9a60eb2697170a0680e261d301aaa8881330125266`。原件由需求负责人持有；仓库读者不能据此访问原件，需按权限提供。
- 已运行捆绑 inspect_bundle.py；CRC 通过，67 个条目，无重复或不安全路径；formatVersion=1、schemaRevision=13、无未处理 capability。
- outcome=partial：请求最近 5 轮，扫描触及约 64 MiB 上限，仅有 1 个 user turn 加前一轮的部分事件；原会话文件约 599 MB。不能据此排除缺失时段的动作。
- 运行版本 0.3.0，buildId=20260914T175952Z-fcab8eb17236-87a25f26，local-test，dirty=true；安装完整性 consistent 只证明导出时安装身份一致。
- 部分日志仅保留尾部；Windows Shell journal 从 23:10:10 起，晚于 run 结束，不能补证关闭瞬间。
- 未执行真实 LLM、游戏操作；候选原生 fixture 启动试验见 E-011；未读取无关桌面截图。
- 查询方法：限定所选会话的启动请求/结果、收尾检查、run_terminal 和最后用户反馈，再用构建提交的源码解释生命周期。不是对所有历史异常的扫描。

## 未决问题与下一项验证

1. 已选择薄原生助手委托当前桌面 Explorer 的候选路线；下一项关键证据是生产等价 Job 约束下的启动、助手清理及产品退出试验。选择方案不是隔离成功的证据。
2. 已确定 accepted 与业务就绪分离、unknown 不自动重放；仍需验证既有实例、无新 PID、回执丢失与恢复分支。
3. MAA skill 是否可在目标发行版上完成窗口、管理器状态及 ADB 核验？候选启动就绪后，在明确授权的目标设备上验收。
4. 已确定首期仅支持 Windows 本地 exe 和适配现有桌面审批的入口；缺少桌面、CLI/IM 和其他平台明确不可用，仍需验证能力投影和错误语义。

## 决策影响与对账

- E-001 至 E-004 支持需求中的正常启动、生命周期隔离及 skill 联动验收。
- E-005/E-006 用于区分任务、会话和后台命令的生命周期，不用于声称 Codex Desktop 已采用某种 GUI 启动机制。
- E-007/E-008 支持优先评估操作系统启动路径，以及把父级 Job 隔离列为验收条件；尚未选择最终 API。
- 2026-09-17：需求范围已由用户确认，分支 134 已占号。requirement 为 CONFIRMED；技术方案尚未确认、未进入实现，progress.md 在实现阶段按工作流建立。
- 2026-09-17 后续：用户确认技术方案，design 为 CONFIRMED，取代上一条“技术方案尚未确认”的阶段状态。E-009/E-010 补充选择依据；尚未实现或运行候选验证，progress.md 仍在实现阶段建立。
- 2026-09-17 实施：用户明确开始实现。首次 COM 实验直接 GetItemObject<IShellFolderViewDual> 返回 E_NOINTERFACE；按接口契约改为先获取 IDispatch 再 cast 后，原生隔离试验通过两次。E-011 推进原 E-009 中“尚未验证”的本机 Job 隔离部分；原事故归因不升级为 confirmed。
- E-011 第二次报告位于本机构建工作区 `desktop-application-dev/smoke-e85e4c6e9ea24d359322978b61907328/report.json`，由 `scripts/desktop-application/run.ps1 smoke` 生成；构建工作区不是持久仓内归档。通过的最小事实已记于此处，完整原件可能受后续构建 GC 生命周期影响。
