---
name: package-release
description: 在 agent-friend 项目中查询或设置桌面产品版本，并构建受治理的 Windows 安装包。Use when 用户说“打安装包”“构建安装包”“本地测试包”“查当前/最新版本”“设置/升级版本”“package/release build”，或要求 ordinary、trusted-beta、local-test 安装包时使用；不要因单纯 commit、merge、push 或已完成验收而触发。
---

# Package Release

所有事实和约束以 `scripts/package/run.*` 的输出为准。不要自行拼接制品路径、比较版本、搜索替换版本字段，或运行裸 `pnpm tauri build` 冒充完整产品安装包。

## 入口

Windows 使用：

```powershell
.\scripts\package\run.ps1 version-status
.\scripts\package\run.ps1 version-set <semver>
.\scripts\package\run.ps1 trusted-beta-configure <next-stable-semver>
.\scripts\package\run.ps1 installer-contract
.\scripts\package\run.ps1 all --release [--smoke-report <PATH>]
.\scripts\package\run.ps1 all --trusted-beta [--smoke-report <PATH>]
.\scripts\package\run.ps1 all --local-test [--trusted-beta] [--smoke-report <PATH>]
```

macOS/Linux 使用同语义的 `./scripts/package/run.sh`。当前真正的 installer 构建只支持 Windows x64；版本命令不需要启动桌面端。

## 工作流

1. 先运行 `version-status` 和 `installer-contract`，向用户展示项目稳定版本、源码 dirty 状态、本机最新正式版本、trusted-beta 目标与下一自动候选版本、正式制品可构建状态、脚本解析出的目录、`impact.classification` 和 `verificationInputs.packageInputSha256`。不要自行覆盖脚本的安装影响分类，也不要用完整源码 hash 判断矩阵能否复用。
2. 产品稳定版本仍只接受用户明确给出的 SemVer，通过 `version-set` 同步。可信内测每个开发周期只配置一次用户明确给出的下一稳定版本：`trusted-beta-configure <next-stable-semver>`；脚本随后自动生成单调递增的 `<target>-beta.<UTC YYYYMMDD>.<sequence>`。目标缺失或过期时询问用户，不自行选择 major/minor/patch，也不联网查远端版本。
3. 根据用户意图选择构建模式：
   - 正式公开发布：只有用户明确要求 release 时使用 `all --release`；脚本要求稳定项目版本、clean worktree并执行版本与不可变制品门禁。裸 `all` 不是有效构建意图。
   - 可信内测分发：用户明确要求给可信测试者使用时使用 `all --trusted-beta`，不得加 `--local-test`；脚本从已配置目标自动分配预发布版本。提醒安装包可被提取长期凭据、不得公开发布。
   - 本机验证未提交代码：`all --local-test`；不得把 dirty 的 release/trusted-beta 静默降级为 local-test。只有验证凭据封装本身时才组合 `--local-test --trusted-beta`，该产物仍不可分发。
4. `impact.classification=installer-sensitive` 时，先确定是否已有当前工作流明确记录的 smoke report；不得搜索或猜测“最近报告”。把该显式报告交给目标 production 构建的 `--smoke-report`，只有脚本成功验证 schema、候选 manifest/build/hash、必需 scenario 与当前 `packageInputSha256` 后才可复用。无显式匹配报告时，构建 ordinary `installer-smoke` baseline/candidate，运行隔离矩阵并用候选 build ID 与 installer hash 验证报告：`installer-smoke-build` → `installer-smoke --smoke-build-report ...` → `installer-smoke-verify --smoke-report ... --candidate-build-id ... --candidate-installer ...`。矩阵必须覆盖同目录升级/修复、更高版本 mutation-free 拒绝、无 move intent 的目录漂移拒绝、显式程序根迁移及失败回滚，并确认用户数据、设置、日志和 keyring namespace 不参与迁移。旧 schema、指纹不匹配、scenario 失败、磁盘空间预检失败或必需场景缺失时停止，不得用 source 测试、文件存在检查、完整源码 hash、Agent 判断或 production `installed-smoke` 替代。
   - 安装器语言发生变化时，`installer-contract` 必须报告交互式 selector 为 `always`。可见 UI 验收同时覆盖无历史语言与已保存 English 的身份；仅在全新隔离身份看到一次 selector，不能证明真实产品升级仍允许选语言。
5. 调用脚本完成目标构建；installer-sensitive production `installer|all` 必须传入已验证的 `--smoke-report <PATH>`。打包 smoke 使用 fake/missing credential，不调用真实 LLM 或 provider API；自动变化的 trusted-beta 预发布版本不由 Agent 自行判定是否复用矩阵，仍只接受脚本对 `packageInputSha256` 和报告契约的验证。凭据变化只执行目标构建的凭据专项门禁，凭据值或 hash 不进入验证指纹。若另行执行真实对话/语音验收，遵守 `llm-api-confirm`。
6. 构建开始时核对脚本输出的 `package_version`、`release_channel` 和可选 `target_release_version`；回报绝对安装包路径、build ID、精确版本、模式、敏感标识和 SHA/manifest 核验结果。不得把同版本包描述为升级包，不得用 build ID 判断版本先后。失败时原样保留脚本的 fail-closed 原因，不绕过门禁。

## 模式边界

- `local-test` 使用 release 优化编译，但属于不可分发的本机测试制品；它不是 Tauri/Cargo debug profile。
- `trusted-beta` 是可分发给明确可信对象的敏感预发布渠道，不是 local-test，也不是稳定 release；正式制品一旦生成不可覆盖。
- stable release 版本来自同步的项目版本；trusted-beta 精确包版本由脚本从一次性配置的下一稳定版本派生。build ID 只负责构建追溯和完整性，不参与安装器新旧比较。
- `version-status` 的“最新版本”只来自本机有效正式 manifest；local-test、损坏目录和远端 GitHub Release 不参与。
- 分步重试只能使用脚本返回的 build ID 和 `--reuse-build-id`，不要猜测“最近一次”worktree 产物。
- `sourceTreeSha256` 用于完整源码追溯，`packageInputSha256` 用于隔离矩阵复用。即使前者因文档、Rule、Skill、进度或测试变化而改变，只要脚本确认后者一致，既有矩阵仍可复用；任何代码、打包输入、矩阵契约或构建环境变化都会使脚本拒绝旧报告。
- `installer-smoke` 只允许受治理的 `installer-smoke` profile，并在同一账户使用完全隔离的安装、注册表、快捷方式、启动项、数据、设置、日志、keyring 和端口 namespace；它不授权修改 production。
- 打包或 smoke 产生的旧 build 只能按精确 build ID 在受治理 work/release 根内清理或迁出；不得把低磁盘导致的文件截断当作安装器回归，也不得为了腾空间删除当前 baseline、candidate、源码或真实用户状态。
- 真实 production 覆盖升级不是普通 build 的隐含步骤。只有匹配候选的隔离 smoke report 和项目检查通过后，展示脚本 preflight、当前安装与候选身份，并为该候选的一次升级取得用户明确授权；未授权时保持停止。

