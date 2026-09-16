# 桌面应用正常启动与生命周期：进度

## 总体状态

IN_PROGRESS

2026-09-17 用户明确授权实现、必要 smoke、真实 LLM、桌面验收，并在通过后合入 main、推送远端、构建 local-test。需求与技术方案均为 CONFIRMED；当前分支 `feature/134-desktop-application-launch`。Guide 基线阻塞已解决，真实模型首轮已通过，正在完成产品与打包验收。下文早期阻塞记录保留为历史，不代表当前状态。

## 任务列表

- [x] 原生 Explorer 启动助手、结构化协议、受管 Job 隔离验证（专用原生 fixture）。
- [x] Python 应用启动服务、工具契约、取消/未知结果与持久化恢复。
- [x] 现有审批控制面、风险评估与前端展示接入。
- [ ] 开发装配、受治理 payload 与 frozen capability smoke。
- [x] 诊断生产/消费契约、工具恢复及相关测试。
- [x] 独立 MAA skill 指引同步与确定性验证，已提交并推送 `6e4c5f3`。
- [x] 工具行为回归 case 与真实验证边界记录。
- [ ] 项目检查、真实 Windows/MuMu 验证和用户验收。

## AC 验证

验收目标见 [requirement.md](requirement.md)。

| AC | 状态 | 证据/缺口 |
| --- | --- | --- |
| AC-1 | pending | MuMu 正常打开及四层就绪核验 |
| AC-2 | pending | 真实日常及任务后存活，需有界游戏执行授权 |
| AC-3 | pending | 任务取消、服务停止、产品退出的实际隔离 |
| AC-4 | pending | 已有实例复用不改变生命周期 |
| AC-5 | source-verified | 原 command_runtime 的取消、超时、收尾测试通过；未改变清理实现 |
| AC-6 | partial | 无效目标、拒绝、入口不可用、未知不重放、checkpoint/证据失败、恢复有单测；Android 未就绪的真实场景未测 |
| AC-7 | pending | 明确关闭目标的既有应用流程 |
| AC-8 | partial | 新工具、审批 UI、持久化恢复和诊断契约已测；行为 case 确定性检查通过，真实模型与事故归因回答待验 |
| AC-9 | partial | 正式 skill 仓库本地指引已改、117 项测试通过；未发布、未在真实产品跑日常 |
| AC-10 | partial | 非桌面入口及助手缺失单测通过；无 Explorer/非 Windows 实机未验 |

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-09-17 | 建立实施清单；开始原生隔离可行性验证 | 尚无通过结果；真实游戏、付费 LLM 与安装操作按各自规则授权 |
| 2026-09-17 | 新增独立应用启动工具、Windows Explorer COM 助手、审批/UI、持久化 checkpoint、恢复与诊断投影；未改变受管命令或服务 Job 策略 | accepted 不代表就绪，unknown 不重放，launch_id 不能操作命令进程 |
| 2026-09-17 | 原生 fixture 在 KILL_ON_JOB_CLOSE Job 下两次验证通过 | 第二次 helper SHA-256 `436021a29754dfde6b046481fa0885c74ce28ef6dc5819619b214d5c3f387890`；argv roundtrip、目标不属助手 Job、助手退出及 Job 关闭后存活。不是 MuMu/安装版验收 |
| 2026-09-17 | 前端完整检查通过 | 156 个测试文件、1300 项测试，lint/typecheck/production build 通过；保留既有 CSS highlight/chunk size warnings |
| 2026-09-17 | Rust 完整检查通过 | cargo fmt、cargo test、cargo check --release 通过；保留既有 dead_code warnings |
| 2026-09-17 | 后端首轮全量 3210 passed / 5 skipped / 1 failed | 新增 doctor 能力导致原单测缺 fixture；已修正该单测，继续复验，不能把首轮写为全绿 |
| 2026-09-17 | MAA skill 本地修改与 117 项测试通过 | 基线 `7d4d37fe9e189272966987881a9cd606fae317d8` + references/mumu-windows.md；第一次受 pycache 残留影响，清理缓存并禁用字节码后通过；Skill validator、guidance audit、个人及项目 adapter doctor 通过 |
| 2026-09-17 | 固化 C24-04/07/08/09，保持 C27-03/04 为未参与设计的受管命令 holdout | 真实 LLM 尚未运行；确定性 judge 核验进行中；回执与就绪措辞仍需人工逐条复核 |
| 2026-09-17 | 后端完整复验通过 | 3216 passed、5 skipped；lint/typecheck 通过。之后仅统一 exec_command 描述与沙箱错误路由，245 项 command_runtime/agent_eval 回归及 lint 通过 |
| 2026-09-17 | 增加应用审批推送、独立决定 endpoint 与不会生成受管进程的前端回归；补充 session_auto 风险提示 | 33 项相关前端测试通过；最终前端 lint/typecheck 通过。全量 1300 项为这些增量前的结果，不混报为最终全量 |

## 打包与后续验证边界

- `installer-contract` 因 `scripts/package/run.py` 给出 `full-matrix` floor，不降档。当前先走 package-release runtime-only；runtime 通过也不代表安装器矩阵完成。
- package-baseline：Guide r47 已发布且 immutable，checkout `3f564e0115821d293abbb3f06d89aceb864aae47`，snapshot `1511e816c926c4e0742e73baccf8aaf1461272a1ce802a7112d5e313468d4edd`，verify 通过。
- Guide reviewed_product_commit `aa61f5541132cfd880cad8b913f8cb5f446ee543` 不是当前 HEAD 的祖先，未擅选另一公开复核基线。本次已知变更明确需要后续更新 using-agent-friend 的正常应用启动、保留运行和未知回执说明；自动 package-baseline 不编辑或发布公开知识。
- 尚未启动真实 MuMu/游戏、付费 LLM、产品安装或真实桌面视觉验收。AC-1/2/3/4/7/9 的端到端部分仍待有界验证，不宣称通过。

### 早期冻结验证阻塞（已解决）

1. runtime-only 首次调用在实际构建前失败：共享 Guide checkout 已从 r47 被外部工作推进到 r48，原参数 commit 不再匹配。没有产生本次 runtime build ID，也没有留下 builtin overlay。
2. 为固定输入，创建本任务独立的 detached Guide worktree：`%LOCALAPPDATA%/agent-friend/maintenance/guide-requirement-134`，HEAD `0eda8efdcd0b6326d4965556c6422728f1151001`。GitHub r48 非 draft、非 prerelease、immutable，兼容范围 `[0.3.0,0.4.0)`；manifest snapshot `a536599e1370f3f68eeb240be800e52901b33b14754a2bf2f0a374fd9d55eac2`。
3. 独立干净 checkout 的 verify 失败：metadata 预期 content SHA `59b6a7e5183af056b42b8b2544925f69df6c982003e67299a2cd2c2945be7559`，实际 `0f073acfdc75a6be8ee8d3b3924afa96de6d4fb560613046b77f35abb76f1f28`。同 commit 的共享 checkout verify 通过。
4. `git ls-files --eol` 确认独立 checkout 全部 LF；共享 checkout 的 metadata 为 CRLF、troubleshooting.md 为 CRLF、using-agent-friend.md 为 mixed，Git status 均 clean。Guide digest 实现直接读取 raw bytes（scripts/agent-friend-guide/run.py 的 package_digest）。这是同 Git 内容无法稳定重建既有 release 字节的独立基线缺陷，不是 Explorer 启动失败。
5. 未修改公开 Guide、重签哈希、降档、换回旧 release 或使用未校验快照。需要先修复 Guide 内容/发布字节一致性并取得新的有效 immutable baseline，再继续 runtime-only 与 full-matrix。独立 Guide worktree 保留供复查，未推送任何变更。

### 早期下一步（由后续执行记录更新）

- 先处理上述 Guide 基线缺陷；它超出已确认的桌面应用启动设计，本轮保留诊断证据，没有顺手修改公开发布链。
- 源码候选通过后，真实模型计划为 C24-04/07/08/09 + C27-03/04，各 1 次，`deepseek/deepseek-v4-flash`，报告写 reports，note `requirement-134-desktop-launch-first-validation`。执行前再冻结 case digest/指引 diff 和调用上限，并按 llm-api-confirm 取得该次授权；本记录不是授权。
- 被测产品描述不含 case ID、MuMu/Canvas/Viewer、fixture 路径或答案原句；MAA 自身领域说明不作为泛化证据。C27-03/04 未参与新启动契约设计，作为相邻 holdout。真实报告需人工复核请求接受与业务就绪的区别，不能只采信关键词判分。
- 在明确的测试实例、账号与资源范围下验证 MuMu/MAA；真实产品退出/服务停止/任务取消与已有实例复用分别记录，不能用本轮原生 fixture 替代。
- 用户验收前不标 COMPLETED，不合入或发布。MAA 本地 Skill 尚未同步到 GitHub；个人与项目 adapter 已校验。

### 后续执行记录

- 用户已授权本次交付所需验证与通过后的合入、推送、local-test；不再以早期未授权记录阻塞。
- 集成最新 main `1bde2073`，无冲突；当前实现 HEAD `3a3535f9`。
- Guide 发布端增加 Git 提交字节一致性门禁及回归，17 项测试通过；公开内容补充正常应用启动边界，发布 immutable r49，来源 `65e94efd3ed4818bc9153d20e4aa7ebdc609a137`，snapshot `0536d9cb1a47123d32ab622a853c94861b26856f14bf1a096c342ca5dc6a402a`。独立干净 checkout verify 通过，冻结输入固定到该版本。
- 集成后 backend 全量：3283 passed、5 skipped；lint 与 mypy 通过。保留既有第三方退出时 event-loop 警告，不作为业务失败。
- C24-04/07/08/09 和 C27-03/04 在当前 `deepseek/deepseek-flash` / off 各跑一次，6/6 通过；报告位于 `agent_eval/reports/2026-09-16T17-3*.json`。前三项基于 `cc3b8ee6`，后三项基于 `3a3535f9`，期间仅 Guide 发布门禁变化，工具和 prompt 相同。逐条复核接受不等于就绪、unknown 不重试、既有应用保留及受管命令 handle 延续。
- 实际 Tauri WebView 审批与最终回复已观察；通过生产工具打开 MuMu，回执 accepted、readiness not_verified，回合终态后进程和可恢复窗口仍存在。其 Android 从 starting_rom/false 过渡至 start_finished/true，随后按管理器新端口连接 ADB 成功。此项说明冷启动及延迟就绪，不用过期端口或此前进程归属推断复用通过。
- 私有桌面/游戏证据统一保存在本机 maintenance/requirement-134-acceptance；公开仓库不收录用户历史侧栏截图、账号或原始游戏日志。
