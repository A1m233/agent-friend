---
name: fix-issue
description: 把 docs/issues/<NNN>-<slug>/ 下已登记的 issue 闭环——读 issue → 核对现状 → 选定修复方向 → 按分支策略落地代码 → 状态改 resolved → 调 gen-commit-message 出 commit。当用户说"修 issue X"、"解决 issue X"、"close issue X"、"fix issue"时触发。
---

# 修 issue（docs/issues/）

承接 `docs/issues/<NNN>-<slug>/README.md` 已经写好的"现象 / 根因 / 修复方向"，把分析落成代码 + 闭环 issue 状态。**只处理已登记的 issue**——临场发现的小问题直接改、不走本 skill。

## 前置：先读管理规则

动手前先读 `docs/issues/README.md` 与 `.cursor/rules/dev-workflow.mdc`，确认状态机、关闭流程、分支策略没有变化（README/rules 是 single source of truth，本 skill 只是操作流程）。

边界确认：
- 本 skill **不负责**：合入 main（用户 review 后自决）、跑 verify（产品体验类问题 verify 跑不出）、写 commit message（交给 `gen-commit-message` skill）。
- 本 skill **负责**：分支策略、代码改动、issue 状态闭环、串接 `gen-commit-message`。

## 步骤

### 1. 读 issue

读 `docs/issues/<NNN>-<slug>/README.md`，理清四件事：现象、根因初判、可选修复方向、关联需求。同目录下的 `assets/` / 复现脚本 / 日志若有也要扫一遍。

### 2. 核对现状（防修空气）

issue 可能登记后被其他改动顺手修掉、或根因点名的文件 / 函数早已重命名 / 删除。动手前必须 grep / Read 根因里点名的位置，确认：

- 文件还存在
- 函数 / 符号还存在
- 现象按 issue 写法仍能复现（能跑就跑，跑不了就静态推演）

若现状已变（问题已消失 / 位置改名 / 根因不再成立），**停下来报告用户**，让用户决定是直接关 issue 还是重新分析根因。**不要自己脑补一个新根因继续修**。

### 3. 选定修复方向

- issue 列了 A / B / C 等多方向 → **必须报告给用户拍板**，哪怕 issue 里写了"建议先试方向 A"也要确认一次（避免 skill 自作主张选错方向后浪费一轮）。
- issue 没列方向 → 自己提议 1-3 条路线，附简要权衡，让用户拍板。
- 用户拍完板再进入下一步，不要边等回复边动手。

### 4. 分支策略

判定 **是否动 src**（`agent/src/`、`apps/`、`engine/` 等代码目录）：

| 情况 | 分支动作 |
| --- | --- |
| 只改 `docs/issues/<NNN>/README.md` 状态字段 / 纯文档勘误 | 沿用 `dev-workflow.mdc` 例外，直接 main |
| 动到 src 代码 | **必开 worktree**，分支名 `fix/<NNN>-<slug>`（对齐 issue 目录名） |

worktree 处理流程（动 src 场景）：

1. 检查当前所在分支：
   - 已在 `fix/<NNN>-<slug>` → 复用，直接进入第 5 步
   - 已在 `main` 且 worktree clean → 用 `EnterWorktree` 工具新建，指定 `name: fix/<NNN>-<slug>`
   - 已在其他分支 / 无关 worktree（包括其他 `fix/*`、`feature/*`、随机 `worktree-*`）→ **fail-fast**，要求用户先 `ExitWorktree` 退出再重启 skill。不要自作主张切分支。
2. worktree 路径会落到 `.claude/worktrees/`，分支名是语义化的 `fix/<NNN>-<slug>`，不走随机三词名。

### 5. 实施修复

- 遵守 `.cursor/rules/` 下相关规则（`coding-design.mdc`、`cross-platform-dev.mdc` 等按文件 frontmatter 判定）。
- 若改动涉及核心逻辑 / 可独立验证的行为，按 `dev-workflow.mdc` 要求补单测。
- 改完别立刻 commit，先到第 6 步更新 issue 状态。

### 6. 更新 issue 状态

按项目规范（`docs/issues/README.md`）闭环：

1. 把 issue README 的 **状态** 字段改成 `resolved`。
2. 在文末加一行修复指向，格式：

   ```markdown
   ---
   已在 commit `<占位>` 修复。
   ```

   commit hash 这一步还拿不到，先填占位（如 `TBD`），第 7 步 commit 完成后回填真实 hash 再补一次提交，或在同一轮 commit 前回填。

   **推荐做法**：commit 前先用占位提交一次草稿 → 拿到 commit hash → amend 进 README 末尾 → 重新 commit。或更简单：先 commit 代码改动，commit 后单独再 commit 一次 issue 状态更新（README 改动微小，单独一个 docs commit 也清楚）。

3. **不追加"修复记录"完整段落**——项目规范保持轻量，只要状态 + 一行 commit 指向。详细的修复思路 / 决策由 commit message 承担。

### 7. 出 commit

调用 `gen-commit-message` skill 生成 commit message，遵守其格式约定（commit type `fix(<NNN>): ...`，正文引用 issue 编号）。

commit 范围：
- 一次 commit 涵盖"代码改动 + issue 状态更新"是首选（一次回填 commit hash 麻烦）。
- 实在需要分两次：第一次 commit 代码改动（拿到 hash）；第二次 commit 改 issue README 状态（README 里写第一次的 hash）。

### 8. 收尾

- 报告用户：分支名、改动文件清单、issue 状态已变 `resolved`、commit hash。
- **不自动合入 main**——由用户 review 后自决（项目现状是 `git merge --no-ff` 风格，由用户操作）。
- **不自动跑 verify**——若 issue 属于产品体验类（如本 skill 设计时参考的 issue 006 BedtimeSource 跑偏），verify 跑不出；属于行为可断言类（如 cursor passthrough 之类），是否触发 `verify` skill 由用户决定。

## 反模式

- **不读 issue 直接动手**：根因可能早已变化，凭一句话标题修改大概率修错位置。
- **自作主张选修复方向**：issue 里"建议先试方向 A"是给人类看的提示，skill 仍应让用户拍板。
- **fail-fast 时自己切分支**：当前在其他分支就停下报告用户，不要 `git checkout` / `git stash`。
- **状态字段忘改**：代码修了但 issue 还挂 `open`，下一次 grep `open` issue 列表会污染视野。
- **commit 不引 issue 编号**：失去可追溯性，`docs/issues/<NNN>/README.md` 末尾的 commit 指向也对不上号。
- **顺手 fix 别的 issue**：一次 skill 调用对应一个 issue，跨 issue 修补让分支语义混乱、状态回填出错。
