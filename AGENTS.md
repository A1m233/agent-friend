# agent-friend 项目说明

> **规则与 Skill 的单一数据源在 `.cursor/`。** 修改一律改 `.cursor/` 下的文件，不要在 `.Codex/` 里复刻或新建副本。`.Codex/skills/<name>` 是指向 `.cursor/skills/<name>` 的软链；本文件（AGENTS.md）是规则的"指路牌"，不复制规则全文。

## 规则（位于 `.cursor/rules/*.mdc`）

承接非平凡任务前，**先扫一遍 `.cursor/rules/` 下相关 `.mdc`**，按 frontmatter 判断适用范围：

- `alwaysApply: true` → 项目通用，必须遵守
- 含 `globs:` → 仅在改动匹配 glob 的路径时适用
- 其余 → 按 `description` 判断本次任务是否相关

当前规则索引（**内容以文件为准**，下方仅作 lookup）：

| 文件 | 适用场景 |
|---|---|
| `coding-design.mdc` | agent-friend 项目编码设计原则：边界、可扩展性与不过度设计 |
| `cross-platform-dev.mdc` | agent-friend 项目跨平台开发支持与 scripts/ 规范 |
| `dev-workflow.mdc` | agent-friend 项目需求与 issue 交付流程：需求走 feature 分支，issue 修复按影响范围选择 main 或 fix 分支 |
| `docs-structure.mdc` | agent-friend 项目 docs/ 目录结构（项目特有） |
| `frontend-ui-conventions.mdc` | agent-friend 前端 UI 编写规范：组件复用与设计 token CSS 变量化 |
| `frontend-visual-verification.mdc` | agent-friend 桌面前端视觉验收规范：改 Tauri 窗口 UI 时优先观察真实桌面端，而不是只看浏览器页面。 |
| `repo-directory-layout.mdc` | 仓库与模块的目录编排：顶层职责、模块用文件夹、单文件职责 |

> Cursor 特有的 `disable-model-invocation` 字段 Codex 不识别，相当于"用户主动触发"的语义请凭描述判断。

## Skills

`.Codex/skills/<name>` 是软链 → `.cursor/skills/<name>`。**修改 skill 直接改 `.cursor/skills/<name>/SKILL.md`**，不要在 `.Codex/skills/` 下新建或拷贝文件。新增 skill 时也是在 `.cursor/skills/` 建好后，再运行项目级 Codex adapter 同步。

当前项目 skill 索引（**触发条件以各 SKILL.md frontmatter description 为准**）：

| Skill | 触发场景 |
|---|---|
| `add-issue` | 在 agent-friend 仓库的 docs/issues/ 下登记一个"已知但暂不修复"的问题（bug、体感瑕疵、技术债、设计弱点）。当用户说"记一个 issue"、"登记问题"、"先记… |
| `add-shadcn-component` | 在 agent-friend 前端（frontend/src/components/ui/）通过 shadcn CLI 引入通用 UI 组件的标准流程。当需要新增通用纯 UI 件、引入/添加… |
| `agent-handoff` | 把 feature-delivery 流程中当前 session 的工作交接给下一个 agent。产出 transfer prompt（给对方粘贴）+ 当前 session 收尾 list（… |
| `desktop-visual-inspection` | 在 agent-friend 真实桌面端观察和验证前端视觉/DOM 的标准流程。Use when 修改或排查 Tauri 桌面窗口 UI（对话页、设置页、桌宠窗、语音窗、窗口布局、滚动条、输… |
| `fix-issue` | 把 docs/issues/<NNN>-<slug>/ 下已登记的 issue 闭环——读 issue → 核对现状 → 选定修复方向 → 按 dev-workflow 分支策略落地 → 验… |
| `tdesign-chat-style` | 在 agent-friend 前端调整 TDesign Chat 对话组件具体视觉样式的标准流程。Use when 用户想修改对话页里的 TDesign Chat / ChatMessage… |
