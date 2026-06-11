# 011 记忆召回质量评测机制 - 进度

## 总体状态
<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
IN_PROGRESS（PoC 代码 + 单测全部落地；机制类 AC-1~AC-6 已逐条跑通；requirement / design / progress 文档补齐中，待合入 main）

> 本需求是**先实现 PoC、后补文档**：代码先行落地于 `feature/011-memory-recall-eval` 分支，文档按 `feature-delivery` 回填以保证可追溯。

## 里程碑与任务

### M11.1 评测机制骨架（datasets / adapters / harness）
- [x] `datasets/case.py`：EvalCase / EvalTurn / EvalQuestion 统一契约
- [x] `datasets/locomo.py`：英文基线 loader（整数 category → 标签映射）
- [x] `adapters/memory_adapter.py`：按 session 投影 fragment、角色映射、`flush` 阻塞
- [x] `harness/runner.py`：`run_case` 编排 + 每 case 独立库隔离
- [x] `harness/judge.py`：Judge 协议 + NoopJudge（扩展点，本期不打分）
- [x] `harness/report.py`：`print_outcome` 控制台展示
- [x] `__main__.py`：CLI（`--dataset` / `--limit-*` / `--model`）
- [x] `scripts/memory-eval/run.{sh,ps1}` 双端一键脚本 + `scripts/README.md` 登记
- [x] 顶层 `memory_eval/` 包接入 root `pyproject.toml`（workspace / sources / testpaths）

### M11.2 中文 benchmark 接入（PerLTQA）
- [x] `datasets/perltqa.py`：dialogues 子集 loader（按时间块切 session、Reference Memory 容错）
- [x] PerLTQA 数据落位 `memory_eval/data/perltqa/`（`.gitignore` 忽略，使用者自备）
- [x] 默认 benchmark 切到 PerLTQA，LoCoMo 保留为英文基线

### M11.3 自测 + 验证
- [x] 单测：`test_locomo.py` / `test_perltqa.py` / `test_adapter.py`（fake LLM，不触发真实调用）
- [x] `./scripts/check`（lint + mypy + test）全绿
- [x] 真 LLM 端到端冒烟：PerLTQA 灌入 `observe` → 问题 `retrieve` 跑通、有召回产出

## 实现日志
| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-11 | 评测机制骨架 + LoCoMo loader 落地 | 顶层独立包 memory_eval；分支 feature/011-memory-recall-eval |
| 2026-06-11 | 切到中文 PerLTQA（dialogues 子集）为默认 benchmark | 英文 LoCoMo 在中文 memory 上语言错配，改用原生中文基准 |
| 2026-06-11 | 真 LLM 端到端跑通 PerLTQA | 机制确认可跑通、有召回产生；观察到 episodic 抽取偏概括（属 008 改进点，见 requirement §7 Q-2） |
| 2026-06-11 | 回填 requirement / design / progress 文档 | 按 feature-delivery 补全可追溯文档 |

## 验收小结

> 全部为**机制层面**过程性 AC（验证"能不能测"），不含召回质量指标。

| AC | 标题 | 状态 | 依据 |
|---|---|---|---|
| AC-1 | 加载跑通 | ✅ | loader 单测 + 真跑均能归一成 EvalCase，limit 生效 |
| AC-2 | 端到端跑通 | ✅ 真 LLM 冒烟 | PerLTQA observe→retrieve 完整跑完，有可读召回展示 |
| AC-3 | 可插拔验证 | ✅ | PerLTQA / LoCoMo 共用同一 adapter / runner / report，仅 loader 不同 |
| AC-4 | 判分扩展点就位 | ✅ 离线 | Judge 协议 + NoopJudge，runner 始终调用 |
| AC-5 | 机制自测 | ✅ 离线 | 解析 / 适配单测（fake LLM）；`./scripts/check` 全绿 |
| AC-6 | 一键运行 | ✅ 离线 | `scripts/memory-eval/run.{sh,ps1}` 双端 |

> AC-2 为真实 LLM 路径，按 `llm-api-confirm` 已获授权后运行；验收以"机制跑通、确有召回产出"为准，不评判召回正确率（见 requirement §6）。
