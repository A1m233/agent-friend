# 受治理的通用本地命令运行时 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 提炼公共 ApprovalBroker、通用 request/decision envelope 与 lifetime fail-closed 策略
- [x] 将文件审批迁移到公共控制面并保持 Tool、Bridge、push、前端和回归行为兼容
- [x] 实现 exec-server stdio JSON-RPC client、握手、进程 registry 和异常失联处理
- [x] 实现 `exec_command` buffered/streaming 模式及 `write_stdin`
- [x] 实现 ToolInvocationControl，贯通 run cancel、timeout 和进程树清理
- [x] 实现沙箱、受控环境、输出预算和结构化终态
- [x] 实现 run-scoped Skill execution snapshot 与内容身份校验
- [x] 接入 Bridge assembly、REST snapshot/cancel 和 command process push
- [x] 实现命令审批与运行状态的会话内前端卡片及 reconnect 恢复
- [x] 接入固定 Codex 制品 lock、source prepare/doctor/smoke 和 Windows package capability smoke
- [x] 闭环 Windows 上 Skill 快照目录作为进程工作目录时的真实启动失败，并补齐正向 Skill 脚本 E2E smoke
- [x] 将 Windows 沙箱 readiness / setup / repair 提升为跨会话全局能力，并接入系统权限设置和 push 恢复
- [x] 将 Skill-bound 命令工作目录改为包内相对 `skill.workdir`，补齐真实网络与公开 Skill 安装完整旅程
- [x] 完成其余定向测试、Windows source/frozen/installer 矩阵、项目完整门禁和 AC 证据整理
- [x] 实现 session-scoped 默认命令目录、跨 run 恢复、容量边界和 hard-delete 清理
- [x] 将普通命令改为可省略 `workdir`，把模型权限值改为 `read_only` / `read_write` 并更新最终 wire schema
- [x] 将宿主可确定的 PATH 程序根作为只读派生范围绑定审批、grant、TOCTOU 与 sandbox refresh
- [x] 区分宿主不存在、范围缺失、沙箱不可见、启动失败和命令非零退出，并贯通模型结果、历史与 UI
- [x] 更新命令卡片的会话目录、显式目录、Skill 快照和派生程序范围展示
- [x] 补齐无工作区确定性回归、跨平台契约、Windows source/frozen smoke 与项目完整门禁
- [x] 实现 `sandboxed` / `host_user` 两级执行、结构化 argv、一次性审批展示与沙箱 readiness 分流
- [x] 增加 Windows 命令形态预检、启动阶段说明、审批/启动耗时和 NTSTATUS 解释
- [x] 将真实多账号旅程中的“重复追问”和“桌面程序误入沙箱”拆成脱敏 Agent Eval 回归

## AC 验证

- [x] AC-1 通用命令执行
- [x] AC-2 结构化执行边界
- [x] AC-3 现有授权链路复用
- [x] AC-4 仅本次授权
- [x] AC-5 授权等级扩展边界
- [x] AC-6 长任务无模型忙轮询
- [x] AC-7 独立进度与及时终态
- [x] AC-8 交互式进程
- [x] AC-9 取消、超时与进程树清理
- [x] AC-10 环境与能力隔离
- [x] AC-11 输出与上下文上限
- [x] AC-12 Skill 内容身份
- [x] AC-13 产品自带 Codex 依赖（固定制品、隔离配置、frozen 与 installer 机制证据通过；用户于 2026-08-19 明确豁免无用户级 Codex 的物理 Windows 实机验证）
- [x] AC-14 供应链失败关闭
- [x] AC-15 跨平台边界（source 契约已按新 schema 重验；macOS/Linux 实机验证仍沿用用户豁免）
- [x] AC-16 冻结运行时验证
- [x] AC-17 回归与项目门禁
- [x] AC-18 全局沙箱能力状态
- [x] AC-19 沙箱内真实网络能力
- [x] AC-20 Skill 安装完整旅程
- [x] AC-21 无工作区命令旅程
- [x] AC-22 宿主用户执行级别（确定性实现、回归与 frozen `host_user` smoke 通过；用户于 2026-08-19 明确豁免真实桌面程序用户验收）

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-16 | 用户单独授权开始实现；建立 Phase 3 任务与 AC 验证清单。 | 需求与技术方案均为 `CONFIRMED`；尚未修改生产代码。 |
| 2026-08-16 | 新增 `agent.approvals` 公共生命周期与唯一 `ApprovalBroker`，文件审批通过兼容别名迁入同一内核；显式 lifetime 当前只启用 `once`，未知或未来等级 fail-closed。 | 文件审批定向测试 `7 passed`；ruff 通过，定向 mypy 仅暴露并清理了两个已失效的 test ignore。 |
| 2026-08-16 | 完成 Codex exec-server adapter、buffered/streaming Tool、run-scoped 句柄、协作式取消、输出 tail、结构化终态、Bridge REST/push 与前端命令卡片。普通命令保持 Tool pending 到终态，streaming 在 start ack 后返回句柄；前端仅消费本地 push，不设置模型或 HTTP 周期轮询。 | fake exec-server 覆盖输出、交互输入、归属、取消、沙箱确认和 runtime lost；输出 push 按 200ms/64KB 聚合。 |
| 2026-08-16 | 完成沙箱权限映射、路径 link/junction 与批准后身份复核、最小环境 allowlist、run 私有临时目录、64KB 模型结果预算和 256KB/stream UI tail。Windows 精确 split-root 使用 Codex elevated sandbox backend；普通命令不会隐式触发 setup。 | 未初始化时在审批前 fail-closed；新增显式 status/setup API 与用户点击按钮。实际 UAC setup 尚未执行。 |
| 2026-08-16 | 完成 activated Skill 的 immutable run snapshot、entry/workdir 重写、内容 manifest 指纹、链接/特殊文件/复制竞态/预算失败关闭与 run 收尾清理。 | 定向测试覆盖 structured binding、原包修改后快照不变、链接路径与批准后目录替换。 |
| 2026-08-16 | 固定官方 Codex `0.147.0` 制品与许可证 lock，接入 source cache、身份校验、isolated `CODEX_HOME`、Tauri/Bridge 注入、package staging 与 frozen capability smoke 入口。 | `command-runtime doctor` 通过；Windows 产品 sandbox 已经用户显式授权并通过 UAC 初始化，产品目录为 `%APPDATA%/agent-friend/command-runtime/codex-home`。产品运行不使用用户 PATH、Codex 登录态或用户 `.codex`。 |
| 2026-08-16 | 完成 Windows source 真实命令 smoke，并按 Codex `0.147.0` 实际协议补齐 `process/write.writeId`。 | `status=ok`；buffered、streaming/stdin、cancel、read-only write deny 全部通过。Codex Windows sandbox 对 Windows PowerShell 5.1 存在上游 `8009001d` 问题，因此 Windows 默认 shell 改为绝对路径 `cmd.exe`；显式 PowerShell 脚本当前要求可用的 `pwsh.exe`。 |
| 2026-08-16 | 完成 package-sensitive frozen candidate 与 capability smoke。 | build `20260816T075109Z-b006f2c106ee-db73a1d3`；candidate 内固定 `codex.exe` 真实覆盖 buffered、streaming/stdin、read-only deny、Skill immutable snapshot、cancel 和 child-tree cleanup，全部通过。 |
| 2026-08-16 | 按标准分类器的 `full-matrix` floor 完成隔离 installer candidate、14 场景 smoke 与独立报告复核。 | `packageInputSha256=d797fb03627f3d1c18b75fbfca3ee1c7ac81e1c5b3deeb5e5a3e7eeaf1706ac0`；build `20260816T075653Z-b006f2c106ee-19704586`；`installer-smoke-verify` 通过；production 未修改。 |
| 2026-08-16 | 完成真实 10 分钟 buffered 长任务验收。 | `606.641s`；fake-model 边界调用 `2`（发起工具调用 + 终态 continuation）；process push `22`，其中 progress push `20`；`output_seq=20`、exit `0`、`terminal_reason=exited`、无截断。等待期间没有模型级固定周期查询。 |
| 2026-08-16 | 完成当前实现的完整 backend、frontend 与 Rust 门禁。 | backend：ruff、mypy、`1729 passed, 4 skipped`；frontend：lint/typecheck、`93 files / 532 tests`、production build；Rust：`196 passed, 1 ignored` + helper `6 passed` + release check，只有既有 warning。 |
| 2026-08-16 | 用真实 catalog 和产品 Codex 沙箱执行 Skill 正反向 smoke；全程无网络、无真实 LLM。 | 共享 `context7-mcp` 可从 shared root 加载并激活，但其声明的 `resolve-library-id` / `query-docs` 均未注册，按预期 fail-closed，确认通用 MCP host/forwarding 是独立缺口。内置 `skill-creator` 已完成加载、激活、immutable snapshot、64 位内容指纹和一次性授权，但 `ExecCommandTool` 将 cwd 改写到 snapshot root 后，Codex Windows backend 在 `process/start` 返回 `CreateProcessWithLogonW failed: 267`；正向脚本未启动，不能据此判定 Skill E2E 通过。 |
| 2026-08-16 | 闭环 Windows Skill snapshot cwd 启动失败：`CommandRuntime` 现在显式接收并校验互不重叠的 `codex_home` / `execution_root`，持久 Codex 控制面留在产品数据目录，run temp 与 immutable Skill snapshot 迁到系统临时区的产品命名空间并继续由 `ToolRunContext` 精确清理。 | 真实 source E2E 通过：catalog 加载并激活内置 `skill-creator`，经 `ExecCommandTool`、一次性 `ApprovalBroker`、snapshot cwd 和产品 Codex sandbox 执行原始 `scripts/quick_validate.py`，返回 `Skill is valid!`；`sandbox_type=windowsRestrictedToken`，snapshot fingerprint `9af0ef3003b4a32dc8bf8e0fa95f03dc1a504da34825265216900cbfb8383c54`。未回退到原 Skill 包执行。 |
| 2026-08-16 | 将 frozen command capability smoke 升级为真实 Tool 链路，并重跑完整 backend 与受治理打包门禁。 | backend：ruff、mypy、`1733 passed, 4 skipped`。frozen runtime build `20260816T094610Z-b006f2c106ee-b0c66afa` 的 `governed_command_runtime` 全绿，含 `skill_tool_path=true`、immutable source replacement、buffered、streaming/stdin、read-only deny、cancel 和 child-tree cleanup。分类器要求的新隔离 14 场景矩阵与独立复核也通过：`packageInputSha256=2342e31789a6ac927ff75b82bce9c34af05cd45c84538c470734eafd228ac8f4`，candidate build `20260816T095107Z-b006f2c106ee-e2a01173`，installer SHA-256 `786630f3ca119dc69774bd0b3e6fa10534b1587d48d7014e8bc29d9d6ddc725d`；production 未修改。 |
| 2026-08-17 | 完成跨会话全局 Windows 沙箱 capability：Bridge 只读检测并 push `setup_required / ready / repair_required / unavailable`，系统权限设置统一初始化、重检和修复；命令审批在未就绪时保持 pending，ready 后恢复但不自动批准。 | 真实 setup 使用外层 UAC 启动官方 `sandbox setup --elevated`，显式传入原产品用户 `--user` 与隔离 `--codex-home`，避免提权账户漂移；普通命令路径不会隐式弹 UAC。相关 backend、Bridge、frontend store/UI 回归通过。 |
| 2026-08-17 | 完成 Skill-bound 包内相对 cwd、最小子进程环境、Codex Windows 托管代理保留和真实网络诊断。 | `network-smoke` 在 `windowsRestrictedToken` 下确认 SystemRoot/WINDIR/ComSpec/TEMP/TMP、Winsock 与公开 HTTPS 200。补齐 `SystemDrive` 后，不再由输入法组件在仓库 cwd 生成字面量 `%SystemDrive%/ProgramData`；该 smoke 生成的两份缓存已精确清理，真实 `C:\ProgramData` 未触碰。 |
| 2026-08-17 | 新增 builtin `skill-installer`，使用隔离个人 Skill 根执行公开 GitHub 安装并由 watcher 无重启发现；自动模式对 GitHub archive HTTP 失败回退 git，并保留 HTTPS/SSH 双路径诊断。 | 受控 fixture `6 passed`；公开 `openai/skills` 的 `skill-creator` 安装成功，`sandbox_type=windowsRestrictedToken`、`catalog_generation=3`、审批展示 `workdir="."`，未修改真实个人 Skill 根。期间真实遇到 HTTP 503/429，暴露并修复了 fallback 与 smoke 强制 download 的缺口。 |
| 2026-08-17 | 完成当前变更的 backend、frontend、Rust 与 package-sensitive 冻结 runtime 验证。 | backend：ruff、mypy、`1778 passed, 4 skipped`；frontend：lint/typecheck、`95 files / 540 tests`、production build；Rust：`197 passed, 1 ignored` + helper `6 passed` + release check。frozen runtime build `20260817T145323Z-97b96e144726-d0b41f12` doctor `ok=true`，builtin installer、catalog watcher、buffered/streaming、read-only deny、Skill snapshot/tool path、cancel、child-tree cleanup 全绿；未构建安装包。 |
| 2026-08-18 | 真实无项目会话再次失败后回溯 Phase 1/2：确认 agent-friend 当前没有项目概念，并对齐 Codex 的 thread-scoped cwd，把默认执行目录改为 session-scoped；普通命令 workdir 可省略，权限值改为 read_only/read_write，PATH 程序根由宿主派生并进入同一次授权。 | AC-2、AC-4、AC-10、AC-15～17 与新增 AC-21 重新打开；既有 Skill snapshot、全局 sandbox provisioning、网络和安装旅程证据保留，待新契约实现后做受影响复验。 |
| 2026-08-18 | 完成无项目命令旅程的实现与收口：session-scoped 默认目录稳定跨 run，显式 cwd 仅覆盖单条命令，hard-delete 清理；只读/读写权限、宿主 PATH 程序根、容量门槛、错误分类和前端来源展示贯通 Tool、审批、历史与 UI。刷新页面时立即校准后台 run，等待审批卡片可恢复按钮。 | 真实新会话在不传 `workdir` 时一次审批后执行 `echo agent-friend-session-cwd-smoke` 成功，`cwd_source=session_managed`、`sandbox_type=windowsRestrictedToken`、exit `0`。Windows Codex `0.147.0` 首次只读 cwd 的 ACL 竞态通过产品自有 session 目录的同步 metadata 预热规避；实际命令仍使用原只读 sandbox。 |
| 2026-08-18 | 完成最终回归、冻结 runtime 和桌面留证。 | backend：ruff、mypy、`1804 passed, 4 skipped`；frontend：lint/typecheck、`98 files / 551 tests`、production build；Rust：`197 passed, 1 ignored` + helper `6 passed` + release check。最终 local-test frozen build `20260817T202954Z-97b96e144726-15c41416` 的 command smoke 全绿，新增 fresh session-managed read-only 检查确认首次启动成功且写入被拒绝；未构建安装包。刷新恢复授权按钮的桌面截图为 `%TEMP%/agent-friend-085-approval-restored.png`。 |

| 2026-08-18 | 真实 MAA 多账号旅程中，MuMu 桌面程序在 `windowsRestrictedToken` 下以 `0xC0000409` fast-fail；新增 `host_user` 执行级别并保持公共 Tool、一次性审批和 exec-server 生命周期不变。Windows cmd 前置拒绝 PowerShell `&` 与未加引号的含空格 exe 路径，审批卡展示宿主不受限能力，沙箱未初始化不再阻塞 host 请求。 | source command smoke 用产品 Codex `0.147.0` 确认 `host_user=true`、`sandbox_type=none`。完整门禁：backend ruff/mypy、`1820 passed, 4 skipped`；frontend `98 files / 549 tests` 与 production build；Rust `197 passed, 1 ignored` + helper `6 passed` + release check。Agent Eval 新增 C18-04 与 C24-04，脱敏账号 fixture，确定性 loader/judge/runner `87 passed`。package-sensitive frozen runtime build `20260818T060937Z-7ec2e46eac34-3293aaf5` doctor `ok=true`、`host_user=true`；未构建安装包，未调用真实 LLM，真实 MuMu 启动留待用户验收。 |
| 2026-08-19 | 重新核对 issue 052 后确认：TanStack end-anchor 滚动重构只存在于当前工作区，不属于需求 085，也未进入当前分支或主干提交；合入前撤回该重构，恢复主干既有的用户滚动意图、主动离底和持续跟随语义。AC-17 因该用户可见回归重新打开，并在撤回后重新关闭。 | 滚动与 conversation store 定向回归 `3 files / 54 tests`；完整门禁 backend ruff/mypy、`1836 passed, 4 skipped`，frontend lint/typecheck、`98 files / 554 tests` 与 production build，Rust `197 passed, 1 ignored` + helper `6 passed` + release check。未升级 TanStack，未启用 `directDomUpdates`，未改动 issue 051。 |
| 2026-08-19 | 用户确认需求收口：AC-13 的无用户级 Codex 物理 Windows 实机验证与 AC-22 的真实桌面程序验收均明确豁免，不再阻塞合入；对应自动化、frozen 和 installer 机制证据继续保留，未执行的人工场景不得表述为实测通过。总体状态切换为 `COMPLETED`。 | issue 053 记录不可用审批入口的重复请求，按用户决定延后处理；该问题已有通用 `no_progress` 止损，不阻塞本需求。当前提交范围已盘点，未跟踪项均来自本轮实现、测试、真实 eval 或 issue 记录，敏感凭据扫描无命中。 |

## 验收豁免与剩余风险

- AC-13 的固定制品、绝对路径、隔离 `CODEX_HOME`、无登录依赖和 installed payload 已分别通过自动化、frozen smoke 与隔离 installer 矩阵；尚未在一台物理上完全没有用户级 Codex 的干净 Windows 主机复验。用户于 2026-08-19 明确豁免该实机验证并接受剩余风险。
- AC-12 已由 source 内置 `skill-creator` 正向 E2E 与 frozen `ExecCommandTool` source-replacement smoke 双重闭环；两条链路都使用 run-scoped immutable snapshot、结构化 Skill binding、内容指纹和一次性授权，未执行原 Skill 包。
- AC-22 已由确定性测试、source smoke 与 frozen `host_user` capability smoke 覆盖结构化 `argv`、审批、隔离级别和失败关闭；尚未由用户完成真实桌面程序启动验收。用户于 2026-08-19 明确豁免该人工场景并接受剩余风险。
- macOS/Linux 当前交付同一 source 接口、协议和 vendor lock；用户于 2026-08-16 明确豁免对应主机的实机验证，不再作为本需求待验收项。
- Windows PowerShell 5.1 在 Codex elevated sandbox 下会触发上游 `8009001d`；当前通用 exe/cmd/bat/Python 路径可用，PowerShell 脚本需显式使用另行可用的 `pwsh.exe`。是否把 PowerShell 7 纳入产品 payload 属于待确认的范围扩展。
- 首次 UAC 编排试验未显式透传隔离 `CODEX_HOME`，曾在用户个人 Codex 目录新增 `.sandbox/setup_marker.json` 与 `.sandbox-secrets/sandbox_users.json`；用户明确授权后已精确删除，个人目录中的其它既有内容未触碰。
