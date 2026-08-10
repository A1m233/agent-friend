---
name: maintain-agent-friend-guide
description: 只读检查 agent-friend 产品变更是否需要更新公开用户知识，并在用户明确授权后维护、验证、发布公开 agent-friend-guide，再同步产品内置快照。显式触发：用户说“检查/维护/更新 agent-friend 知识库或使用指南”“发布 guide”“同步 guide builtin”，或 package-release / dev-workflow 明确询问后用户同意执行知识维护检查。不要因普通实现、打包、commit、push、合并或泛化的“看看是否完成”自行触发。
---

# 维护 agent-friend 用户知识

把公开 `agent-friend-guide` 与产品用户可见事实保持一致。默认只做检查；检查本身不修改文件、不 commit、不 push。

## 工作流

1. 读取 `references/reference-map.md`，确定产品变化可能影响哪些用户知识。
2. 运行 `scripts/agent-friend-guide/run.* status --guide-repo <path> --json` 获取基线、两仓状态和部分完成阶段。
3. 按 `references/review-workflow.md` 阅读从公开 metadata 基线到目标产品状态的真实 diff，并沿用户可见入口核实语义。
4. 输出“需要更新 / 无需更新 / 无法判断”以及证据、受影响 reference 和未决项。到此保持只读。
5. 只有用户明确同意更新后，按 `references/content-quality.md` 修改公开知识；不得直接编辑产品内置快照。
6. 运行公开 Skill validator 与项目 `verify`。验证通过后，按 `references/cross-repo-publish.md` 分阶段确认 commit/push 授权。
7. 公开提交已推送且能从 origin 精确确认后，才运行 `sync --guide-commit <40-hex>` 生成 builtin 快照，并验证产品仓库。

## 只读检查边界

- clean 产品仓库以 `HEAD` 为目标。
- dirty 产品仓库可以检查 `HEAD + tracked diff + untracked 文件`，但只能作为预览；不得把工作区状态写入公开来源锚点。
- baseline 不存在、不可达或不是目标的祖先时停止猜测，报告需要用户选择基线。
- 纯重构、测试、格式和等价替换可以判定无需更新，但要写出简洁证据。
- 用户可见能力、限制、操作、设置、凭据、数据、隐私或错误表现发生变化时，列出准确受影响 reference。

## 授权边界

- “检查”不授权改文件。
- “更新知识”授权修改已列出的公开知识与生成文件，不自动授权任何仓库 commit 或 push。
- 两个仓库的 commit 和 push 分别核对范围；存在来源不明的 dirty 文件时暂停并列出精确路径。
- 不 force-push、不自动 amend 已推送的公开提交、不删除已经成功发布的远端提交。
- `sync` 只生成 builtin 和 manifest，不执行 Git 操作。

## 提醒去重

当本 Skill 由打包或合入主干提醒进入时，在当前任务中记住：

`product_state_summary + delivery_action`

相同产品状态与相同交付动作已经询问、检查或维护后，不再提醒。产品 HEAD、tracked diff 或 untracked 文件集合/内容实质变化后可以重新提醒。该状态不跨任务持久化。
