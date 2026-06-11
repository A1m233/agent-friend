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

## Schema 版本

| version | 简介 |
|---|---|
| 1 | hotfix 之前的"减信息版"：每题只存 `recalled_count`、不存 gold answer / 完整召回；run 段也较薄。仅历史归档保留。 |
| 2（当前） | 完整版：单题含 gold answer + 稳定 question_id + 完整召回；run 段含 dirty / 数据集 hash / provider defaults / note 等。 |

具体 schema 与字段语义见 [`docs/requirements/012-memory-eval-anchor-recall-scoring/design.md` §6.2](../../docs/requirements/012-memory-eval-anchor-recall-scoring/design.md#62-json-结构schema_version2)。简略：

### `run` 段（运行条件）

| 字段 | 含义 |
|---|---|
| `started_at` / `ended_at` / `duration_seconds` | 跑这一次的起止时间与总耗时 |
| `git_commit` | 跑评测时 HEAD 的短 sha；非 git 环境为 `"nogit"` |
| `working_tree_dirty` | working tree 有未提交改动则 `true`；干净 `false`；非 git 环境 `null`。**dirty 时 `commit` 字段不能完全代表当时实际状态**，对比时要警惕。 |
| `dataset` / `limit_samples` / `limit_questions` | 评测范围 |
| `note` | 跑这次的人写注解（CLI `--note` 传入），方便未来回看"这是为啥跑的" |
| `provider.model` / `api_base` / `defaults` | 抽取阶段 LLM 配置；`defaults.temperature` 等参数会影响抽取确定性 |
| `dataset_files[]` | 每个原始数据文件的 `path` / `sha256` / `bytes`。PerLTQA 自身会更新，没这个 hash 时旧 baseline 与新 baseline 表面一致但实际样本不同。 |

### `macro` 段（核心数字）

| 字段 | 含义 |
|---|---|
| `score_mean` | 已判分题的算术平均分（**核心数字**，对比时主要看它） |
| `n_questions_total` / `n_questions_scored` / `n_questions_zero` | 总题数 / 已判分题数 / 0 分题数 |

### `per_question[]`（单题维度）

| 字段 | 含义 |
|---|---|
| `question_id` | 稳定 ID `<sample_id>#q<idx>`；对比工具按此 join 跨 baseline |
| `sample_id` / `question` / `answer` | 样本标识、问题文本、gold answer（PerLTQA 标注的标准答案） |
| `anchors` | 关键 token 列表（PerLTQA 自带，AnchorRecallJudge 用其 substring 匹配） |
| `score` / `detail` | 分数 + judge 输出（detail 里列未命中 anchor） |
| `recalled_count` | 召回条数（冗余字段，便于快速汇总） |
| `recalled[]` | **完整召回内容**：每条 `{layer, text, source_ref, score}`；用于"为什么这题 0 分"复盘 + 未来换 judge 离线再判分（无需重跑 LLM 抽取） |

## 不要做什么

- **不要为了"清爽"批量删旧文件**——删了就丢了对比基线。真要清理用 `git rm` + commit 留痕，不要悄悄删。
- **不要手动编辑 JSON**——它是机器产物，编辑会失去"记录运行真实状态"的语义。
- **看到 `working_tree_dirty=true` 的 baseline 要警惕**：这次跑可能带着未提交的改动，`commit` 字段记录的并不完整代表当时代码状态。

## PerLTQA 数据片段授权

baseline 文件中会包含 PerLTQA 数据集的小片段（每题的 `question` / `answer` / `anchors`）。PerLTQA 走 **CC BY-NC 4.0（仅限非商用研究）**，本项目作为非商用研究项目使用其少量样本属合理引用。

- 数据来源：<https://github.com/Elvin-Yiming-Du/PerLTQA>（`Dataset/zh/`）。
- **不要将本目录的 baseline 文件用于商用模型训练或商用产品评测**。
- 完整 PerLTQA 数据集本身**不进 git**（仅 `memory_eval/data/perltqa/` 由使用者本地下载）。

## 当前归档

| 文件 | schema | 当时 commit | macro | 简注 |
|---|---|---|---|---|
| `2026-06-11T07-36-01-c8735f0.json` | 1 | `c8735f0`（011 合 main） | 0.133 | **首份归档基线**——hotfix-2 之前以 schema=1 落盘。保留作历史参照、不重跑覆盖。 |
