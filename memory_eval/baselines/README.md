# memory_eval / baselines/

每次 `./scripts/memory-eval/run.sh` 跑出来的 baseline 都落到这里，**随代码一起入 git**——目的是让"改 memory 之前的分数 / 之后的分数"在仓库历史里持续可对比。

## 命名

```
<ISO-datetime>-<short-sha>.json
```

例：`2026-06-11T07-36-01-c8735f0.json`

- ISO 时间用 `-` 替换 `:`（Windows 路径安全）
- short-sha 取自跑评测时的 `git rev-parse --short HEAD`；非 git 环境降级为 `nogit`
- 文件名带时间 + sha，**天然永远不冲突**——多人 / 多次跑互不覆盖

## JSON 字段语义

完整 schema 与字段含义见 [`docs/requirements/012-memory-eval-anchor-recall-scoring/design.md` §6.2](../../docs/requirements/012-memory-eval-anchor-recall-scoring/design.md#62-json-结构)。简略：

| 段 | 字段 | 含义 |
|---|---|---|
| `run` | `started_at` / `git_commit` / `dataset` / `model` / `limit_samples` / `limit_questions` | 跑这一次评测的运行条件，足以复现 |
| `macro` | `score_mean` | 已判分题的算术平均分（**核心数字**，对比时主要看它） |
| `macro` | `n_questions_total` / `n_questions_scored` / `n_questions_zero` | 总题数 / 已判分题数 / 0 分题数 |
| `per_question` | 每题：`sample_id` / `question` / `anchors` / `score` / `detail` / `recalled_count` | 单题诊断信息；不存召回全文（baseline 是"对比用的数字"不是"重放快照"，需要看具体召回时人重跑） |

`schema_version`：当前 `1`。后续若改 schema 在此递增。

## 不要做什么

- **不要为了"清爽"批量删旧文件**——删了就丢了对比基线。真要清理用 `git rm` + commit 留痕，不要悄悄删。
- **不要手动编辑 JSON**——它是机器产物，编辑会失去"记录运行真实状态"的语义。

## 当前归档

| 文件 | 当时 commit | macro | 简注 |
|---|---|---|---|
| `2026-06-11T07-36-01-c8735f0.json` | `c8735f0`（011 合 main） | 0.133 | **首份归档基线**——012 落地时的 memory 召回水平，作为后续改 memory 的对比起点 |
