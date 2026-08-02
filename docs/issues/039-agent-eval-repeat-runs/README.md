# 039 · agent_eval 缺少按 contract 或 case 重复运行与置信度聚合

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-07-31
- **关联需求**：无

## 现象

`agent_eval` 当前会把选中的每个 case 执行一次。CLI 支持用 `--contract` 选择一组
case、用 `--case` 选择单个 case，也支持用 `--limit-cases` 缩小范围，但不支持把
选中的范围自动重复运行 N 次。

这会让概率性 LLM 行为的单次结果缺少置信度。例如 C13 图片证据校准首次真实运行只
产生每个 case 各一条回答；即使当次人工复核全部符合预期，也无法判断相同行为在重复
采样下是否稳定。此前 C9 的多次观察只能靠外部重复执行命令，形成多份分散报告，再由
人工合并结论。

## 根因初判

当前执行与报告模型以“一个选中 case 对应一次 `run_case`”为固定结构：

- `Selection` 只有 contract、case 和 limit，没有重复次数；
- CLI 对每个 case 只调用一次 `run_case`；
- report 只保存一条 case trace 和 verdict，没有 repetition index、逐 case 成功次数或
  重复采样聚合结果。

因此这不是某个模型或某条 case 的问题，而是 eval harness 尚未把随机性和重复采样建模
为一等能力。

## 影响与判断

- 单次 pass / fail 容易被随机波动放大，不能代表行为稳定性；
- contract 的 pass rate 当前主要表达不同 case 的覆盖率，不能同时表达同一 case 的重复
  成功率；
- 手工重复命令会生成分散报告，增加漏跑、命名不一致和人工汇总错误；
- 多模态、工具路由、长上下文等概率性较高的契约尤其缺少可信的回归证据。

现有 workaround 是外部重复运行同一命令并人工复核报告，因此不阻塞当前 C13 契约继续
使用；但在用 eval 结果判断 prompt 或 agent 行为是否稳定前，应补齐这一能力。本 issue
只登记能力缺口，暂不在当前图片证据修复中扩张 CLI、schema 和报告格式。

## 可选修复方向

1. 为 CLI 增加 `--repeat N`，与现有选择器组合：`--contract C13 --repeat 5` 重复整组，
   `--case C13-03 --repeat 10` 重复单个 case；默认值保持 1。
2. 每次 repetition 使用独立临时 session 和完整 `run_case` 生命周期，不复用上一轮模型
   上下文、工具结果或附件 provider cache，避免样本互相污染。
3. report 同时保留每次原始 trace / verdict，并按 case 聚合 `passed / total / pass_rate`；
   contract 汇总区分“case 覆盖率”和“重复运行成功率”，避免混成一个数字。
4. 在执行前显示总 case-run 数及预计主模型调用量，使真实 LLM 授权和成本范围可判断；
   非法或过大的 N 应显式拒绝或要求拆分运行。
5. 为 selection、CLI、runner、report schema 和失败详情补确定性测试，覆盖整组重复、单 case
   重复、部分失败、runtime error 和 `N=1` 向后兼容。
6. 若未来确有“同一 contract 内只有部分 case 使用不同重复次数”的需求，再评估 case 级
   `repeat` 配置或显式 case 列表；首版优先复用 `--contract` / `--case`，避免过早引入复杂
   调度语义。
