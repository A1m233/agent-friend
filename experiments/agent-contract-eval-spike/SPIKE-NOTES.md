# SPIKE-NOTES · agent 行为契约评测方法验证

> 本文件是本 spike 的**最终交付物**。spike 跑完后填写。

## 元信息

- **创建时间**：2026-06-24
- **结束时间**：_待填_
- **状态**：🚧 脚手架阶段（README 已定型 / 验证清单已锁 / 代码未实现）
- **验证对象**：「handcraft 契约 + 强模型扩 variation + deterministic assertion」组合方法
- **目标结论**：判断这套方法值不值得升级为正式包 `tool_use_eval`

## 验证清单实测（待填）

### 1. 信号清晰度

> 至少一条契约跑出来的"未通过"数量在 ~20–80% 区间。

_待填：各契约通过率表 + 是否落在合理区间_

### 2. 判分稳定性

> 同一 case 跑 N=3 次，通过/不通过结果一致。

_待填：抖动率 + 主要抖动来源_

### 3. 代码边际成本

> 第 2、3 条契约能否在不重写 framework 的前提下复用。

_待填：实际复用情况 + 框架未抽象到位的点_

### 4. 失败 case 的可指导性

> 抽 5 个未通过 case，能否给出 agent 该怎么改的具体建议。

_待填：5 个 case 的可指导性评估 + 真实有用的 prompt / 工具描述改进建议示例_

## 最终结论

_待填_

- ⬜ 四项全过 → 升级正式包 `tool_use_eval`，走 feature 流程
- ⬜ 部分通过 → 局部修正后重跑
- ⬜ 全部失败 → 放弃方法，回到 [`docs/explorations/agent-evaluation/`](../../docs/explorations/agent-evaluation/README.md) 重审

## Lesson Learned（无论通过与否都填）

_待填：本 spike 过程中暴露的方法学 / 工程 / 评测设计上的洞察，回流到 explorations 文档_
