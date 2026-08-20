---
name: fix-issue
description: 把用户明确点名的 docs/issues/NNN-slug/ 已登记 issue 闭环——读 issue、核对现状、落地修复、验证、标记 resolved 并提交。仅当用户给出 issue 编号/路径，或上下文无歧义地要求修/关某个已登记 issue 时使用；不得因症状相似、历史 issue 可参考或本次修复覆盖其假设而自动触发。
---

# 修 issue（docs/issues/）

承接 `docs/issues/<NNN>-<slug>/README.md` 已经写好的"现象 / 根因 / 修复方向"，把分析落成代码 + 闭环 issue 状态。**只处理已登记的 issue**——临场发现的小问题不走本 skill，但仍按 `dev-workflow.mdc` 的临场修复分支策略判断是否需要 fix 分支。

## Activation Gate

进入本 skill 前必须同时满足：

1. 目标是 `docs/issues/` 下已经存在的具体 issue；
2. 用户明确给出编号 / 路径，或当前上下文无歧义地指向并要求修复 / 关闭该 issue。

只搜索到相似 issue、从主干历史看到相同症状、旧 issue 列过相关假设，或本次修复可能顺带覆盖旧 issue，都不构成触发。此时把旧 issue 当调查上下文，本次仍按临场修复推进，不修改旧 issue 状态；若证据确认二者确属同一问题，也要先让用户明确同意绑定。

## 前置：先读管理规则

动手前先读 `docs/issues/README.md` 与 `.cursor/rules/dev-workflow.mdc`，确认状态机、关闭标准、分支策略没有变化（README/rules 是 single source of truth，本 skill 只是操作流程）。

边界确认：
- 本 skill **不负责**：自动合入 main / push（用户 review 后自决）、替用户确认产品体验类验收、直接生成 commit message（交给 `gen-commit-message` skill）。
- 本 skill **负责**：按 `dev-workflow.mdc` 判断分支策略、代码改动、必要测试 / smoke / 人工验证提示、issue 状态闭环、串接 `gen-commit-message`。

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

以 `.cursor/rules/dev-workflow.mdc` 的 **Issue 修复流程** 为准，不在本 skill 里维护第二份分支规则。

操作前先判断改动类型并向用户说明：

- 只补充 issue 文档 / issue-local 复现资料 → 可直接 main。
- 只新增小型回归测试、不改生产代码 → 可作为 test/hotfix 直接 main；覆盖核心链路或影响较大时推荐 `fix/<NNN>-<slug>`。
- 修复生产代码 → 必须开 `fix/<NNN>-<slug>`。

如果当前分支不符合 `dev-workflow.mdc` 的要求，停下来报告用户，不要自作主张 `checkout` / `stash` / `reset`。

### 5. 实施修复

- 遵守 `.cursor/rules/` 下相关规则（`coding-design.mdc`、`cross-platform-dev.mdc` 等按文件 frontmatter 判定）。
- 若改动涉及核心逻辑 / 可独立验证的行为，按 `dev-workflow.mdc` 要求补单测。
- 真 LLM / 人工体验类行为用 smoke / replay / 手动验收记录补证据；触发真实 LLM 前遵守 `llm-api-confirm`。
- 改完别立刻 commit，先到第 6 步判断是否满足关闭标准。

### 6. 验证并更新 issue 状态

先按 `dev-workflow.mdc` 的 **关闭标准（issue）** 判断能不能关：

- 自动化可断言的行为：对应单测 / 集成测试通过。
- 无法稳定断言的体验 / LLM 行为：对应 smoke / replay / 人工验证已跑，并把结果记录到 issue README。
- 改动代码或正式测试：`./scripts/check` 全绿。
- 用户可见行为或产品体验类 issue：用户明确确认验收完成。

满足后再按项目规范（`docs/issues/README.md`）闭环：

1. 把 issue README 的 **状态** 字段改成 `resolved`。
2. 在文末加一行修复指向，格式：

   ```markdown
   ---
   已在 commit `<占位>` 修复。
   ```

   commit hash 这一步还拿不到时，先填占位（如 `TBD`），commit 完成后按实际情况回填。

3. **不追加"修复记录"完整段落**——项目规范保持轻量，只要状态 + 一行 commit 指向。详细的修复思路 / 决策由 commit message 承担。

如果只是调查出“当前代码没问题”或“另有新根因”，不要擅自把原 issue 关掉；报告用户，让用户决定是关闭、改写 issue，还是登记新 issue。

### 7. 出 commit

调用 `gen-commit-message` skill 生成 commit message，遵守其格式约定（commit type `fix(<NNN>): ...`，正文引用 issue 编号）。

commit 范围：
- 一次 commit 涵盖"代码改动 + issue 状态更新"是首选（一次回填 commit hash 麻烦）。
- 实在需要分两次：第一次 commit 代码改动（拿到 hash）；第二次 commit 改 issue README 状态（README 里写第一次的 hash）。

### 8. 收尾

- 报告用户：分支名、改动文件清单、issue 状态已变 `resolved`、commit hash。
- **不自动合入 main**——由用户 review 后自决（项目现状是 `git merge --no-ff` 风格，由用户操作）。
- 报告已跑过的测试 / smoke / 人工验证，以及未跑项和原因。

## 反模式

- **不读 issue 直接动手**：根因可能早已变化，凭一句话标题修改大概率修错位置。
- **自作主张选修复方向**：issue 里"建议先试方向 A"是给人类看的提示，skill 仍应让用户拍板。
- **fail-fast 时自己切分支**：当前分支不满足规则就停下报告用户，不要 `git checkout` / `git stash`。
- **没有达到关闭标准就改状态**：代码看似修了，但测试 / smoke / 人工验证缺失，不能标 `resolved`。
- **状态字段忘改**：确认修复后 issue 还挂 `open`，下一次 grep `open` issue 列表会污染视野。
- **commit 不引 issue 编号**：失去可追溯性，`docs/issues/<NNN>/README.md` 末尾的 commit 指向也对不上号。
- **顺手 fix 别的 issue**：一次 skill 调用对应一个 issue，跨 issue 修补让分支语义混乱、状态回填出错。
