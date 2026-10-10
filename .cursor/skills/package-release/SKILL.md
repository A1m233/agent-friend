---
name: package-release
description: 在 agent-friend 项目中查询或设置桌面产品版本、构建受治理的 Windows 安装包，并盘点或清理打包工作区。Use when 用户说“打安装包”“构建安装包”“本地测试包”“查当前/最新版本”“设置/升级版本”“package/release build”“查看/清理打包缓存或工作区”，要求 ordinary、trusted-beta、local-test 安装包，或 frozen-runtime-packaging Rule 明确调用 runtime-only 验证时使用；不要因单纯 commit、merge、push 或已完成验收而触发。
---

# Package Release

所有事实和约束以 `scripts/package/run.*` 的输出为准。不要自行拼接制品路径、比较版本、搜索替换版本字段，或运行裸 `pnpm tauri build` 冒充完整产品安装包。

## 入口

Windows 使用：

```powershell
.\scripts\package\run.ps1 version-status
.\scripts\package\run.ps1 workspace-status [--scan]
.\scripts\package\run.ps1 workspace-gc [--scan] [--apply]
.\scripts\package\run.ps1 plan [--purpose <STEP>] [--state-dir <PATH>] [--releases-dir <PATH>]
.\scripts\package\run.ps1 version-set <semver>
.\scripts\package\run.ps1 trusted-beta-configure <next-stable-semver>
.\scripts\package\run.ps1 installer-contract
.\scripts\package\run.ps1 installer-smoke-build --guide-repo <PATH> --guide-commit <SHA>
.\scripts\package\run.ps1 installer-smoke --guide-repo <PATH> --guide-commit <SHA> --smoke-build-report <PATH>
.\scripts\package\run.ps1 installer-smoke-verify --guide-repo <PATH> --guide-commit <SHA> --smoke-report <PATH> --candidate-build-id <ID> --candidate-installer <PATH>
.\scripts\package\run.ps1 all --release --guide-repo <PATH> --guide-commit <SHA> --smoke-report <PATH>
.\scripts\package\run.ps1 all --trusted-beta --guide-repo <PATH> --guide-commit <SHA> [--verification-level <LEVEL>] [--smoke-report <PATH>]
.\scripts\package\run.ps1 all --local-test --guide-repo <PATH> --guide-commit <SHA> [--trusted-beta] [--verification-level <LEVEL>] [--smoke-report <PATH>]
```

macOS/Linux 使用同语义的 `./scripts/package/run.sh`。当前真正的 installer 构建只支持 Windows x64；版本命令不需要启动桌面端。

## 首次实际构建的共同前置

本节由 `runtime-only` 与安装包工作流共同使用；在第一次执行任何实际构建（包括 `installer-smoke-build`）前完成。`version-status`、`version-set`、`trusted-beta-configure`、`installer-contract`、`workspace-status` 和 `workspace-gc` 等只读/配置动作不触发。

自动显式调用 `maintain-agent-friend-guide` 的 `package-baseline` 模式，完成只读知识检查并解析最新兼容、已发布且 immutable 的 Guide checkout、commit 与 snapshot；不要再询问用户是否进入该流程。把返回的 checkout 和 commit 作为 `--guide-repo`、`--guide-commit` 传给每次 `runtime-verify`、`runtime`、`installer`、`all`、`installer-smoke-build`、`installer-smoke` 和 `installer-smoke-verify`；内部 candidate 子构建通过受校验 receipt 继承，不自行重新选择版本。自动入口不授权修改公开知识、创建 release 或提交产品仓库；发现公开内容需要更新时只报告。

- `local-test` 与 installer smoke 的 baseline 是临时打包输入。package runner 负责在同一事务内应用并在成功、失败或正常中断后恢复进入前 Git 状态；无关 dirty 必须原样保留，Guide 管理路径预先 dirty 时 fail closed。Agent 不直接运行持久化 `sync`，也不在收尾时留下 baseline overlay。
- stable release 与可分发 trusted-beta 只接受已经提交的精确 baseline。package runner 发现不匹配时在构建前返回 `baseline-update-required` 且不修改工作树；随后只有在当前 Git 授权覆盖时，才在任务自有分支用 maintainer `sync` 生成独立 baseline changeset，完成提交后从 clean commit 重新构建。

同一任务内按 `product_state_summary + package:<channel>` 去重 Guide 检查与 baseline 刷新：相同产品 HEAD、tracked diff、untracked 文件集合/内容和相同渠道已经检查或维护后，不重复执行；产品状态实质变化后重新检查。用户直接要求检查或更新知识时直接进入 maintainer Skill。

## 仅验证冻结 runtime（runtime-only）

由 `frozen-runtime-packaging` Rule 明确要求验证打包敏感变更时，使用本路径；用户只要求构建 runtime 时也保持这一范围。它不进入下方安装包工作流，不修改版本、不构建 installer、不安装或发布。

1. 确认 Windows x64 环境和 Rule 要求的后端检查；运行 `version-status`、`installer-contract` 核对源码状态、打包目录与验证等级，不降低脚本给出的 floor。
2. 完成“首次实际构建的共同前置”，取得本次验证的精确 Guide 输入，再执行：

   ```powershell
   .\scripts\package\run.ps1 runtime-verify --local-test --verification-level package-sensitive --guide-repo <PATH> --guide-commit <SHA>
   ```

3. 以 staging 后 doctor 和 frozen capability smoke 为结果，回报 build ID、持久验证报告、精确版本与验证状态，然后返回原任务。纯验证成功后中间 runtime 会回收，不把已经删除的 runtime 路径作为交付物；需要稍后继续 installer 时明确使用保留型 `runtime`。若代码 floor 另要求 `full-matrix`，说明本次 runtime 证据只覆盖冻结能力，剩余安装器验收仍由原工作流按 `windows-installer-integrity` Rule 处理，不能把本路径当成完整验收。

## 安装包工作流

1. 先运行 `version-status` 和 `installer-contract`，向用户展示项目稳定版本、源码 dirty 状态、本机最新正式版本、trusted-beta 目标与下一自动候选版本、正式制品可构建状态、脚本解析出的目录、`impact.packageRequired`、`impact.minimumVerificationLevel` 和 `verificationInputs.packageInputSha256`。不要自行降低脚本给出的最低等级。若当前目标包含安装到本机 production，必须在任何实际构建前按同一渠道参数运行 `production-install-plan`；它读取真实安装注册表、active receipt 与 build identity，确定 `absent / upgrade / repair / downgrade-blocked`。不得用 `version-status` 的制品库版本代替真实安装身份。
2. 产品稳定版本仍只接受用户明确给出的 SemVer，通过 `version-set` 同步。可信内测每个开发周期只配置一次用户明确给出的下一稳定版本：`trusted-beta-configure <next-stable-semver>`；脚本随后自动生成单调递增的 `<target>-beta.<UTC YYYYMMDD>.<sequence>`。目标缺失或过期时询问用户，不自行选择 major/minor/patch，也不联网查远端版本。
3. 独立判断是否需要产包。一级改动默认不自动打包；只有用户要求测试包、AC 要求真实安装、准备交付用户或 release 时才产包。二级/三级代码改动即使用户没有要求分发包，也必须在合入前构建验证 candidate。然后根据用户意图选择渠道：
   - 正式公开发布：只有用户明确要求 release 时使用 `all --release`；脚本要求稳定项目版本、clean worktree并执行版本与不可变制品门禁。裸 `all` 不是有效构建意图。
   - 可信内测分发：用户明确要求给可信测试者使用时使用 `all --trusted-beta`，不得加 `--local-test`；脚本从已配置目标自动分配预发布版本。提醒安装包可被提取长期凭据、不得公开发布。
   - 本机验证未提交代码：`all --local-test`；不得把 dirty 的 release/trusted-beta 静默降级为 local-test。只有验证凭据封装本身时才组合 `--local-test --trusted-beta`，该产物仍不可分发。
4. 先完成“首次实际构建的共同前置”，再按最低验证等级执行：
   - `standard`：如果决定产包，执行一次标准完整构建及其内置确定性检查；普通需求未要求包时不打包。
   - `package-sensitive`：合入前至少构建真实 frozen candidate，并以 doctor/capability smoke 为证据；用户需要完整包时再继续 installer。语义上需要但脚本未自动识别时用 `--verification-level package-sensitive` 升档。
   - `full-matrix`：先确定是否已有当前工作流明确记录且指纹匹配的 smoke report；不得搜索或猜测“最近报告”。无匹配报告时运行 `installer-smoke-build` → `installer-smoke` → `installer-smoke-verify`，三个步骤都传入本次 package-baseline 的同一组 `--guide-repo` / `--guide-commit`，确保构建和验证重算的是同一 package input。默认只构建一次 candidate 并由它提供受校验 fixture；只有显式给出已验证 baseline build ID 时才复用历史 baseline。矩阵覆盖升级/修复、降级拒绝、目录漂移/迁移/回滚、进程占用、失败恢复和身份错配。stable release 自动升到该级，不能跳过矩阵。
   - 安装器语言发生变化时，`installer-contract` 必须报告交互式 selector 为 `always`。可见 UI 验收同时覆盖无历史语言与已保存 English 的身份；仅在全新隔离身份看到一次 selector，不能证明真实产品升级仍允许选语言。
5. 调用脚本完成目标构建。准备安装到当前机器时，production `installer|all` 必须附带 `--for-current-install`，让脚本在创建 build workspace 和冻结 runtime 前重新执行同一安装计划并 fail closed；`--trusted-beta --local-test` 使用受治理的下一 beta 版本，`--local-test` 只改变可发布性，不得把候选版本降回项目稳定版本。脚本计算 `effective_verification_level=max(代码 floor, release floor, 用户升档)`；用户传入较低等级不会降档。`full-matrix` production `installer|all` 必须传入已验证的 `--smoke-report <PATH>`。打包 smoke 使用 fake/missing credential，不调用真实 LLM 或 provider API；凭据值或 hash 不进入验证指纹。若另行执行真实对话/语音验收，遵守 `llm-api-confirm`。
6. 构建开始时核对脚本输出的 `package_version`、`release_channel` 和可选 `target_release_version`；回报绝对安装包路径、build ID、精确版本、模式、敏感标识和 SHA/manifest 核验结果。不得把同版本包描述为升级包，不得用 build ID 判断版本先后。失败时原样保留脚本的 fail-closed 原因，不绕过门禁。

## 打包工作区治理

- 发布前必须成功生成私有 native symbols 归档（桌面 EXE、匹配 GUID/age 的 PDB、代码段身份及构建元数据），release manifest 绑定归档 SHA。入口自动校验；失败不可绕过。归档位于 releases 根的 `_symbols/windows-x64/<profile>/<build-id>/`，独立于 workspace GC，不能随缓存删除或公开。trusted-beta 归档含敏感 EXE；诊断包永不附带符号。离线定位使用 `scripts/crash-diagnostics/run.* symbolize --dump <PATH> --symbols <ARCHIVE_DIR> --output <NEW_JSON>`，不自动下载符号。

- 先用 `package run.* plan` 查看实际目录、卷空间与受管缓存策略；计划不代表 Guide、smoke 或正式发布门禁已经通过，实际 runner 会重新核验。
- `workspace-status` 是轻量只读查询，不初始化项目虚拟环境；默认展示已有统计及历史未接管目录。需要精确体积时显式加 `--scan`，关注完整性和统计时间，不把 unknown 当作零。
- `workspace-gc` 默认只展示 package 类别的回收计划；用户明确要求清理后才加 `--apply`。不会删除源码、安装包（含 local-test）、manifest、native symbols、下载缓存或用户资产。
- 开发、Rust 检查和专项回归缓存使用 `scripts/build-storage/run.*` 的统一管理入口；具体参数与范围以该入口和 `scripts/README.md` 为准，不自行拼接删除路径。
- 自动回收默认关闭。用户明确配置并启用指定类别后，后续标准构建可按该持久策略执行；不因用户要求产包或实现需求就替其启用，也不重复索要仍有效的同范围授权。
- 旧目录只能先盘点和识别；`workspace-adopt --path <精确目录>` 默认预览，显式 `--apply` 后登记并保护。确认旧消费者已停止后才按对象 ID 解除保护，不凭目录年龄推断空闲。
- `runtime-verify` 用于纯冻结验证，成功后保留报告并回收本次中间目录；`runtime` 用于等待 `installer --reuse-build-id`，按脚本给出的期限保留。失败现场和显式 pin 也遵循同一可查询记录。
- 构建与清理采用稳定控制目录中的互斥保护；更换 state root 不能绕过。锁冲突只报告，不删除 lock 或强杀未知进程。
- 空间门槛、预算、保留期限及实际可回收量全部以脚本计划为准；多卷或并发预留可能使需要的空间高于原 state 单卷门槛。空间不足时报告缺口，未经授权不换盘或扩大回收范围。
- 中断操作按 `build-storage status` 的对象和 reservation 记录定位；只有核实整棵子进程树停止后，才使用显式 reconcile 入口解除保护。不得把 PID 消失当作所有后代已经退出。

## 模式边界

- `local-test` 使用 release 优化编译，但属于不可分发的本机测试制品；它不是 Tauri/Cargo debug profile。
- `local-test` 的 Guide baseline 只进入制品，不成为产品工作树的持久修改；release manifest 记录精确 Guide commit、snapshot digest 与 lifecycle。构建返回后 Git 状态必须与进入 baseline 事务前一致。
- `trusted-beta` 是可分发给明确可信对象的敏感预发布渠道，不是 local-test，也不是稳定 release；正式制品一旦生成不可覆盖。
- stable release 版本来自同步的项目版本；trusted-beta 精确包版本由脚本从一次性配置的下一稳定版本派生。build ID 只负责构建追溯和完整性，不参与安装器新旧比较。
- `version-status` 的“最新版本”只来自本机有效正式 manifest；local-test、损坏目录和远端 GitHub Release 不参与。真实安装兼容性只以 `production-install-plan` 为准。
- 分步重试只能使用脚本返回的 build ID 和 `--reuse-build-id`，不要猜测“最近一次”worktree 产物。
- `sourceTreeSha256` 用于完整源码追溯，`packageInputSha256` 与 candidate build/hash 用于验证证据复用。输入未变时可显式复用当前工作流记录的证据；脚本拒绝旧 schema、输入、环境、build 或 installer hash 不匹配的报告。
- `installer-smoke` 只允许受治理的 `installer-smoke` profile，并在同一账户使用完全隔离的安装、注册表、快捷方式、启动项、数据、设置、日志、keyring 和端口 namespace；它不授权修改 production。
- 打包或 smoke 产生的旧 build 只能按精确 build ID 在受治理 work/release 根内清理或迁出；不得把低磁盘导致的文件截断当作安装器回归，也不得为了腾空间删除当前 baseline、candidate、源码或真实用户状态。
- 真实 production 覆盖升级不是普通 build 的隐含步骤。只有匹配候选的隔离 smoke report 和项目检查通过后，展示脚本 preflight、当前安装与候选身份，并为该候选的一次升级取得用户明确授权；未授权时保持停止。

