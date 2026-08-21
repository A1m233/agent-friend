---
name: package-release
description: 在 agent-friend 项目中查询或设置桌面产品版本、构建受治理的 Windows 安装包，并盘点或清理打包工作区。Use when 用户说“打安装包”“构建安装包”“本地测试包”“查当前/最新版本”“设置/升级版本”“package/release build”“查看/清理打包缓存或工作区”，或要求 ordinary、trusted-beta、local-test 安装包时使用；不要因单纯 commit、merge、push 或已完成验收而触发。
---

# Package Release

所有事实和约束以 `scripts/package/run.*` 的输出为准。不要自行拼接制品路径、比较版本、搜索替换版本字段，或运行裸 `pnpm tauri build` 冒充完整产品安装包。

## 入口

Windows 使用：

```powershell
.\scripts\package\run.ps1 version-status
.\scripts\package\run.ps1 workspace-status
.\scripts\package\run.ps1 workspace-gc [--apply]
.\scripts\package\run.ps1 version-set <semver>
.\scripts\package\run.ps1 trusted-beta-configure <next-stable-semver>
.\scripts\package\run.ps1 installer-contract
.\scripts\package\run.ps1 all --release --guide-repo <PATH> --guide-commit <SHA> --smoke-report <PATH>
.\scripts\package\run.ps1 all --trusted-beta --guide-repo <PATH> --guide-commit <SHA> [--verification-level <LEVEL>] [--smoke-report <PATH>]
.\scripts\package\run.ps1 all --local-test --guide-repo <PATH> --guide-commit <SHA> [--trusted-beta] [--verification-level <LEVEL>] [--smoke-report <PATH>]
```

macOS/Linux 使用同语义的 `./scripts/package/run.sh`。当前真正的 installer 构建只支持 Windows x64；版本命令不需要启动桌面端。

## 工作流

1. 先运行 `version-status` 和 `installer-contract`，向用户展示项目稳定版本、源码 dirty 状态、本机最新正式版本、trusted-beta 目标与下一自动候选版本、正式制品可构建状态、脚本解析出的目录、`impact.packageRequired`、`impact.minimumVerificationLevel` 和 `verificationInputs.packageInputSha256`。不要自行降低脚本给出的最低等级。若当前目标包含安装到本机 production，必须在任何实际构建前按同一渠道参数运行 `production-install-plan`；它读取真实安装注册表、active receipt 与 build identity，确定 `absent / upgrade / repair / downgrade-blocked`。不得用 `version-status` 的制品库版本代替真实安装身份。
2. 产品稳定版本仍只接受用户明确给出的 SemVer，通过 `version-set` 同步。可信内测每个开发周期只配置一次用户明确给出的下一稳定版本：`trusted-beta-configure <next-stable-semver>`；脚本随后自动生成单调递增的 `<target>-beta.<UTC YYYYMMDD>.<sequence>`。目标缺失或过期时询问用户，不自行选择 major/minor/patch，也不联网查远端版本。
3. 独立判断是否需要产包。一级改动默认不自动打包；只有用户要求测试包、AC 要求真实安装、准备交付用户或 release 时才产包。二级/三级代码改动即使用户没有要求分发包，也必须在合入前构建验证 candidate。然后根据用户意图选择渠道：
   - 正式公开发布：只有用户明确要求 release 时使用 `all --release`；脚本要求稳定项目版本、clean worktree并执行版本与不可变制品门禁。裸 `all` 不是有效构建意图。
   - 可信内测分发：用户明确要求给可信测试者使用时使用 `all --trusted-beta`，不得加 `--local-test`；脚本从已配置目标自动分配预发布版本。提醒安装包可被提取长期凭据、不得公开发布。
   - 本机验证未提交代码：`all --local-test`；不得把 dirty 的 release/trusted-beta 静默降级为 local-test。只有验证凭据封装本身时才组合 `--local-test --trusted-beta`，该产物仍不可分发。
4. 按最低验证等级执行：
   - `standard`：如果决定产包，执行一次标准完整构建及其内置确定性检查；普通需求未要求包时不打包。
   - `package-sensitive`：合入前至少构建真实 frozen candidate，并以 doctor/capability smoke 为证据；用户需要完整包时再继续 installer。语义上需要但脚本未自动识别时用 `--verification-level package-sensitive` 升档。
   - `full-matrix`：先确定是否已有当前工作流明确记录且指纹匹配的 smoke report；不得搜索或猜测“最近报告”。无匹配报告时运行 `installer-smoke-build` → `installer-smoke` → `installer-smoke-verify`。默认只构建一次 candidate 并由它提供受校验 fixture；只有显式给出已验证 baseline build ID 时才复用历史 baseline。矩阵覆盖升级/修复、降级拒绝、目录漂移/迁移/回滚、进程占用、失败恢复和身份错配。stable release 自动升到该级，不能跳过矩阵。
   - 安装器语言发生变化时，`installer-contract` 必须报告交互式 selector 为 `always`。可见 UI 验收同时覆盖无历史语言与已保存 English 的身份；仅在全新隔离身份看到一次 selector，不能证明真实产品升级仍允许选语言。
5. 在当前工作流第一次执行任何实际构建前，自动显式调用 `maintain-agent-friend-guide` 的 `package-baseline` 模式，完成只读知识检查并解析最新兼容、已发布且 immutable 的 Guide checkout、commit 与 snapshot；不要再询问用户是否进入该流程。把返回的 checkout 和 commit 作为 `--guide-repo`、`--guide-commit` 传给每次 `runtime`、`installer`、`all`、`installer-smoke-build` 构建；内部 candidate 子构建通过受校验 receipt 继承，不自行重新选择版本。`version-status`、`version-set`、`trusted-beta-configure`、`installer-contract`、`workspace-status` 和 `workspace-gc` 等只读/配置动作不触发。自动入口不授权修改公开知识、创建 release 或提交产品仓库；发现公开内容需要更新时只报告。
   - `local-test` 与 installer smoke 的 baseline 是临时打包输入。package runner 负责在同一事务内应用并在成功、失败或正常中断后恢复进入前 Git 状态；无关 dirty 必须原样保留，Guide 管理路径预先 dirty 时 fail closed。Agent 不直接运行持久化 `sync`，也不在收尾时留下 baseline overlay。
   - stable release 与可分发 trusted-beta 只接受已经提交的精确 baseline。package runner 发现不匹配时在构建前返回 `baseline-update-required` 且不修改工作树；随后只有在当前 Git 授权覆盖时，才在任务自有分支用 maintainer `sync` 生成独立 baseline changeset，完成提交后从 clean commit 重新构建。
6. 调用脚本完成目标构建。准备安装到当前机器时，production `installer|all` 必须附带 `--for-current-install`，让脚本在创建 build workspace 和冻结 runtime 前重新执行同一安装计划并 fail closed；`--trusted-beta --local-test` 使用受治理的下一 beta 版本，`--local-test` 只改变可发布性，不得把候选版本降回项目稳定版本。脚本计算 `effective_verification_level=max(代码 floor, release floor, 用户升档)`；用户传入较低等级不会降档。`full-matrix` production `installer|all` 必须传入已验证的 `--smoke-report <PATH>`。打包 smoke 使用 fake/missing credential，不调用真实 LLM 或 provider API；凭据值或 hash 不进入验证指纹。若另行执行真实对话/语音验收，遵守 `llm-api-confirm`。
7. 构建开始时核对脚本输出的 `package_version`、`release_channel` 和可选 `target_release_version`；回报绝对安装包路径、build ID、精确版本、模式、敏感标识和 SHA/manifest 核验结果。不得把同版本包描述为升级包，不得用 build ID 判断版本先后。失败时原样保留脚本的 fail-closed 原因，不绕过门禁。

同一任务内按 `product_state_summary + package:<channel>` 去重 Guide 检查与 baseline 刷新：相同产品 HEAD、tracked diff、untracked 文件集合/内容和相同渠道已经检查或维护后，不重复执行；产品状态实质变化后重新检查。用户直接要求检查或更新知识时直接进入 maintainer Skill。

## 打包工作区治理

- 所有 worktree 的 package build 使用仓库级互斥锁；锁冲突时报告占用，不绕过或手工删除 lock。
- `workspace-status` 只读输出全部 worktree 的 build-id、共享增量 cache、注册状态、保留策略和预计可回收字节。
- `workspace-gc` 默认 dry-run；只有用户明确要求执行清理后才加 `--apply`。默认回收超过 48 小时的 build-id，以及超过 7 天未使用的 per-worktree Cargo/PyInstaller cache。
- GC 只处理脚本解析出的 `%LOCALAPPDATA%/agent-friend/packaging/work/<repo>/<worktree>/`；不删除 source worktree、正式或 local-test release、下载 cache、用户数据、设置或日志。
- `runtime` 必须保留 build-id 供显式 `installer --reuse-build-id` 使用；成功完成 installer/all 后自动删除该 build-id。installer smoke 的 candidate 与可选历史 baseline 保留到 smoke 成功，之后自动回收。
- package build 在创建工作区前要求 state 所在卷至少有 40 GiB 可用空间。空间不足时先运行 status/GC，或显式把 `--state-dir` 指向容量足够的其它卷；不得降低门禁伪造通过。

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

