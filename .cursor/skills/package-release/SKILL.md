---
name: package-release
description: 在 agent-friend 项目中查询或设置桌面产品版本，并构建受治理的 Windows 安装包。Use when 用户说“打安装包”“构建安装包”“本地测试包”“查当前/最新版本”“设置/升级版本”“package/release build”，或要求 ordinary、trusted-beta、local-test 安装包时使用。
---

# Package Release

所有事实和约束以 `scripts/package/run.*` 的输出为准。不要自行拼接制品路径、比较版本、搜索替换版本字段，或运行裸 `pnpm tauri build` 冒充完整产品安装包。

## 入口

Windows 使用：

```powershell
.\scripts\package\run.ps1 version-status
.\scripts\package\run.ps1 version-set <semver>
.\scripts\package\run.ps1 all [--local-test] [--trusted-beta]
```

macOS/Linux 使用同语义的 `./scripts/package/run.sh`。当前真正的 installer 构建只支持 Windows x64；版本命令不需要启动桌面端。

## 工作流

1. 先运行 `version-status`，向用户展示项目版本、源码 dirty 状态、本机最新正式版本、正式制品可发布状态和脚本解析出的目录。
2. 用户要求改版本时，只把用户明确给出的 SemVer 传给 `version-set`。未给版本时询问，不自行选择 major/minor/patch，也不联网查远端版本。
3. 根据用户意图选择构建模式：
   - 可分发正式包：默认 `all`；脚本要求 clean worktree并执行版本与不可变制品门禁。
   - 本机验证未提交代码：`all --local-test`；不得把 dirty 的正式构建静默降级为 local-test。
   - 可信内测敏感包：仅在用户明确要求后加 `--trusted-beta`，并提醒安装包可被提取长期凭据、不得公开发布。
4. 调用脚本完成构建。打包 smoke 使用 fake/missing credential，不调用真实 LLM 或 provider API；若另行执行真实对话/语音验收，遵守 `llm-api-confirm`。
5. 回报脚本输出的绝对安装包路径、build ID、版本、模式、敏感标识和 SHA/manifest 核验结果。失败时原样保留脚本的 fail-closed 原因，不绕过门禁。

## 模式边界

- `local-test` 使用 release 优化编译，但属于不可分发的本机测试制品；它不是 Tauri/Cargo debug profile。
- ordinary 与 trusted-beta 可以共享产品版本，但各自的正式制品一旦发布都不可覆盖。
- `version-status` 的“最新版本”只来自本机有效正式 manifest；local-test、损坏目录和远端 GitHub Release 不参与。
- 分步重试只能使用脚本返回的 build ID 和 `--reuse-build-id`，不要猜测“最近一次”worktree 产物。

