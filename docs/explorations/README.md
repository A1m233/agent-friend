# 探索记录（Explorations）

本目录承载**长期话题的探索与讨论记录**——既不是已锁定的决策，也不是具体的可交付需求，而是需要多轮迭代、逐步沉淀的话题（例如：记忆系统的设计方向）。

## 组织约定

- 每个主题一个子目录，命名用 **kebab-case** 英文短语（不编号），例如 `memory/`、`personality/`。
- **主题内文件结构完全自由**：一份 `README.md` 长文也行、按子话题拆 `survey.md` / `notes.md` / `vision.md` 也行。怎么写顺手怎么来。

## 与 `decisions/` / `requirements/` 的边界

- 这里只是**过程笔记**。当某个点足够成型，**正常地**去 `docs/decisions/` 写决策 或到 `docs/requirements/<id>/` 写需求 / `design.md` 即可。
- **不需要**在 explorations 这边做台账登记、出栏标记、归档动作——保持轻量。
- 反过来：项目级方向、具体交付物的 single source of truth **不放在 explorations**，避免和 decisions/requirements 抢权威。
