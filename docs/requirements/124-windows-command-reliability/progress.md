# Windows 命令执行可靠性与故障诊断 — 进度

## 总体状态

IN_PROGRESS

用户已确认 requirement/design 并授权实现。分支 `feature/124-windows-command-reliability`，基线 `1279a7d0487037e0fbaa03c55c51b3320b50c8a5`。

## 任务列表

- [x] RPC 网络决策与进程授权生命周期。
- [x] 沙箱状态结构、故障保留与修复探针。
- [x] 配套助手完整性和 frozen 加载检查。
- [x] 脱敏诊断、Bridge、Tauri 契约与 consumer 同步。
- [x] 错误呈现与恢复回归。
- [x] 项目自动化与 runtime-only frozen 验证（完整安装矩阵另列待验收）。

## AC 验证

- [ ] AC-1 网络授权与限制。
- [ ] AC-2 就绪状态准确。
- [ ] AC-3 初始化/修复/并发/重启。
- [ ] AC-4 助手完整性与加载。
- [ ] AC-5 工具、实时与历史结果一致。
- [ ] AC-6 诊断、脱敏与兼容。
- [ ] AC-7 Windows 10 与用户现场验证（待外部环境）。
- [ ] AC-8 项目检查与用户验收。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-09-11 | 确认方案、开始实现 | 尚无完成声明；未授权 production 安装或现场修改 |

| 2026-09-11 | 实现四条核心链路和新增诊断证据族；公开工具输入 schema 未变化 | 初轮专项 141 通过/2 个旧夹具失败；更新夹具后 162 通过；后续补充测试与全量检查进行中 |
| 2026-09-11 | 项目 adapter sync/doctor | 均通过；未修改个人配置 |
| 2026-09-11 | Guide package-baseline | r39 / 32e79ffc8d9ba1480fbf21bf42af409ec044e45c，GitHub immutable=true，verify 通过；snapshot 3312a888016f760d59998097fe9dc36ae6a2e5016f3559f32e330913ea424c9b |

## 验证边界

- Guide 当前目标是 HEAD + 工作区预览；基线 reviewed_product_commit=26a3256f11ba013fa40c71b4caa46d4bd95d85c2，已确认祖先关系。新增有界诊断、助手加载与修复验证将影响 data-and-privacy.md、troubleshooting.md、settings-reference.md；package-baseline 不授权公开修改或发布，本轮不编辑 Guide。
- package installer-contract 默认比较范围包含之前已合入的安装相关变化，脚本 floor=full-matrix；不降低该 floor。当前 runtime-only 只验证冻结能力，完整矩阵仍为合入前未完成项。
- Windows 10 干净环境、用户现场恢复和真实桌面视觉未执行。无真实 LLM 调用，也不宣称 Agent 行为泛化已经验证。

## 本轮自动化结果

- backend 全量：lint、mypy（677 文件）通过；3,030 passed / 5 skipped。第三方清理回调和日志协程警告未影响退出码。
- frontend 全量：lint、typecheck、153 测试文件 / 1,256 测试和生产构建通过；保留既有 CSS / chunk-size 警告。
- Rust：fmt、test、release check 通过；初轮发现代表性 bundle fixture 缺新条目，修正后复验通过。
- 专项回归：185 passed；最后 helper 无参数探针和故障分类调整后，再跑 vendor/reliability 26 passed；最终 lint/typecheck 通过。
- 冻结 runtime：local-test/runtime 成功，固定 Guide r39；PyInstaller 403.3 秒，runtime-stage-and-smoke 303.0 秒，退出码 0。
- AC-2/5/6 的确定性机制已有测试覆盖；AC-1/3 的真实受管网络、沙箱修复和 AC-7 用户现场仍待实测，不用 mock 通过代替。

## 冻结验证证据

- build_id：`20260911T134343Z-1279a7d04870-3ef83bc7`，版本 0.3.0，Windows 11 10.0.26200；来源为本 feature HEAD + 本轮工作区实现。
- 产物：`C:/Users/example-user/AppData/Local/agent-friend/packaging/work/4aa1f81a6164/73a336d8c9be/20260911T134343Z-1279a7d04870-3ef83bc7/runtime-stage`。
- 命令：`scripts/package/run.ps1 runtime --local-test --verification-level package-sensitive --guide-repo C:/Users/example-user/AppData/Local/agent-friend/maintenance/agent-friend-guide --guide-commit 32e79ffc8d9ba1480fbf21bf42af409ec044e45c`。
- doctor `ok=true`：配套助手哈希和无参数加载检查通过；真实 `windowsRestrictedToken` 命令输出 frozen-command-ok，流式输入、只读拒写、技能路径、取消和子进程清理通过；managed Python ready。
- Bridge/voice frozen service smoke 通过，整个 runtime 入口退出码 0。未运行 installer 构建/安装，也未执行外部 LLM 请求。
- local-test Guide 临时 overlay 已恢复：builtin guide 路径 Git diff 为空。最终 `git diff --check` 通过，仅行尾转换提示。
- 脚本 effective floor 仍为 full-matrix；runtime-only 成功不表示完整安装矩阵已执行。
- AC-4 本机正常 frozen candidate 证据齐备，缺失/篡改/异常启动分支由自动化覆盖；用户机器上的具体缺失模块仍未知。

## 下一步验收

- 在对应 Windows 10 环境验证完整候选安装产物及受管网络允许/拒绝链路。
- 获得现场候选安装和修复操作授权后，记录用户电脑的失败、显式修复、命令探针和原操作独立重试结果；导出新诊断包核对 build/阶段/错误码。
- 完成项目要求的完整安装矩阵和用户验收后再收口。本轮实现与本机验证完成，整体状态继续 IN_PROGRESS；未提交或合入实现。
## 用户交付决定（2026-09-11）

用户明确要求先将当前实现合入主干并推送，再登记剩余现场问题为 open issue；随后由用户自行打包给受影响用户。本次按该明确决定先交付已实现部分，Windows 10、完整安装矩阵、真实联网和现场恢复证据继续待补，不将需求或原始弹窗问题标记完成。此决定覆盖本次先合入的顺序，不代表未执行的验收已通过。

## 实际交付结果

- 实现提交 e43213e26310e5ad54248e564a16c044b03175ae；合并提交 2969849bff44e56e385c846740b394b72e33db3e 已推送 origin/main。上文未提交/未合入描述是实施时状态，以本节为最新结果。
- 合入时 origin/main 已包含需求 125 的身份提示词改动，无重叠文件、无合并冲突；本次改动内容保持不变。未重新构建安装包，既有 frozen 证据只绑定对应 feature 实现。
- Guide 自动维护完成：r40，来源 78af5946b5345ef97635d41efc22153e34a4d6d9，immutable=true；validator 和 verify 通过，builtin 未同步。
- 剩余工作移交 [issue 066](../../issues/066-windows-sandbox-field-recovery/README.md)，保持 open；用户自行后续打包，需求仍为 IN_PROGRESS。
