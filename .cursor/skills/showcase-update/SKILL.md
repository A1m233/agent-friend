---
name: showcase-update
description: 按用户意图检查、生成或同步、预览、提交或发布 agent-friend 的公开 showcase。当用户要求验证现有 showcase、更新 sanitized snapshot、运行或发布 agent-friend-showcase checkout 时使用；只执行当前请求需要的路径。
---

# Showcase 维护

私有 agent-friend 仓库是生成源，公开 showcase 是经过脱敏的快照。先识别用户需要的动作；多个动作只有在当前请求明确包含它们，或属于该动作的必要前置时才组合。

## 入口与边界

- 从用户指定路径、当前工作区和 Git 身份确认私有源仓库及目标 checkout；worktree 的相邻目录不一定是目标，不默认使用 `../agent-friend-showcase`。
- 本文的快照脚本从私有源仓库执行，运行态脚本从目标 showcase 执行。执行前查看对应工作区 Git 状态。
- 不复制凭据、`.env`、用户数据和本地运行缓存。扫描 errors 必须为 0；warnings 逐项判断。扫描只覆盖脚本支持的规则，发布前仍需核对 diff 中的公开内容。
- 真实 LLM/API 调用遵守个人 `llm-api-confirm`；启动桌面端不能被描述为保证不启动后台服务或不调用外部 API。

## 路径一：检查现有 checkout

用户只说“验证 / 检查现有 showcase”时，直接检查现有内容：

```powershell
.\scripts\showcase-snapshot\run.ps1 --verify-checkout <showcase-path>
```

```bash
./scripts/showcase-snapshot/run.sh --verify-checkout <showcase-path>
```

该入口允许 dirty checkout，扫描当前 tracked 和未被 Git 忽略的 untracked 文件，输出到控制台，不生成新快照、不改写报告或 checkout。双端包装只复用已有 Python，不同步源项目依赖或下载解释器；缺少 Python 时先报告环境缺口。报告扫描范围、错误和未验证部分；检查请求本身不启动 setup、桌面端、提交或同步。

如果用户要求的验证还包括某项测试或运行时行为，追加与该目标直接相关的现有脚本；需要预览时进入路径三。

## 路径二：生成或同步快照

只生成独立快照时使用 `--output <snapshot-path>`；已有快照需要覆盖时确认它确属本次生成产物，再使用 `--replace`。只有用户要更新已有 showcase checkout 时使用：

```powershell
.\scripts\showcase-snapshot\run.ps1 --sync-checkout <showcase-path>
```

```bash
./scripts/showcase-snapshot/run.sh --sync-checkout <showcase-path>
```

runner 要求目标是带 showcase sentinel 的 Git 根目录，且没有 staged、unstaged、删除或非 ignored 的 untracked 改动。它在构建前检查，并在覆盖前再次检查；源快照与目标不得重合或互相包含。扫描通过后才镜像，目标中不再属于快照的文件会被删除。

如果目标 dirty，列出精确路径及当前冲突，不自动 stash、提交、丢弃或替用户选择哪个版本。已有明确处理决定时按该决定继续；缺少决定时先取得决定，不能绕过 runner 的检查。

同步保留 `.git`、`.venv`、`.tmp`、`.env`、`.agent-friend-data`、`.showcase-run` 和 `node_modules`；这些保留项不豁免 tracked 改动的 dirty 检查。同步后核对 Git diff 和本次生成的 `SHOWCASE-SCAN-REPORT.md`。更新请求本身不要求安装依赖、预览或发布。

## 路径三：预览或运行

用户要求看效果或运行时，在已确认的 showcase checkout 中执行 `scripts/dev/run.* status`，按项目 `desktop-startup-default` 核对运行实例身份、源码新鲜度与生命周期，再启动或复用正确实例。

- 默认入口是 `scripts/dev/run.ps1` / `scripts/dev/run.sh` 的 Tauri 模式；web 的适用例外由该 Rule 定义。
- 仅在缺少运行所需依赖时执行目标 checkout 的 `scripts/setup/run.*`。预览现有内容不先重新生成或同步。
- 需要截图、DOM 或真实桌面交互结论时，按 `frontend-visual-verification` 与 `desktop-visual-inspection` 的触发和授权边界执行。
- 用户未要求保留时，结束后停止本次自行启动的 runtime；复用用户已有实例时遵守对应生命周期规则。

## 路径四：提交或发布

用户明确要求提交 / 推送现有 showcase 时，先用路径一检查当前待提交内容，再检查 Git 状态、diff、目标分支与远端。当前请求已经授权的正常 commit / push 不再重复确认；来源或范围不明的改动按个人 Git 规则处理。

提交前调用 `gen-commit-message`。提交或发布不隐含重新同步、setup 或桌面预览；只有用户要求更新并发布时，才先执行路径二，再检查生成结果并完成已授权的提交 / 推送。

## 回报

说明实际执行的路径、扫描或验证结果，以及生成 / 修改 / 提交 / 推送到的目标。运行态仍保留时给出 worktree、mode 和停止入口；不要把静态扫描通过表述为运行时验收通过。
