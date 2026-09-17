# 桌面应用正常启动与生命周期：进度

## 总体状态

COMPLETED

2026-09-17 用户明确授权实现、必要 smoke、真实 LLM、桌面验收，并在通过后合入 main、推送远端、构建 local-test。需求与技术方案均为 CONFIRMED；当前分支 `feature/134-desktop-application-launch`。全部 AC 的适用证据与项目检查已完成，14 项安装矩阵和独立校验通过；按用户明确的“通过后合入并推送”授权进入交付。下文早期阻塞记录保留为历史，不代表当前状态。

## 任务列表

- [x] 原生 Explorer 启动助手、结构化协议、受管 Job 隔离验证（专用原生 fixture）。
- [x] Python 应用启动服务、工具契约、取消/未知结果与持久化恢复。
- [x] 现有审批控制面、风险评估与前端展示接入。
- [x] 开发装配、受治理 payload 与 frozen capability smoke。
- [x] 诊断生产/消费契约、工具恢复及相关测试。
- [x] 独立 MAA skill 指引同步与确定性验证，已提交并推送 `6e4c5f3`。
- [x] 工具行为回归 case 与真实验证边界记录。
- [x] 项目检查、真实 Windows/MuMu 验证和用户验收。

## AC 验证

验收目标见 [requirement.md](requirement.md)。

| AC | 状态 | 证据/缺口 |
| --- | --- | --- |
| AC-1 | passed | 真实 Tauri 冷启动；可恢复窗口、目标实例、Android 和当前 ADB 地址分别核验。 |
| AC-2 | passed (bounded smoke) | 正式 Skill 的 StartUp/Award 有界任务在产品内执行，最终回复和清理后同一 MuMu 窗口、实例、ADB 保持；不外推为刷图或完整资源型日常。 |
| AC-3 | passed | 真实 Tauri 任务取消、托盘退出、supervisor 停止 bridge 分别记录，MuMu 同 PID 及 ADB 保持。 |
| AC-4 | passed | 产品任务开始前由普通桌面启动的既有实例被复用；MAA 终态、最终回复及开发服务清理后同 PID/窗口/ADB 保持。 |
| AC-5 | passed | 受管命令取消/超时/收尾自动化回归及 frozen capability smoke 通过，未改变清理策略。 |
| AC-6 | passed | 失败/拒绝/不可用/unknown/恢复由单测覆盖，真实拒绝不打开；MuMu 窗口先出现、Android 后就绪的过程实测。 |
| AC-7 | passed | 管理器对已确认实例正常 shutdown 返回成功，关闭状态实测；之后重新打开并保持。 |
| AC-8 | passed | 审批、工具证据、恢复和诊断测试；6/6 模型回归及单轮历史归因观察。各一次观察，不宣称统计稳定性。 |
| AC-9 | passed | MAA 正式源 6e4c5f3 已发布并更新到产品，117 项测试；真实产品通过 Skill binding 执行现有有界任务。 |
| AC-10 | passed (contract/tests) | Windows desktop_chat 能力边界及缺失助手/不支持入口错误有确定性测试；不宣称非 Windows 或无交互桌面实机验证。 |

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

## 早期打包与验证边界（历史记录，现状见后续执行记录）

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
- 后续桌面 E2E：真实拒绝未打开、接受后取消终态 cancelled 且 MuMu 同 PID 存活；实际托盘 Quit 后 shell/bridge/voice 消失、开发端口释放，MuMu/ADB 保持；再次冷启动后，通过真实 supervisor 停止 bridge，返回 stopped 且对应 PID 消失，MuMu 同 PID 与 ADB 保持。明确关闭目标实例的管理器调用返回成功，随后再正常打开并保留。
- MAA 有界 smoke 的 StartUp/Award 两条链完成、无 task-chain 错误；奖励扫描确认 10/10 档与每日两项奖励已领。只验证当前账号的启动/奖励，不代表刷图、消耗资源或多账号完整日常。正式源 `6e4c5f391060eb420dde70d38b1deb8b1fb5536d` 已推送，产品内同源更新成功，digest `9d13f446d76890db1b38aad55850c5beb44ab98ea36d34f5631cb7e6bf52eab9`。
- 桌面取消 pilot 的计数压力请求被人格直接拒绝，未调用工具；改为普通打开请求后实测取消通过。保留首轮失败，不将它混入成功率。单轮历史归因观察没有以“未调用关闭工具”排除宿主清理；这不升级原事故归因，也不代表统计稳定性。
- 集成后 frontend：157 文件 / 1310 测试通过，lint/typecheck/production build 通过；Rust fmt、290 项测试（另 1 ignored）与 release check 通过。
- 首个 frozen candidate `20260916T175525Z-e5b33e6aab35-f3ac42bb` 的 doctor 全绿，新增助手身份、Explorer dispatch、参数往返与持久化证据通过。MSIX 宿主的隔离目录重定向被 matrix preflight 在安装前拒绝；转由当前用户普通桌面进程运行原脚本后通过该 preflight，没有削弱门禁。
- 首轮完整矩阵发现安装后 doctor 入口缺少 app launcher/fixture 输入，7 次安装后 doctor 失败并使 silent-success 关联项失败；其余 6 项通过。报告保留，不可用于交付。已修正安装后 smoke：绑定安装 payload 内的助手，fixture 放在临时目录，成功与失败都清理；相关 3 项测试通过，正在重跑项目检查和候选矩阵。

### 最终验收与交付依据

- 最终生产代码候选 `303d9563bfe9332e6a8af56e598e250f46231c35`：backend lint/mypy 全绿，3285 passed / 5 skipped；frontend 157 文件 / 1310 测试、lint/typecheck/build 全绿；Rust fmt、290 passed / 1 ignored、release check 全绿。后续仅整理验收文档。
- 真实产品内 MAA 有界复验：正式 Skill 通过 `exec_command` 的 Skill binding 执行既有 StartUp/Award task，进程与 wrapper 退出码均 0，两条链 Completed、无内部或子任务错误。最终回复后立即核验，再停止本次开发 runtime 并释放端口后复核：同一 MuMu PID、可恢复窗口、Android 与 ADB 均保持。此证据覆盖既有桌面实例复用，不把外部预先打开的动作算作产品新启动。
- 早期指定 `host_process_start` 执行受保护 Skill 文件的 pilot 被 `skill_binding_required` 拒绝，没有运行 MAA；改用公开支持的 `exec_command` Skill binding 后通过。另一次运行成功后因会话中断未即时取到存活快照，不以 19 小时后的进程状态判断当时结果；最终复验补齐这项证据。
- 验收日常限定为启动与领奖，不刷图、不用药、不切号。执行边界成功不等于物品级奖励保证，未用 Completed 推断奖励；历史奖励扫描只适用于其记录的游戏日。
- 私有核心证据清单 `final-evidence-manifest.json` SHA-256：`4d9bc68e635f3d161383ac31e5baf94737203cd89be99f1e917566a642df8aec`。原始对话、截图、账号与游戏日志不进入公开仓库。
- 修正候选 `20260917T141921Z-303d9563bfe9-ee4e9771`：staged doctor 与七轮 installed runtime doctor 通过；完整隔离矩阵 14/14 passed，`installer-smoke-verify` 通过。报告 `smoke-20260917T141921Z-303d9563bfe9-ee4e9771.json`；packageInputSha256 `ec0e81f0d1542af885c10f47eb26997c57a17896840894848c157060a0a40a0d`；候选安装器 SHA-256 `a970f77e3282d5524b66c1be5af7a89419786ff4c65d91c447f76c35ae282d5c`。失败首轮报告保留为历史，不用于交付。
- 普通桌面进程运行原 package runner，环境隔离检查保持启用。local-test/smoke 的 Guide r49 baseline 事务结束后已恢复，产品工作树无残留 overlay；未安装到 production。
- Guide 自动复核：自已发布用户知识对应的 `3a3535f9` 之后，仅新增验收脚本、报告、安装后 smoke 输入及回归测试，用户可见语义未再变化；继续绑定 immutable r49，无需另发公开知识版本。
- 用户明确授权必要验收及通过后合入、推送和本地测试包，正常交付不再次索要确认。验收完成；最终主干提交及其测试包由 Git 历史与制品 manifest 绑定。

### 合入后测试包的验证脚本竞态

- 主干 `c43ad283` 已推送，合入树与验收分支一致。随后按用户要求构建主干 local-test，build `20260917T145533Z-c43ad2831bee-c21fc44b` 的 doctor 在清理应用 fixture 临时目录时返回 WinError 32；未产生可交付安装器。
- 失败属于验证装置：fixture 的 `stopped` 文件在进程退出前写出，原 harness 将该标记当作退出证据；Windows 进程仍可能持有 cwd。启动、参数往返、持久证据步骤没有报告失败。此前 14/14 安装矩阵仍是其精确候选的有效历史证据，不伪称此次打包成功。
- 独立 fix 分支改为先取得本次 fixture 的只读同步进程句柄，发送 stop，再有界等待 OS 退出；超时明确失败，不关闭用户应用，不调整产品生命周期。延迟退出及超时两项真实进程回归通过；后端全量和新 frozen 验证继续执行。
- 本次脚本 contract 的 floor 为 package-sensitive，安装状态转换没有变化；新输入不沿用旧报告冒充匹配，按该 floor 验证冻结能力。Guide 用户知识语义不变，继续绑定已发布 r49。

- 竞态修复最终验证：backend lint/mypy 全绿、3287 passed / 5 skipped；新增延迟退出与超时两项真实进程回归通过。冻结 runtime `20260917T150818Z-f743a752a459-86d9e21c` 的完整 doctor 通过，临时 Guide baseline 已恢复、工作树干净。仅验证脚本变化，前端/Rust 与此前已通过的输入一致。继续按既有授权合入修复、推送并从最终主干构建测试包。
