---
name: agent-handoff
description: 把 feature-delivery 流程中当前 session 的工作交接给下一个 agent。产出 transfer prompt（给对方粘贴）+ 当前 session 收尾 list（给自己）。**显式触发**：用户说"交接给别的 agent" / "写交接 prompt" / "做 handoff" / "出交接" / "agent handoff" 任一时调用。仅服务 feature-delivery 流程中途交接（Phase 1 / 2 / 3 任一位置往后切），通用 session 交接请自己手写 prompt。
---

# Agent Handoff（feature-delivery 流程中途交接）

把当前 session 在 feature-delivery 中累积的"文档外活信息"提炼成给下一个 agent 的 transfer prompt + 给本 session 自己的收尾 list。**仅服务 feature-delivery 流程中途交接**——通用对话交接请自己手写。

---

## 前置 assertion（硬条件 · 任一未过即拒绝产出）

skill 触发后立刻检测，**全部通过**才进入步骤；**任一未过**直接报错并告诉用户哪条缺、怎么补，不出 transfer prompt。

1. **在 feature-delivery 流程中**：当前 cwd 或其祖先目录下存在 `docs/requirements/<NNN>-<slug>/requirement.md`。否则报"当前不是 feature-delivery 场景，本 skill 不适用"。
2. **当前 phase Confirmed 产物已落盘**：
   - Phase 1+ 交接：`requirement.md` 状态 = `已确认（Confirmed）`，不是 `草稿（Draft）`
   - Phase 2+ 交接：`design.md` 同上
   - 用 grep 检测文件内的状态行（项目约定见 `docs/requirements/README.md`）
3. **工作区无未 commit 的修改**：`git status --short` 输出为空（`.claude/worktrees/` 等 gitignore 之外的临时目录除外）。否则列出脏文件 + 提示"先 commit 或 stash"。
4. **当前分支已 push 到远程**：`git status -uno` 含 `Your branch is up to date with 'origin/...'`。否则提示"先 push 当前分支"。

## 软条件（提示但不阻塞）

硬条件全过后继续检测：

- **Phase 2 → Phase 3 交接**：`design.md` 已 Confirmed 但 `progress.md` 不存在 → 提示"建议先落 progress.md 再切——否则新 agent 接 Phase 3 没任务入口，得重新拆解，价值损失大"
- **Phase 3 中途交接**：`progress.md` 存在，但实现日志最近一条登记时间 > 1 小时（或从未登记）→ 提示"建议先把进度同步到 progress.md 实现日志、让新 agent 知道接力点"。**Phase 3 起步交接排除此条**——判定标志：progress.md 总体状态 = `NOT_STARTED` 且任务列表全未勾选；起步本就没东西可登记，提示无意义

软条件未过时输出：

```
软条件未满足：
- <列出每条具体缺失>

是否仍要 handoff?（y / 补完再切）
```

用户选 y → 继续出 transfer prompt；选"补完再切" → skill 退出。

---

## 模式选择（A 默认 / B opt-in）

git 物理约束：**同一分支不能同时 active 在两个 worktree**——这决定了 handoff 只能两种模式。

| 模式 | 语义 | 触发 |
| --- | --- | --- |
| **A · 单向接力**（默认） | 原 agent push + 退出；新 agent 任意 cwd 接手（claude 可 spawn 新 worktree、cursor 可打开主 repo 或那个 worktree 目录）；原 agent 不"回来" | 默认 |
| **B · 借用 worktree + 回来收尾**（opt-in） | 原 agent push + 暂停 session + **保留 worktree 不删**；新 agent **必须进原 agent 的 worktree path 干活**（同分支冲突）；新 agent push + 退出后，原 agent 回 worktree pull + 收尾 | 用户触发时显式说"模式 B" / "借用 worktree" / "原 agent 回来收尾"任一 |

skill 检测用户表述里有没有模式 B 关键词；没有 → 走 A。

---

## 步骤

### 1. 收集 git 状态

- 分支：`git rev-parse --abbrev-ref HEAD`
- worktree：`git worktree list` 判定当前 cwd 是 main worktree 还是 `.claude/worktrees/xxx`（按 `git rev-parse --show-toplevel` 跟 main worktree path 比对）
- 远程同步状态（已由硬条件 #4 保证）

### 2. 收集 feature-delivery 上下文

- 需求编号 + slug：从 `docs/requirements/<NNN>-<slug>/` 推
- 当前 phase（看文档状态推）：
  - 只有 `requirement.md` Confirmed → Phase 1 完成，下一步进 Phase 2
  - `requirement.md` + `design.md` Confirmed → Phase 2 完成，下一步进 Phase 3
  - `progress.md` 实现日志已登记 ≥ 1 条 → Phase 3 中途
  - 三者都 Confirmed 且 progress 全勾 + AC 全验 + 用户 sign-off → 已完成（这种 case 通常不需要 handoff）

### 3. 收集"文档外活信息"

按当前 phase 翻 session 历史 + 自查，提取以下类目（**只列抽象类目，具体内容由调用方填**——不要把当下需求的具体编号 / R-x / AC-x / M-x 硬编进 skill 自身）：

| 类目 | Phase 适用 | 含义 |
| --- | --- | --- |
| 用户当前 session 表达过但未进文档的偏好 | 全部 | 如"不改既有文档"、"暂不引开关"、"先 X 不 Y"等命令式语义 |
| 范围拍板过程中被拒绝 / 推迟的备选 | Phase 1+ | 让新 agent 知道为什么不做某事，避免回头重新提议 |
| 设计选择中用户显式 prefer / reject 的方案 | Phase 2+ | 避免新 agent 重新做相同 trade-off |
| 已在前序 phase 提前完成的 milestone | Phase 3 | 如 audit 类、prototype 类、spike 类——避免新 agent 重跑 |
| 验收门禁中由 owner 独立 sign-off 的项 | Phase 3 | 主观体感、外部资源验证、不可自动化项 |
| 环境限制 | Phase 3 | 当前 OS / 网络 / 权限跑不了某些 AC，必须切机或借机 |
| 高风险操作的前置授权 | Phase 3 | 跑某类 LLM 调用、外部 API、删除类操作前需 owner 同意 |

**关键判断**：只传"对方加载 skill + 读项目 rules + 读文档读不到的活信息"。文档里写了 + rule 自动加载的 + skill 自带的，**一律不塞**（见反模式段落）。

### 4. 出 transfer prompt + 收尾 list

按下方模板填，**不自己跑 git commit / push / 写文件**——skill 只产文本，行动由用户 / agent 按收尾 list 按需做。

---

## transfer prompt 模板

```
仓库：<cwd 绝对路径>
分支：<branch>（已 push 占号 · 来自 <main worktree | .claude/worktrees/<name>>）
需求：<NNN>，加载 feature-delivery skill 从 Phase <N> 起步

requirement.md / design.md / progress.md 已落，完整范围 / 设计 / AC / 风险 / 任务清单
都在 docs/requirements/<NNN>-<slug>/ 里，自己读。

文档没写、必须知道的几条：

1. <活信息 1>
2. <活信息 2>
...
```

**模式 B 时追加段**：

```
本期是模式 B 交接（借用 worktree）。原 agent 在 <worktree 绝对路径>，
**请用户把你的 IDE workspace / cwd 切到这个 worktree 目录后再接手**
——同分支不能在两个 worktree active。干完事 push，原 agent 会回这个
worktree pull + 收尾。
```

---

## 收尾 list 模板

```
[ ] 当前分支已 push 远程（硬条件已过，确认）
[ ] requirement.md / design.md / progress.md 已 commit + push（硬条件已过）
[ ] 把上面的 transfer prompt 复制给用户，粘贴到新 session

模式 A（默认）：
[ ] worktree 保留 / 删除均可（对方独立 checkout 同分支或新建 worktree 即可）

模式 B（借用 worktree）：
[ ] 不要删 worktree
[ ] 通知对方进哪个 worktree 目录（transfer prompt 已写明）
[ ] 等对方干完事 + push + 退出后，自己回 worktree pull + 收尾
```

---

## 反模式（必读）

transfer prompt 里 **绝对不要塞** 以下内容——对方加载 skill / rule / 自己读文档就有，重复塞只会把 prompt 撑大、把信噪比拉低：

- ❌ skill 模板 / 流程 / 状态枚举（`feature-delivery` / `gen-commit-message` 等的内部步骤）
- ❌ 项目目录约定（`docs/requirements/` 命名规则、状态字段等）
- ❌ `CLAUDE.md` / `.cursor/rules/*.mdc` 任何 rule 内容
- ❌ 用户级 / 全局 rules / memory
- ❌ 既有同库相关需求 / decisions / explorations 的具体内容——**只引路径**（如"参 `docs/requirements/018/design.md §3.5`"），对方自己读
- ❌ 任何具体 milestone / AC / R- / M- 编号的展开内容——只引路径，对方进文档看
- ❌ 项目级技术栈选型（哪个语言 / 框架 / Live2D 库选哪个等）——decisions / 既有 design 都有

**只传**：session 对话过程中 owner 拍板 / 偏好 / 拒绝 / 提前完成 / 外部限制 / 授权要求等**文档外活信息**。判断准则：**对方加载所有 skill + 读所有可见文档之后仍然不知道的事**。

---

## 示例（showing not telling）

以"某次需求 NNN 在 Phase 3 起步时交接"为简化示例（**示例编号 / 内容是占位，调用时按当时实际填**）：

```
仓库：/path/to/agent-friend
分支：feature/NNN-xxx-yyy（已 push 占号 · 来自 main worktree）
需求：NNN，加载 feature-delivery skill 从 Phase 3 起步

requirement.md / design.md / progress.md 已落 Confirmed，完整内容在
docs/requirements/NNN-xxx-yyy/ 里，自己读。

文档没写、必须知道的几条：

1. <X audit 已在 Phase 2 阶段直接完成，结论 + 降级路径在 design.md §3.1>
   —— 别再单独跑 audit milestone。

2. 用户偏好"不改既有文档" —— 既有相关需求 / design 不动；本期 requirement
   也不要回头改，实现期细节调整全走 progress.md。

3. AC-<X> 主观 / owner 独立 sign-off —— 你不能自己 close，progress.md 标
   COMPLETED 前必须 owner 明确说 OK。

4. 环境限制：<某些 AC 当前 OS 跑不了> —— progress.md 把这部分留作"待 owner
   切机验"分项，不挂在自己门禁卡死。

5. 跑会触发 <受控外部资源> 的 AC 前先获 owner 明确同意（相关 rule 你启动
   会自动加载，这里提示一下不要忘）。
```

注意：**示例段只展示"如何填",具体的 X / NNN / AC-x 都是模板槽位**，调用方按当时实际需求填。

---

## 注意

- skill 仅产文本（transfer prompt + 收尾 list）；**不**自己跑 git / 不自己写文件 / 不动 `progress.md`
- 模式 B 是 opt-in；用户没显式说，一律走默认 A
- worktree 检测用 `git worktree list` + 路径比对，**不**依赖目录命名约定
- 默认 A 的原因：cursor 接手模式 B 时需要用户**手动把 IDE workspace 打开到 worktree 目录**——这一步 skill 自动化不了，写错很容易让对方在错的 cwd 上干活；默认 A 更稳
