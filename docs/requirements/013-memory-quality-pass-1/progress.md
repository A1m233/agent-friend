# 013 · 记忆质量第一轮迭代（Pass 1）— 进度

## 总体状态
<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
IN_PROGRESS

> M13.1 主因 1（抽取保具体词）实施中。Phase 1 / Phase 2 已 Confirmed：
> - [`requirement.md`](./requirement.md)：本期范围 = issue 003 主因 1 / 2 / 3 + 落对照 baseline
> - [`design.md`](./design.md)：实施按主因 1 → 3 → 2 推进；不引入向量召回 / 不动 conversation.py / 内部 schema 可调

---

## 里程碑与任务

> 任务粒度按"涉及文件 + 涉及单测"组织，便于实施时逐项打勾、断点续传。
> 顺序按 design.md 锁死的依赖关系：**M13.1（抽取） → M13.2（FTS5） → M13.3（pinned gate）**。

### M13.1 主因 1：抽取保具体词

**目标**：让抽取产物保留对话中的具体词（专名 / 时间 / 动作 / 数量 / 物件）；张小红样本 anchor 字面词保留率 ≥ 7/14。

- [x] `memory/src/memory/extraction/prompts/extract.md`：按 design §3.2 重写——保留三原则 + 4 条新约束（保留词类清单 / 12 个禁词 / 拆分而非合并 / 紧凑+具体词）+ few-shot 反例（issue 003 张小红反面教材 + 合格对照）
- [x] `memory/src/memory/extraction/prompts/extract_legacy.md`：新增，备份当前旧 prompt（用于切片 baseline `extraction_keep_specifics=False`）
- [x] `memory/src/memory/extraction/result.py`：`ExtractionOutput.episodic_summary: str | None` → `episodic_entries: list[str]`；同步 `ExtractionResult.episodic_id: str | None` → `episodic_ids: list[str]`
- [x] `memory/src/memory/extraction/extractor.py`：`_parse_output` 兼容新旧字段名（`episodic_summary: str` 与 `episodic_entries: list[str]` 都能解析）
- [x] `memory/src/memory/extraction/reconciler.py`：`apply` 从单条 episodic 改为循环写多条 `EpisodicRow`（每条 entry 一行；provenance 累积全部 episodic_ids）
- [x] `tools/src/tools/cli/__main__.py`：`_make_memory_debug_callback` 适配 `episodic_ids: list[str]`（多条时显示"记下 N 条经历"）
- [x] `memory/tests/test_extraction.py`：新增多 episodic 写入单测 + provenance 累积单测 + 旧 `episodic_summary` 字段兼容性单测；既有 12 个测试已迁移到新字段名
- [x] `./scripts/check`：ruff + format + mypy 124 files + pytest 261 个全过
- [x] `memory/tests/test_extractor_pass1.py`：新建——以张小红 dialogue block `4_0_1#1` + 14 个 anchor 为 fixture，cassette 风格（硬编码真实 LLM raw JSON 输出快照）；3 个断言：anchor 保留率 ≥ 7/14 / pinned 身份事实存在 / 旧 baseline 对照 < 3/14
- [x] **prompt 迭代收敛验证**：**1 轮一次过**——12/14 (85.7%) 远超 7/14 hard floor，无需触发颗粒度回退
- [x] **回填**：`design.md §10` "prompt 迭代轮次"项已 commit 实测结果

### M13.2 主因 3：FTS5 召回 zero-deps 改进

**目标**：FTS5 trigram 配置改进 + 新增 `fts_match_pinned` 接口（为 M13.3 准备）。零依赖，不引入向量召回。

- [ ] `memory/src/memory/store/sqlite_store.py`：`_fts_query` 改进
  - [ ] `_MAX_TRIGRAMS` 从 64 提到 256
  - [ ] 加 bigram 兜底：trigram 凑不出时降级 bigram；bigram 也凑不出（chunk < 2）才返回 None
- [ ] `memory/src/memory/store/sqlite_store.py`：新增 `fts_match_pinned(query, *, owner_user_id, persona_id) -> set[str]`——复用 semantic FTS5 索引，WHERE 加 `pinned = 1`，返回命中 id 集合
- [ ] `memory/tests/test_fts_query.py`：新建（或扩展现有 store 测试）——`_fts_query` 在中文短 query / 长 query / 全短词 query / 极短记忆条目（< 3 字）四类输入下产出符合预期
- [ ] **回填**：`design.md §10` ⏳ "FTS5 `_MAX_TRIGRAMS` 值"项填入实测值（如评测显示 256 不够 / 太多则调整）

### M13.3 主因 2：pinned gate

**目标**：给 pinned 注入加 query relevance gate；身份题 smoke 单测全过；闲聊负样本不误注入。

- [ ] `memory/src/memory/retrieval/pinned_gate.py`：新建——实现 `pinned_gate(query, pinned, *, store, threshold_mode) -> list[SemanticRow]`，复用 `store.fts_match_pinned`
- [ ] `memory/src/memory/facade.py`：`retrieve` 在拿到 pinned 后过 gate；构造期接受 `pinned_relevance_gate: bool` 开关与档位（严格 / 宽松 / 动态）
- [ ] `memory/src/memory/retrieval/__init__.py`：导出 `pinned_gate`（如需）
- [ ] `memory/tests/test_pinned_gate.py`：新建——按 design §5.4 的 5–8 道身份题 fixture（极短 query / 长 query / 近义词 / 同身份多种问法 / 闲聊负样本），断言 gate 输出符合预期。**所有 PR 必跑**
- [ ] **阈值档位实测选定**：先用宽松档跑 fixture + smoke
  - [ ] 宽松档过：选定宽松，进 M13.4
  - [ ] 宽松档误伤极短 query：切动态档（≤ 6 字直接通过；> 6 字走 FTS5 命中判定）
- [ ] **回填**：`design.md §10` ⏳ "pinned gate 阈值档位"项填入选定档位

### M13.4 切片 baseline 配置

**目标**：`build_memory` 三开关 + evaluator runner 透传 + CLI flag。

- [ ] `memory/src/memory/factory.py`：`build_memory` 增加 3 个 kw-only 参数（默认 True）
  - [ ] `extraction_keep_specifics: bool`（True → 用新 prompt；False → 用 `extract_legacy.md`）
  - [ ] `pinned_relevance_gate: bool`（True → 过 gate；False → pinned 全量注入）
  - [ ] `recall_wide: bool`（True → 新 trigram + bigram；False → 旧 64 上限 + 不降级）
- [ ] `memory/src/memory/facade.py`：`Memory.__init__` 接收三开关并据此装配 `Extractor` / `pinned_gate` / `_fts_query` 模式
- [ ] `memory_eval/src/memory_eval/harness/runner.py`：新增 `MemoryConfig` dataclass + `run_case` 接受 `memory_config` 参数透传给 `build_memory`
- [ ] `memory_eval/src/memory_eval/__main__.py`：CLI 加 `--no-extraction-keep-specifics` / `--no-pinned-relevance-gate` / `--no-recall-wide` flag
- [ ] `memory/tests/test_factory_switches.py`：新建（或扩展）——三开关组合的最小 smoke 测试

### M13.5 评测与归档

**目标**：跑 4 份切片 baseline，验证 4 条 AC，落收尾报告。

- [ ] `./scripts/check` 全绿（ruff + mypy + pytest 全通过）
- [ ] 跑 PerLTQA smoke（`--limit-samples 3 --limit-questions 10`）确认 4 种切片配置不挂
- [ ] **跑 4 份全量 baseline**（按 `llm-api-confirm` 单独获授权；预估 8–12 小时，颗粒度回退后 16–24 小时）
  - [ ] `pass-1-full`：三开关全开
  - [ ] `pass-1-only-extraction`：extraction=ON, pinned=OFF, recall=OFF
  - [ ] `pass-1-only-pinned`：extraction=OFF, pinned=ON, recall=OFF
  - [ ] `pass-1-only-recall`：extraction=OFF, pinned=OFF, recall=ON
- [ ] 落到 `memory_eval/baselines/<timestamp>-<commit>-<slice>.json`
- [ ] **AC 验证**（对照 `2026-06-12T01-31-46-46e810d.json`）
  - [ ] **AC-1a**：665 道"有 episodic 但 0 分"题集的零分率必须可观测下降（不持平、不上升）
  - [ ] **AC-1b**：张小红 dialogue block `4_0_1#1` 连续 3 道题至少 1 道脱离 0 分
  - [ ] **AC-2**：全量 macro ≥ **0.219**（hard fail 线，**严格不退化**）
  - [ ] **AC-3**：每片切片 baseline 至少有一个量化指标改善（保留率↑ / 仅 pinned 占零分比↓ / recalled_count 中位数↑）
  - [ ] **AC-4**：008 工程 AC 不失守（跨会话名字 / 不重新自我介绍 / 调试可观测 / persistence）
- [ ] 跑 008 既有验收用例确认 AC-4 通过
- [ ] **写收尾报告**：到 `docs/issues/004-memory-eval-pass-1-result-<日期>/README.md` 或本目录 `report.md`，与 issue 003 同等粒度
- [ ] **回填**：`design.md §10` ⏳ "pass-1 收尾报告位置"项填入实际位置

### M13.6 附带任务

- [ ] `docs/issues/003-memory-eval-baseline-2026-06-12/README.md`：补 footnote 修正"pinned 挤占召回额度"措辞——pinned 是平行通道、不挤占 top-K；"挤占"是结果现象、根因是 episodic/semantic 召不到（详见 design §2.2）
- [ ] `docs/requirements/013-memory-quality-pass-1/design.md` §10：4 个 ⏳ 项全部 commit 实测值
- [ ] `docs/requirements/013-memory-quality-pass-1/progress.md`：实施日志逐里程碑追加
- [ ] git commit 串：每个里程碑独立 commit（用 `gen-commit-message` skill）

---

## 子需求进度
<!-- 本期未拆子需求；如 M13.1 触发颗粒度回退导致 cost 显著上升，再考虑是否拆 pass-1.5 -->
无

---

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-12 | M13.1 任务 1–5 + 测试迁移 + tools/cli 适配 | extract.md 重写 / extract_legacy.md 备份 / result.py 双 dataclass 改名 / extractor `_parse_output` 兼容新旧字段 / reconciler 循环写多条 episodic + provenance 累积 / tools/cli 适配 `episodic_ids` / test_extraction.py 15 个测试全过；`./scripts/check`：ruff + format + mypy 124 + pytest 261 全过 |
| 2026-06-12 | M13.1 任务 6–7 + design.md ⏳ 回填 | test_extractor_pass1.py（cassette 风格 3 断言）；张小红 dialogue block `4_0_1#1` 用新 prompt 真实跑 DeepSeek，**14 个 anchor 命中 12 个 = 85.7%**（pre-pass-1 ~ 0），1 轮一次过、无需颗粒度回退；design.md §10 "prompt 迭代轮次"项回填；mypy 125 files / memory 37 测试全过 |
