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

### M13.2 主因 3：jieba 中文分词召回（**design 变更，2026-06-12**）

**原方案 FTS5 trigram zero-deps + bigram 兜底在实施期实测不可行**（trigram FTS5 索引 token 是 3-字滑窗、bigram phrase 无法 substring 命中 / 中文短 query 与 pinned 无共享 trigram），改为引入 jieba 中文分词替换 FTS5 内置 trigram tokenizer。详见 [`design.md §4 / §11`](./design.md)。

- [x] `memory/pyproject.toml`：加 `jieba>=0.42.1` 依赖
- [x] `memory/src/memory/store/schema.py`：升 schema v2
  - [x] `semantic` 表加 `statement_tokens TEXT NOT NULL DEFAULT ''` 列
  - [x] `episodic` 表加 `summary_tokens TEXT NOT NULL DEFAULT ''` 列
  - [x] `semantic_fts` / `episodic_fts` 改成 `tokenize='unicode61'` 索引新 `*_tokens` 列
  - [x] `SCHEMA_VERSION` 常量从 1 升到 2
- [x] `memory/src/memory/store/sqlite_store.py`：
  - [x] 顶层加 `_tokenize(text) -> str`（jieba.cut + 空格连接）
  - [x] `add_semantic` / `add_episodic` 落库时计算 `*_tokens` 列
  - [x] `_fts_query` 改成 jieba 切词后空格连接（去重 + 跳过纯标点 token + `_MAX_QUERY_TOKENS=64` 上限）
  - [x] 新增 `fts_match_pinned(query, *, owner_user_id) -> set[str]` 接口（为 M13.3 准备；底层走 jieba 索引；pinned 是 user 维度只按 owner 过滤）
  - [x] `SqliteMemoryStore.__init__` 加 lazy migration：`_premigrate_v1_to_v2` 在 DDL 前 ALTER TABLE 加列 + DROP 旧 trigram FTS5；DDL 后 `_backfill_tokens_and_rebuild_fts` 回填并重建索引；最后 UPDATE schema_meta v2
- [x] `memory/tests/test_store.py`：
  - [x] 改写 `test_search_too_short_query_returns_empty` → `test_search_single_char_query_now_hits_via_jieba`（反映 jieba 单字也是 token 的新行为）
  - [x] 新增 `test_search_empty_or_punctuation_query_returns_empty`（空/纯标点仍空）
  - [x] 新增 `test_search_two_char_chinese_word_hits_via_jieba`（2 字 query "宠物" 命中）
  - [x] 新增 `test_search_long_chinese_query_hits_via_shared_word`（长 query 共享 "名字" 命中 pinned）
  - [x] 新增 4 个 `test_fts_match_pinned_*`（命中相关 pinned / 不相关空集 / 非 pinned 忽略 / 空 query 空集）
  - [x] 新增 `test_v1_to_v2_lazy_migration`（手搓 v1 库 + 验证启动后自动升 v2 + jieba 召回旧数据）
- [x] `./scripts/check`：ruff + format + mypy + pytest **272 全过**
- [x] **回填**：`design.md §10` ~~"FTS5 `_MAX_TRIGRAMS` 值"~~ 项已废弃（design §11 变更记录登记）

### M13.3 主因 2：pinned gate

**目标**：给 pinned 注入加 query relevance gate；身份题 smoke 单测全过；闲聊负样本不误注入。

- [x] `memory/src/memory/retrieval/pinned_gate.py`：新建——实现 `pinned_gate(query, pinned, *, store, owner_user_id, mode) -> list[SemanticRow]`，复用 M13.2 的 `store.fts_match_pinned`；`GateMode` Literal 类型
- [x] `memory/src/memory/facade.py`：`retrieve` 拿到 pinned 后过 gate；`Memory.__init__` 加 `pinned_relevance_gate: bool = True` 与 `pinned_gate_mode: GateMode = "lenient"` 两个 kw-only 开关
- [x] `memory/src/memory/retrieval/__init__.py`：导出 `pinned_gate` / `GateMode`
- [x] `memory/tests/test_pinned_gate.py`：新建——4 道身份级 pinned fixture + 9 个测试（lenient 5 个 / strict 2 个 / facade 集成 2 个：默认开 gate 砍闲聊 / 关 gate 走 pre-pass-1）；**所有 PR 必跑**
- [x] `memory/tests/test_facade.py`：旧测试 `test_pinned_always_injected_even_without_query_match` 改写为 `test_pinned_dropped_when_query_unrelated_with_default_gate`（反映 pass-1 新默认行为）
- [x] **阈值档位实测选定**：design §5.3 描述的"宽松"与"动态"档算法等价（短 query 全通过 + 长 query FTS5 判定），合并为 **`lenient`** 一档（默认）；保留 `strict` 档供切片 baseline / future tuning
- [x] **回填**：`design.md §10` "pinned gate 阈值档位"项已 commit 实测结果
- [x] `./scripts/check`：ruff + format + mypy + pytest **281 全过**

### M13.4 切片 baseline 配置（**design 二次变更，2026-06-12**）

**M13.4 实施期调研 LlamaIndex / Martin Fowler 后**：原"三开关 config 含 `recall_wide`"方案违背业界最佳实践（schema 不可逆迁移后做同进程切片会污染归因）。改为"两开关 + pass-1-baseline 切片对比 pre-pass-1 文件"路线。详见 [`design.md §6 / §11`](./design.md)。

- [x] `memory/src/memory/factory.py`：`build_memory` 增加 2 个 kw-only 参数（默认 True）
  - [x] `extraction_keep_specifics: bool`（True → 用新 prompt；False → 用 `extract_legacy.md`）
  - [x] `pinned_relevance_gate: bool`（True → 过 gate；False → pinned 全量注入，pre-pass-1 行为）
  - [x] ~~`recall_wide: bool`~~ → **移除**（jieba schema 迁移已完成，ablation 走文件对比路线）
- [x] `memory/src/memory/facade.py`：`Memory.__init__` 接收两开关（M13.3 已加 `pinned_relevance_gate`；M13.4 通过 factory 透传）
- [x] `memory_eval/src/memory_eval/harness/runner.py`：新增 `MemoryConfig` dataclass（2 字段）+ `run_case` 接受 `memory_config` 参数透传给 `build_memory`
- [x] `memory_eval/src/memory_eval/harness/__init__.py`：导出 `MemoryConfig`
- [x] `memory_eval/src/memory_eval/__main__.py`：CLI 加 `--no-extraction-keep-specifics` / `--no-pinned-relevance-gate` flag，构造 `MemoryConfig` 透传给 `run_case`
- [x] `memory/tests/test_factory_switches.py`：新建——5 个 smoke 测试（默认 = pass-1 终态 / 关 extraction 用旧 prompt / 关 gate / 两开关都关 = pass-1-baseline 切片 / 显式 `extractor_prompt` 覆盖开关）
- [x] `./scripts/check`：ruff + format + mypy + pytest **286 全过**（281 → 286）

### M13.5 评测与归档

**目标**：跑 4 份切片 baseline，验证 4 条 AC，落收尾报告。

- [ ] `./scripts/check` 全绿（ruff + mypy + pytest 全通过）
- [ ] 跑 PerLTQA smoke（`--limit-samples 3 --limit-questions 10`）确认 4 种切片配置不挂
- [ ] **跑 4 份全量 baseline**（按 `llm-api-confirm` 单独获授权；预估 8–12 小时，颗粒度回退后 16–24 小时）
  - [ ] `pass-1-full`：两开关全开（jieba 永远开） —— 对照 pre-pass-1 看 pass-1 总体收益
  - [ ] `pass-1-only-extraction`：extraction=ON, pinned=OFF —— 对照 pass-1-baseline 看主因 1 单独贡献
  - [ ] `pass-1-only-pinned`：extraction=OFF, pinned=ON —— 对照 pass-1-baseline 看主因 2 单独贡献
  - [ ] `pass-1-baseline`：extraction=OFF, pinned=OFF（仅 jieba 永远开）—— 对照 pre-pass-1 看主因 3 jieba 单独贡献
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
| 2026-06-12 | M13.1 commit + 工具链小修 | commit `bc5c28e` feat(memory) M13.1；commit `c84411a` chore(tooling) pyproject 加 `.claude/worktrees` 到 ruff/mypy exclude（之前 worktree 干扰 ruff format --check） |
| 2026-06-12 | **分支整理**：两 commit 误落 main，迁到 `feature/013-memory-quality-pass-1` | 项目惯例所有 013 改动走 feature 分支；origin/main 未受影响，操作完全本地 |
| 2026-06-12 | **M13.2 design 变更**：trigram + bigram 兜底实测不可行 → 改 jieba 中文分词 | SQLite FTS5 trigram 索引 token 是 3-字滑窗、bigram phrase 不能 substring 命中；中文短 query 与 pinned 条目无共享 trigram。design.md §4 / §5 / §10 / §11 已同步；M13.2 任务清单重写。requirement.md 未变（jieba 在 Q-4 候选） |
| 2026-06-12 | M13.2 任务全部完成 | pyproject 加 jieba 依赖；schema 升 v2（`*_tokens` 列 + FTS5 unicode61）；sqlite_store `_tokenize` / `_fts_query` 改 jieba；新增 `fts_match_pinned`；`__init__` 加 v1→v2 lazy migration；test_store 改写既有"过短 query"测试 + 新增 8 个 jieba/migration 测试；`./scripts/check`：ruff + format + mypy + pytest **272 全过**（pre-M13.2 261 + M13.1 3 + M13.2 8） |
| 2026-06-12 | M13.3 任务全部完成 + design.md ⏳ 回填 | `pinned_gate.py` 新建（`lenient` / `strict` 两档；短 query < 6 字 lenient 放行）；facade `Memory` 加 `pinned_relevance_gate` / `pinned_gate_mode` 开关；retrieval `__init__` 导出；test_pinned_gate 9 个测试 + test_facade 旧 "pinned 永远附带" 改写为 "无关 query 砍 pinned"（反映新默认行为）；design.md §10 "pinned gate 阈值档位"项回填（实施合并"宽松/动态"为 `lenient`）；`./scripts/check`：281 全过（272 → 281）|
| 2026-06-12 | **M13.4 design 二次变更**：`recall_wide` 切片在 jieba 不可逆迁移下污染归因 → 调研 LlamaIndex / Martin Fowler 后改"两开关 + 文件对比" | WebFetch 三条权威：Strangler Fig（生产迁移并行路径）/ LlamaIndex Component-wise Evaluation（**主流是跑独立评测、比较 baseline 文件**，不在同管线切片）/ Martin Fowler Feature Toggles（劝阻永久 ablation flag）；选 LlamaIndex 路线。design.md §6 改两开关；§11 二次变更登记；切片总数仍为 4 份（`pass-1-full` / `pass-1-only-extraction` / `pass-1-only-pinned` / `pass-1-baseline`），新 `pass-1-baseline` 切片度量 jieba 单独贡献 |
| 2026-06-12 | M13.4 任务全部完成 | `build_memory` 加 `extraction_keep_specifics` / `pinned_relevance_gate` 两个 kw-only 开关；`extract_legacy.md` 路径常量；runner 新增 `MemoryConfig` dataclass + 透传；harness `__init__` 导出；CLI 加 `--no-extraction-keep-specifics` / `--no-pinned-relevance-gate` flag；新增 `test_factory_switches.py` 5 个 smoke 测试（默认 = pass-1 终态 / 关 extraction 用旧 prompt / 关 gate / 两开关都关 = pass-1-baseline 切片 / 显式 prompt 覆盖开关）；`./scripts/check`：286 全过（281 → 286）|
