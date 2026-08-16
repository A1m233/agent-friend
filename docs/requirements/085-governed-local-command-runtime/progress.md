# 受治理的通用本地命令运行时 - 进度

## 总体状态

IN_PROGRESS

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
- [x] 完成其余定向测试、Windows source/frozen/installer 矩阵、项目完整门禁和 AC 证据整理

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
- [ ] AC-13 产品自带 Codex 依赖
- [x] AC-14 供应链失败关闭
- [x] AC-15 跨平台边界（source 契约已交付；macOS/Linux 实机验证经用户明确豁免）
- [x] AC-16 冻结运行时验证
- [x] AC-17 回归与项目门禁

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

## 待验收门槛

- AC-13 的固定制品、绝对路径、隔离 `CODEX_HOME`、无登录依赖和 installed payload 已分别通过自动化、frozen smoke 与隔离 installer 矩阵；尚未在一台物理上完全没有用户级 Codex 的干净 Windows 主机复验，因此保持未勾选。
- AC-12 已由 source 内置 `skill-creator` 正向 E2E 与 frozen `ExecCommandTool` source-replacement smoke 双重闭环；两条链路都使用 run-scoped immutable snapshot、结构化 Skill binding、内容指纹和一次性授权，未执行原 Skill 包。
- macOS/Linux 当前交付同一 source 接口、协议和 vendor lock；用户于 2026-08-16 明确豁免对应主机的实机验证，不再作为本需求待验收项。
- Windows PowerShell 5.1 在 Codex elevated sandbox 下会触发上游 `8009001d`；当前通用 exe/cmd/bat/Python 路径可用，PowerShell 脚本需显式使用另行可用的 `pwsh.exe`。是否把 PowerShell 7 纳入产品 payload 属于待确认的范围扩展。
- 首次 UAC 编排试验未显式透传隔离 `CODEX_HOME`，曾在用户个人 Codex 目录新增 `.sandbox/setup_marker.json` 与 `.sandbox-secrets/sandbox_users.json`；用户明确授权后已精确删除，个人目录中的其它既有内容未触碰。
