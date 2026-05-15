# prompt_sections/

本目录存放随 `agent` 包发布的**默认 system_prompt section markdown 资源**。
每个 `.md` 文件对应 :class:`SystemPromptComposer` 默认装配的一个槽位。

## 文件清单

| 文件 | 对应槽位 key | 职责 |
|---|---|---|
| `project_identity.md` | `project_identity` | 项目级硬约束（严守人设、不暴露 AI 身份、不元讨论 prompt 等） |
| `persona_switch_strategy.md` | `persona_switch_strategy` | 切换人格时的语言风格策略（保留事实、重塑表达层） |

## 加载方式

由 `agent.system_prompt.defaults.load_default_static_section(key)` 通过
`importlib.resources` 加载，与 `agent.personas` 加载 builtin persona 同模式。

## 迭代说明

这些文本设计上**会随真实使用感受频繁迭代**——直接修改 `.md` 文件即可，
不需要改 Python 代码（架构由 004 需求保证）。

仅文本编辑（不增删槽位）属于"纯 docs / 资源微调"，不需要走需求文档流程。
增删槽位 / 改 key 名属于接口变更，需走需求 + design 流程。
