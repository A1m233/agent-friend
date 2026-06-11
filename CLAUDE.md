# agent-friend 项目说明

> **规则与 Skill 的单一数据源在 `.cursor/`。** 修改一律改 `.cursor/` 下的文件，不要在 `.claude/` 里复刻或新建副本。`.claude/skills/<name>` 是指向 `.cursor/skills/<name>` 的软链；本文件（CLAUDE.md）是规则的"指路牌"，不复制规则全文。

## 规则（位于 `.cursor/rules/*.mdc`）

承接非平凡任务前，**先扫一遍 `.cursor/rules/` 下相关 `.mdc`**，按 frontmatter 判断适用范围：

- `alwaysApply: true` → 项目通用，必须遵守
- 含 `globs:` → 仅在改动匹配 glob 的路径时适用
- 其余 → 按 `description` 判断本次任务是否相关

当前规则索引（**内容以文件为准**，下方仅作 lookup）：

| 文件 | 适用场景 |
|---|---|
| `coding-design.mdc` | 编码设计原则（模块边界、扩展点、不过度设计） |
| `cross-platform-dev.mdc` | 跨平台开发（Windows / macOS / Linux）约定 |
| `dev-workflow.mdc` | 开发流程 |
| `docs-structure.mdc` | 文档结构与组织 |
| `frontend-ui-conventions.mdc` | 前端 UI 约定 |
| `repo-directory-layout.mdc` | 仓库目录布局 |

> Cursor 特有的 `disable-model-invocation` 字段 Claude Code 不识别，相当于"用户主动触发"的语义请凭描述判断。

## Skills

`.claude/skills/<name>` 是软链 → `.cursor/skills/<name>`。**修改 skill 直接改 `.cursor/skills/<name>/SKILL.md`**，不要在 `.claude/skills/` 下新建或拷贝文件。新增 skill 时也是在 `.cursor/skills/` 建好后，再补一条软链过来。
