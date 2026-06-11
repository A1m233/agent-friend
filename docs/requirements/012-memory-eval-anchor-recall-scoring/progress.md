# 012 · PerLTQA 召回判分与首份 baseline — 进度

## 总体状态
<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
COMPLETED

> 全部 5 个里程碑落地；首份 baseline 已生成（macro=0.133，30/30 全判分），schema 与命名符合 design §6。LoCoMo 路径不退化由 test_locomo / test_adapter / mypy strict 覆盖，不额外真跑。

---

## 里程碑与任务

> 任务粒度按"涉及文件 + 涉及单测"组织，便于实施时逐项打勾、断点续传。

### M12.1 datasets 层：anchor 入链路
- [x] `datasets/case.py`：`EvalQuestion` 加 `anchors: list[str] = field(default_factory=list)`
- [x] `datasets/perltqa.py`：新增 `_parse_anchors`；`_parse_questions` 把 `Memory Anchors` 解析填入新字段
- [x] `tests/test_perltqa.py`：补 anchor 解析单测（含正常 / 脏数据：非 list、非 dict、空 key）

### M12.2 判分器：AnchorRecallJudge
- [x] `harness/judge.py`：`JudgeResult` 加 `score: float | None = None`（默认 None，NoopJudge 不变）
- [x] `harness/judge.py`：新增 `AnchorRecallJudge` 类（substring 匹配 + detail 列未命中 + 空 anchors 防御）
- [x] `tests/test_anchor_judge.py`：新建文件，3 个 sanity case（全命中 / 全不命中 / 部分命中 `score == 0.5` 严格相等）+ 一个空 anchors 防御（共 4 个）

### M12.3 Report：汇总输出
- [x] `harness/report.py`：新增 `print_summary(outcomes: Iterable[CaseOutcome])`（macro 平均 + 0 分错题清单）
- [x] `print_outcome` 不动（确认 import / 接口不变）

### M12.4 Baseline 产物
- [x] `harness/baseline.py`：新建模块，`write(outcomes, run, out_dir) -> Path`（实际签名用 `BaselineRun` 聚合元数据，比 design.md 列的散参更紧凑）
- [x] `baseline.write` 内部用 `subprocess.run(["git", "rev-parse", "--short", "HEAD"])` 取 commit，失败降级 `"nogit"`
- [x] `memory_eval/baselines/.gitignore`：内容 `*.json`（目录本身入 git）
- [x] `__main__.py`：PerLTQA 装配 `AnchorRecallJudge`、LoCoMo 仍 `NoopJudge`；循环改造收集 outcomes；跑完调 `print_summary` + `baseline.write`

### M12.5 验证
- [x] `./scripts/check` 全绿（ruff + mypy 124 文件 + 253 pytest 全通过，含本期 4 个 anchor_judge case）
- [x] 真实跑一次 PerLTQA `--limit-samples 3 --limit-questions 10`（已按 `llm-api-confirm` 单独获授权），确认：
  - [x] 控制台输出 macro 平均分 + 0 分错题清单（20 道 0 分题完整列出 anchor + 召回条目）
  - [x] `memory_eval/baselines/2026-06-11T07-36-01-c8735f0.json` 已生成，schema 与 design §6.2 一致（schema_version=1 / run / macro / per_question 30 条）
- [x] LoCoMo 路径不退化：由 `test_locomo.py` + `test_adapter.py` + mypy strict 覆盖（design 允许"能 import 通过即可"，不真跑）

> 注：`./scripts/check` 第 4 步 frontend lint+test 因 Node v20 < v22 跳过，与本期 Python 改动无关。

---

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-11 | M12.1 anchor 入 datasets 链路 | `EvalQuestion.anchors` 加可选字段；`perltqa.py._parse_anchors` 防御式提取；test_perltqa 加 anchors 解析 + 4 类脏数据用例 |
| 2026-06-11 | M12.2 AnchorRecallJudge | `JudgeResult.score` 加可选字段；substring 匹配 + detail 列未命中 anchor；4 个 sanity case（全/全无/部分严格相等/空 anchors） |
| 2026-06-11 | M12.3 print_summary | 新增汇总函数；`print_outcome` 不动；macro 仅基于已判分题、跳过 None |
| 2026-06-11 | M12.4 baseline 落盘 | `baseline.write` + `BaselineRun` 元数据；git short-sha 失败降级 nogit；`baselines/.gitignore = *.json` |
| 2026-06-11 | M12.5 离线检查全绿 + 真跑 baseline | `./scripts/check`：ruff + mypy 124 文件 + pytest 253 通过；真跑 PerLTQA 3×10 → macro=0.133，baseline JSON 命中 schema |

---

## 验收小结

> AC 标号对应 [`requirement.md` §6](./requirement.md#6-验收标准)。

| AC | 标题 | 状态 | 依据 |
|---|---|---|---|
| AC-1 | 判分器接入 | ✅ | PerLTQA 装配 AnchorRecallJudge；真跑 30/30 全判分、score ∈ [0, 1] |
| AC-2 | Report 输出 | ✅ | `print_summary` 输出 macro=0.133 + 20 道 0 分错题完整清单（含 anchor + 召回条目） |
| AC-3 | Baseline 产物 | ✅ | `2026-06-11T07-36-01-c8735f0.json` 含 schema_version=1 / run / macro / per_question 30 条；目录被 `*.json` gitignore 忽略 |
| AC-4 | Sanity case 全过 | ✅ | `tests/test_anchor_judge.py` 4 个用例（含全命中/全不命中/部分严格 0.5/空 anchors）单测全绿，纳入 `./scripts/check` |
| AC-5 | 已有机制不退化 | ✅ | `test_locomo.py` / `test_adapter.py` / mypy strict 全通过；NoopJudge 行为不变 |
