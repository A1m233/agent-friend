---
name: clean-agent-friend-worktrees
description: 显式盘点并安全删除 agent-friend 的 Git/Codex worktree。仅当用户明确调用 `$clean-agent-friend-worktrees` 时使用；检查 dirty/untracked、HEAD 是否已被 main 包含或 patch-equivalent、detached 提交是否仍有引用、分支独有提交、运行进程与 dev runtime lease，并在逐项确认后通过 `git worktree remove` 删除 worktree。不得用于自动后台清理、删除分支、清理打包工作区或未获授权的强制删除。
---

# 清理 agent-friend Worktree

只把确定性脚本的结果当作盘点证据；由 Agent 解释风险，由用户决定删除目标。删除 worktree 不等于删除分支，本 Skill 永不删除本地或远端分支。

## 1. 只读盘点

在当前 agent-friend worktree 根执行：

```powershell
.\scripts\worktree-cleanup\run.ps1 inventory --json
```

macOS/Linux 使用同语义的 `./scripts/worktree-cleanup/run.sh`。盘点不得自动 fetch、prune、remove 或删除目录。

先用无 `--sizes` 的快速盘点确定分类。需要给用户展示可回收体量时，再运行一次 `inventory --json --sizes`；尺寸统计会遍历 Codex worktree 内的大型 Cargo target，执行前告知可能需要数分钟。不要让尺寸统计阻塞先行的安全判断。

先向用户报告本次比较使用的 `mainHead`。如果本地主干可能过期，只能说明证据边界；用户明确要求刷新后才执行 fetch，再重新盘点。

## 2. 解读分类

- `removable-clean`：工作区干净且没有活动保护。即使 `integration=unique-commits`，有名分支仍会在移除 worktree 后保留；明确提醒用户分支尚未进主干，但不把它误报为会丢提交。
- `review-redundant-dirty`：只有已跟踪 dirty 内容，且当前工作区内容与 `main` 一致。可建议丢弃，但必须说明会丢失 dirty 状态并取得逐项授权。
- `blocked-dirty`：dirty 内容与主干不同，或存在 untracked；不得删除。
- `blocked-unreferenced-detached-head`：detached HEAD 有独有且未被 ref 保存的提交；不得删除。先创建分支或 tag 需要另行取得用户授权。
- `protected-active`：当前/main worktree、Git locked、活动进程或 dev runtime lease 命中；不得删除。
- `removable-empty-codex-directory`：Git 已不登记且目录完全为空；可通过脚本的精确 `rmdir` 删除该项目目录及空的直接父目录。
- `blocked-unregistered-codex-directory`：目录未出现在 `git worktree list`，无法证明内容安全；本 Skill 不直接递归删除。
- `stale-git-metadata`：仅报告；本 Skill 不自动执行 `git worktree prune`。

不要只根据“分支已合并”判断；dirty/untracked、detached ref 和活动运行态仍是独立安全门。

## 3. 请求删除授权

列出每个候选的绝对路径、分支、HEAD、integration、dirty 状态和可回收体量。让用户明确选择要删除的 exact paths；不得把一次确认扩张到新出现的候选。

删除前说明：

- `removable-clean` 使用普通 `git worktree remove`；
- `review-redundant-dirty` 会使用 `--force` 丢弃 dirty 状态；
- 分支始终保留；
- 删除的 worktree 目录及其中 ignored build artifacts 不进入回收站。

## 4. 重新核验并删除

对用户选中的每个目标，使用盘点输出里的 `head` 与 `fingerprint`：

```powershell
.\scripts\worktree-cleanup\run.ps1 remove `
  --path <ABSOLUTE_PATH> `
  --expected-head <HEAD> `
  --expected-fingerprint <FINGERPRINT>
```

仅对用户明确授权的 `review-redundant-dirty` 追加 `--discard-redundant-dirty`。脚本会在删除前重新计算全部门禁；任何状态变化都必须停止并重新盘点，不得绕过。

Windows 上 `git worktree remove` 可能先注销 Git 元数据，再因大型目录树或被占用的根目录而失败。脚本只在本次目标已经通过全部门禁、Git 已确认不再登记该路径、路径仍严格位于 Codex 根布局且 `.git` 标记已消失时，继续删除该 exact residual directory；不得把这个恢复路径用于盘点时原本就是 `blocked-unregistered-codex-directory` 的目录。若只剩被外部进程持有的空根目录，记录警告并保留空壳，不终止或强杀未知进程。

一次只删除一个 worktree。某个目标失败时保留错误并继续前先判断是否会影响其它目标；不要退化为 `Remove-Item`、`rm -rf` 或手工删除 `.git/worktrees` 元数据。

## 5. 收尾

重新运行不带 `--sizes` 的 inventory，报告：

- 已删除 exact paths；
- 保留的分支；
- 失败或仍受保护的目标；
- 盘点前后估算的可回收/已回收体量。

不要顺带删除分支、package workspace、release、用户数据或其它仓库的 worktree。
