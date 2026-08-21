---
name: maintain-agent-friend-guide
description: 检查 agent-friend 产品变更是否需要更新公开用户知识，维护并发布 agent-friend-guide immutable release，或为新桌面安装包刷新离线 builtin 基线。显式触发：用户直接要求检查、维护、发布或同步 guide，package-release 提醒后用户同意，或 dev-workflow 以 merge-main-and-push 自动模式明确调用。不要因普通实现、打包、commit、push、合并或泛化的“看看是否完成”自行触发。
---

# 维护 agent-friend 用户知识

把公开 `agent-friend-guide` 与产品用户可见事实保持一致。先确定调用模式：

- `manual`：用户直接请求或 package-release 提醒后进入。默认只做检查，后续写入与 Git 操作按用户明确授权推进。
- `merge-main-and-push`：仅由 `dev-workflow` 在用户明确要求合入 `main` 并推送时调用。按本 Skill 的自动事务完成必要更新、正常 commit/push 与 immutable release，不逐步询问。
- `package-baseline`：仅在 package-release 提醒后用户明确同意，或用户直接要求刷新安装包离线 Guide 时进入。只把已发布的最新兼容 immutable release 同步为产品 builtin；不编辑公开知识、不创建 release、不自动提交产品仓库。

## 工作流

1. 读取 `references/reference-map.md`，确定产品变化可能影响哪些用户知识。
2. 运行 `scripts/agent-friend-guide/run.* prepare --json` 准备默认受管 checkout，再运行 `status --json` 获取基线、两仓状态和部分完成阶段。用户已明确提供 checkout 时可传 `--guide-repo <path>`。`package-baseline` 直接转到“离线基线刷新”。
3. 按 `references/review-workflow.md` 阅读从公开 metadata 基线到目标产品状态的真实 diff，并沿用户可见入口核实语义。
4. 得出“需要更新 / 无需更新 / 无法判断”以及证据、受影响 reference 和未决项。`manual` 模式输出后保持只读；`merge-main-and-push` 模式无需更新则返回交付流程，需要更新则继续，无法判断则停止。
5. `manual` 模式只有用户明确同意更新后才写；`merge-main-and-push` 模式直接按 `references/content-quality.md` 修改已确认的公开知识。不得直接编辑产品内置快照。
6. 运行公开 Skill validator 与项目 `verify`。验证通过后，按 `references/cross-repo-publish.md` 推进公开提交与 release 事务。
7. 公开提交已推送且能从 origin 精确确认后，为下一单调 release sequence 生成完整 ZIP 与兼容 manifest；先 draft、上传两项 asset，再 publish，并确认 GitHub API 报告 `immutable=true` 与 asset digest 一致。正常 Guide 发布到此结束，不修改产品 builtin。

## 离线基线刷新

1. 运行 `prepare`，确认受管 checkout clean、origin 正确并 fast-forward 到 `origin/main`。
2. 用 GitHub Releases API 确认 checkout `HEAD` 对应已发布、非 draft、非 prerelease、`immutable=true` 的最高兼容 release；不得从尚未发布的 main 内容生成 builtin。
3. 运行 `verify`，再运行 `sync --guide-commit <HEAD>`。复核 builtin manifest source commit 与 snapshot digest。
4. 运行 Guide 脚本测试和项目规定检查。同步只改变离线 baseline；客户端自动更新仍以 immutable release 为线上来源。
5. 不自动 commit/push。若构建渠道要求 clean worktree，向 package-release 返回精确变更，先按用户当前 Git 授权完成提交或停止。

## 只读检查边界

- clean 产品仓库以 `HEAD` 为目标。
- dirty 产品仓库可以检查 `HEAD + tracked diff + untracked 文件`，但只能作为预览；不得把工作区状态写入公开来源锚点。
- baseline 不存在、不可达或不是目标的祖先时停止猜测，报告需要用户选择基线。
- 纯重构、测试、格式和等价替换可以判定无需更新，但要写出简洁证据。
- 用户可见能力、限制、操作、设置、凭据、数据、隐私或错误表现发生变化时，列出准确受影响 reference。

## 授权边界

- “检查”不授权改文件。
- “更新知识”授权修改已列出的公开知识与生成文件，不自动授权任何仓库 commit 或 push。
- 两个仓库的 commit 和 push 分别核对范围；创建不可变 release 也是独立外部写入，`manual` 模式必须有明确发布授权。存在来源不明的 dirty 文件时暂停并列出精确路径。
- 不 force-push、不自动 amend 已推送的公开提交、不删除已经成功发布的远端提交。
- `sync` 只生成 builtin 和 manifest，不执行 Git 操作。

### `merge-main-and-push` 自动授权

- 仅覆盖当前一次组合交付所必需的 guide 检查、已列明公开知识修改、validator/verify、正常 commit/push、创建下一条 immutable release 与产品后续正常 commit/push。
- 自动使用固定受管 checkout；仅允许 clone 缺失仓库或把 clean、正确 origin、未分叉的 checkout fast-forward 到 `origin/main`。
- 不覆盖 force-push、改写或删除已发布提交/release、复用或降低 release sequence、回滚远端、解决来源不明改动、发布可能含 secret/私有实现的内容，或在证据不足时选择基线、兼容范围或文案。
- 命中上述边界、任一验证失败、push 非快进、产品状态在复核后改变，或部分完成状态无法唯一续跑时停止并报告。

## 提醒去重

当本 Skill 由打包提醒或合入主干自动流程进入时，在当前任务中记住：

`product_state_summary + delivery_action`

相同产品状态与相同交付动作已经检查或维护后不重复执行。产品 HEAD、tracked diff 或 untracked 文件集合/内容实质变化后必须重新复核。该状态不跨任务持久化。
