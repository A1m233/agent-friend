# 048 · 记忆检索质量优化第二阶段 · 进度

## 总体状态

IN_PROGRESS

## 当前门禁

实现、自动化门禁和一次性 Flash 终局 2×2 均已完成。终局结果证明完整方案相对并发 baseline 显著提升，但抽取独立贡献未通过预注册门禁：

- E0R0（pass-1 抽取 + legacy 检索）macro `0.372821`，零分率 `42.88%`。
- E0R1（pass-1 抽取 + 048 检索）macro `0.424010`，相对 E0R0 提升 `5.12pp`。
- E1R0（048 抽取 + legacy 检索）macro `0.369322`，相对 E0R0 下降 `0.35pp`，`extraction_non_regression=false`。
- E1R1（048 完整终态）macro `0.434399`，相对 E0R0 提升 `6.16pp`；零分率降至 `35.90%`。
- E1R1 对 E0R0 的逐题 mean delta 为 `0.061578`，paired bootstrap 95% CI 为 `[0.046784, 0.076369]`；712 题提升、424 题退化、1574 题不变。
- 六项终局 gate 中五项通过，唯一失败项为 extraction non-regression，因此 `all_gates_passed=false`，按预注册 AC 不把 048 标记为质量优化全部完成。
- 用户已知悉该结果，并于 2026-07-15 明确授权以“完整方案整体显著提升”作为合入依据，将本次合入视为验收例外；文档保留失败 gate，不作事后门槛改写。

## 任务列表

- [x] Phase 1：确认需求范围与验收标准。
- [x] Phase 2：确认统一检索底座、显式召回模式、Core / priority pinned 边界与 M48.0 前置门禁。
- [x] M48.0：实现可复现的关键词、历史扫描与 Chroma + BGE-small-zh benchmark。
- [x] M48.0：补齐双端脚本、可选依赖与自动化测试。
- [x] M48.0：在 Windows 上完成真实本地模型 benchmark，记录质量、启动、延迟、RSS 与磁盘数据。
- [x] M48.0b：完成 ONNXRuntime + sqlite-vec 的 10000 条 Windows benchmark，并锁定最终底座方向。
- [x] M48.0：回填 `benchmark-report.md`、`design.md` 的参数与结论。
- [x] M48.0：提交用户 review，确认后才能开始 M48.1。
- [x] M48.1：统一搜索契约、共享 analyzer、SessionHistorySource 与 recall tool 迁移。
- [x] M48.2：schema v4、core / priority / normal、受限 fact_key、原子 supersede 与旧 pinned 安全迁移。
- [x] M48.3：ONNX EmbeddingProvider、sqlite-vec VectorIndex、manifest / generation、后台回填、RRF 与 stale validity recheck。
- [x] M48.4：`Memory.retrieve` 统一接入、core / recalled / evidence renderer、自动 history fallback、扩展 trace 与 inspector 字段。
- [x] M48.5：memory_eval 六切片、agent_eval C8 证据使用切片与确定性测试。
- [x] 运行非 LLM AC、真实本地 dense smoke 与 `scripts/check` 全量门禁。
- [x] 经当次授权运行 `agent_eval C8` 真实 LLM 验收并记录失败归因。
- [x] 运行首次 Flash PerLTQA 全量 baseline，识别出 hybrid 未实际启用与单次抽取不可归因问题。
- [x] 改造 memory_eval：持久 case DB、逐 case checkpoint、断点续跑、三模式复放、hybrid fail-closed 与候选 trace。
- [x] 安装锁定 revision 的生产 dense 资产，并用真实 ONNX + sqlite-vec 验证 hybrid `ready` 与 `dense_rank`。
- [x] 经当次授权只做一次 Flash 全量抽取，再在同一 workspace 复放 legacy keyword / keyword / hybrid，核对 hard floor 与独立增益。
- [x] 对冻结 DB 做 anchor presence 归因，并补逐 fragment 抽取诊断与可恢复的小样本稳定性 probe。
- [x] 修订 AC 与设计：拆分检索硬门禁、抽取稳定性诊断和 PerLTQA 观察证据，取消单次随机抽取 hard floor。
- [x] 扩展稳定性 summary：直接输出稳定/不稳定、状态翻转、三种输出及逐轮 semantic / episodic 分布。
- [x] 增加 `unified-keyword / unified-hybrid` 冻结 DB replay，复用生产 StructuredMemorySource + SessionHistorySource 装配。
- [x] 修复 automatic raw-history fallback 的中文分词、低置信判定、独立证据预算与相邻问答 excerpt。
- [ ] 在 checkpoint harness 上完成 Pro 对照；Pro 只作第二模型证据，不替代 Flash AC。
- [x] 完成 30×2×3 Flash AB/BA 配对抽取诊断，定位 E1 相对 E0 的抽取契约净影响。
- [x] 按预注册规则只实施一次根因修复与一次小样本确认，冻结候选。
- [x] 执行 E0/E1 × R0/R1 全量终局，报告 paired bootstrap CI 与关键切片门禁。
- [x] 用户知悉 extraction non-regression 未通过，并明确授权带该验收例外合入 main。

## 实现日志

| 日期 | 完成内容 | 备注 |
|---|---|---|
| 2026-07-14 | Phase 1/2 完成，用户授权进入 Phase 3。 | 当前只推进 M48.0；外部 LLM 调用仍按每次实际运行单独确认。 |
| 2026-07-14 | 建立 M48.0 实施门禁与任务清单。 | benchmark 使用本地 embedding；在需要下载依赖 / 模型时单独处理网络授权。 |
| 2026-07-14 | 完成 M48.0 benchmark harness、双端入口和确定性测试。 | 5 项测试通过；脚本全程 0 次外部 LLM 调用。 |
| 2026-07-14 | 完成 Windows 真实本地 benchmark。 | 1000 条语料、16 查询、5 轮 warm；读取 31 个本地 session 时只记录规模/耗时，不记录正文。Chroma 临时索引句柄问题已通过公开 `close()` 修复后复跑通过。 |
| 2026-07-14 | 完成 M48.0a 报告与设计回填，用户确认继续 M48.0b。 | RSS 上限 200MB；macOS 暂不纳入当前门禁。 |
| 2026-07-14 | 完成 M48.0b 轻量候选实测。 | 10000 条：量化 ONNX + sqlite-vec RSS +191.648MB、query p95 37.750ms、模型 23.179MB、索引 20.680MB；锁定 CPU ONNXRuntime + tokenizers / sqlite-vec，等待用户最终 review。 |
| 2026-07-14 | 完成 M48.0 收尾验证。 | benchmark 聚焦测试 6 项通过；`scripts/check` 全绿：800 项 Python、54 个前端 test files / 302 tests，lint、mypy、tsc 与 guards 全过。 |
| 2026-07-14 | 用户确认 M48.0b 最终结果，解除 M48.1 门禁。 | 开始统一搜索契约与 SessionHistorySource；本里程碑不调用外部 LLM。 |
| 2026-07-14 | 完成 M48.1 统一检索底座与显式历史工具迁移。 | 新增 search contracts / deterministic analyzer / service / StructuredMemorySource / SessionHistorySource；共享 time parser；`recall_past_chats` 保持旧参数与 JSON 边界，并新增 persona/current-turn 隔离及 source 部分降级。 |
| 2026-07-14 | 完成 M48.1 验证。 | 聚焦回归 85 项通过；`scripts/check` 全绿：815 项 Python、54 个前端 test files / 302 tests，lint、mypy、tsc 与 guards 全过；0 次外部 LLM 调用。 |
| 2026-07-14 | 完成 M48.2 core 与轻量事实版本。 | schema 升至 v4；旧 pinned 只把高置信姓名 / 核心关系迁为 core，其余迁为 priority；fact_key supersede 在同一 SQLite 事务完成，current / historical 查询有回归测试。 |
| 2026-07-14 | 完成 M48.3 hybrid retrieval 与派生索引生命周期。 | 实现 ONNXRuntime + tokenizers provider、sqlite-vec index、可恢复 manifest / generation、完整 generation 原子激活、旧 generation 清理、RRF、dense stale 二次校验；资产缺失时保持 keyword-ready。 |
| 2026-07-14 | 完成生产 dense 资产发现与本地 smoke。 | benchmark 脚本新增锁 revision 的 `--install-production-assets`；临时目录实测真实 ONNX 512 维 embedding + sqlite-vec 查询通过，0 次外部 LLM 调用，临时资产已清理。 |
| 2026-07-14 | 完成 M48.4 统一读路径与可观测。 | `Memory.retrieve`、`recall_past_chats` 共用同一 service；automatic 仅在 history / temporal / last_interaction 且结构化候选不足时回退 raw history；renderer 与 trace 展示 core / recalled / evidence、source runs、融合决策、预算和 dense 状态。 |
| 2026-07-14 | 完成 M48.5 非 LLM 评测层。 | `memory_eval` 覆盖 identity / paraphrase / temporal / update / abstention / consistency 六切片；`agent_eval` 新增 C8 六条证据使用 fixture，分别定位 retrieval failure 与 generation failure。 |
| 2026-07-14 | 完成非 LLM 全量门禁。 | ruff、format、mypy 全绿；827 项 Python 测试通过；前端 ESLint / color / UI provenance / design token / tsc 全绿，54 个 test files / 302 tests 通过。未执行真实桌面视觉验收；本期仅做 inspector 字段增量。 |
| 2026-07-14 | 完成首次真实验收与下降归因。 | C8 4/6；Flash PerLTQA macro 0.3675 / 零分率 43.47%。确认 full run 未启用 dense，且 491 道退步题混合了排序、抽取/版本与 substring judge 偏差；Pro 30/32 后异常退出且旧 harness 无 checkpoint。 |
| 2026-07-14 | 完成可归因评测基础设施。 | 抽取 DB 持久化；legacy keyword / keyword / hybrid 可复放同一 corpus；逐 case checkpoint / resume；hybrid 缺资产或索引未 ready 直接失败；baseline schema v3 记录 retrieval mode、case index state 和候选 rank trace。 |
| 2026-07-14 | 完成新评测层非 LLM 验证。 | 40 项 memory_eval / factory 聚焦测试通过；项目门禁全绿：832 项 Python 通过、1 项需本地模型环境变量的可选 smoke 跳过，54 个前端 test files / 302 tests；该可选 smoke 已在 Windows 单独实跑通过，真实量化 ONNX + sqlite-vec 达到 ready、indexed=total 且 trace 存在 dense_rank。 |
| 2026-07-14 | 完成 Flash 公平全量与三模式复放。 | 31 cases / 1072 次抽取；legacy 0.2426、keyword 0.2424、hybrid 0.2460。hybrid 相对 keyword 提升约 0.36pp，但三者均显著低于 pass-1 hard floor，排除新 RRF 是大幅下降主因。 |
| 2026-07-14 | 完成冻结语料归因。 | 10368 个 anchor 中 retrieved 2395、active 未召回 878、historical-only 7、所有版本缺失 7088；1691 道零分题中 1479 道属于语料缺失。旧 2026-07-13 运行命中的 3620 个 anchor 有 1530 个在本次全部版本中消失。 |
| 2026-07-14 | 增加抽取稳定性诊断基础设施。 | 生产 Extractor 增加默认关闭的 attempt observer；memory_eval 显式记录原始响应、调用/解析状态、耗时与产出数，逐 fragment checkpoint；固定 hash 小样本支持重复运行、输出 hash、配置锁和 resume。聚焦测试 29 项通过、1 项本地 ONNX 可选 smoke 跳过；未调用真实 LLM。 |
| 2026-07-14 | 完成 Flash 小样本稳定性实验。 | 授权上限 90 次，因首版 probe id 未纳入 fragment 正文，5 个同 source_ref 片段被误去重，实际执行 75 次（25×3）。54 次 success、21 次稳定 empty、0 API/解析错误；7 个 fragment 三次均空，18 个三次均成功，但 18/18 输出内容不稳定、16/18 三次结果各不相同，semantic 条数 48/44/39、episodic 41/38/42。已修 probe id，把 speaker/text/ts/source_ref 全部纳入 hash，并补碰撞回归测试；未擅自补跑剩余 15 次。 |
| 2026-07-14 | 增加抽取 temperature 显式对照。 | memory_eval CLI 新增 `--temperature`，允许 `0`，实际值进入 ProviderSpec defaults 并随 workspace manifest / baseline 记录；9 项聚焦测试与目标 mypy 通过，未调用真实 LLM。 |
| 2026-07-14 | 完成 Flash `temperature=0` 稳定性对照。 | 按授权执行 30 个唯一 fragment × 3，共 90 次调用；48 success、42 empty、0 API / 解析错误。22/30 输出不稳定，11/30 在 empty / success 间翻转，15/30 三次输出各不相同；semantic 总数 28/25/33，episodic 36/35/44。manifest 确认实际 temperature 为 0；结论是降温未解决抽取非确定性，不建议据此重跑 PerLTQA 全量。 |
| 2026-07-14 | 完成评测口径回溯与稳定性报告升级。 | requirement / design 改为三层验收；PerLTQA 单次真实抽取从 hard floor 改为观察性证据。summary 新增 stable/unstable、always-empty/always-success/mixed、三种输出数和逐轮产出分布；定向测试 2 项、ruff lint / format 全过，0 次 LLM 调用。 |
| 2026-07-14 | 完成生产 unified path 公平回放与修复。 | 初始 unified-keyword 与结构化 keyword 同分 0.242425：2736 题仅 72 题检查 history，且中文整句 token 导致 0 候选。修复共享 jieba、query 覆盖低置信判定、结构化 top-8 + raw 独立 4 条预算、相邻用户/assistant excerpt 后，同一冻结 DB 达到 macro 0.339895、零分 1278/2710（47.16%）；相对结构化 keyword 504 题提升、0 题退步、零分少 411。相关测试 33 项通过、1 个本地 ONNX 可选项跳过；0 次 LLM 调用。仍低于 pass-1 观察参考 0.3783，剩余缺口继续归因到抽取 empty / 语料缺失，不标记完成。 |
| 2026-07-14 | 完成 unified-hybrid 终态冻结 DB 回放与 empty 重试收益审计。 | unified-hybrid macro 0.341112、零分 1275/2710（47.05%）；相对 structured-hybrid 0.246030 有 494 题提升、0 题退步。相对 unified-keyword 仅 +0.001217，112 题升 / 99 题降，dense 净收益很小。已有 temperature=0 30×3 样本离线模拟：0 次重试成功 17/30；1 次重试 19/30、调用量 43；2 次重试 22/30、调用量 54，成本收益不足，因此不加入盲目 empty retry。全程 0 次新 LLM 调用。 |
| 2026-07-14 | 预注册“清 bug 后证明自己”的终局实验。 | 锁定 E0/E1 × R0/R1、AB/BA、30×2×3 诊断、一次修复上限、全量 holdout 一次性运行及严格成功门槛；后续不得用单次随机结果或局部指标替代相对 baseline 的证明。 |
| 2026-07-14 | 完成 pass-1 / current 30×2×3 Flash AB/BA 诊断。 | 180/180 调用完成、0 API/解析错误。pass-1 53/90 success、113 semantic、specific-token recall 18.85%；current 46/90 success、84 semantic、16.89%。pass-1 成功/current 空 10 对，反向 3 对，24 对明确回归；证实新增 memory_class / fact_key / pinned 指令使合法空输出增加并压低 semantic 覆盖。 |
| 2026-07-14 | 实施预注册的唯一一次抽取根因修复。 | prompt 精确恢复 origin/main pass-1 契约；memory_class / fact_key / pinned 的 048 存储语义继续由 parser / reconciler 确定性推导。prompt 与 origin/main diff 为 0，ruff 通过，相关 20 项测试通过；待经单次授权做小样本确认。 |
| 2026-07-14 | 完成修复后 10×2 Flash 小样本确认并冻结候选。 | 20/20 调用完成，0 API/解析错误；相同 10 probes 上 pass-1 为 success 50.0% / semantic 1.03 每调用，旧 current 为 43.3% / 0.73，修复后为 45.0% / 0.85。方向上较旧 current 恢复但样本不足以宣称显著；结合 prompt 已恢复为 pass-1 契约，按 stop rule 冻结，不再调 prompt，成败交给全量 2×2。 |
| 2026-07-14 | 完成终局 factorial harness 与 bug 清理。 | 新增 E0/E1 逐 fragment AB/BA checkpoint、从输出重建独立 DB、E0R0/E0R1/E1R0/E1R1 四条件完整 baseline、逐题 paired bootstrap CI 与硬门禁报告。修复 pass-1 prompt 下 fact_key / memory_class 确定性推导缺失，以及 automatic 相邻问答扩展污染显性 recall 工具 said_by / result_count 的策略越界。 |
| 2026-07-14 | 终局运行前标准门禁全绿。 | Ruff 330 文件、mypy strict 296 文件、Python 848 passed / 1 个本地 ONNX 可选 smoke skipped；前端 ESLint / guards / tsc 全绿，Vitest 54 files / 302 tests。确认全量规模为 31 cases、每套 1072 fragments、E0/E1 合计 2144 次 Flash 调用、2736 道问题。 |
| 2026-07-14 | 完成 origin/main baseline parity gate。 | 隔离 worktree 真实执行 pass-1 pipeline，并用同一组姓名、雇主更新、偏好 raw outputs 驱动当前 E0+R0。首次发现 E0 被 048 metadata 推导污染；修复为 E0 使用 pass-1 metadata 解析/落库、E1 使用 048 确定性推导。最终共享 DB 语义、历史失效状态、召回正文/层级/顺序和逐题分数完全一致。 |
| 2026-07-14 | parity 修复后最终标准门禁全绿。 | Ruff 332 文件、mypy strict 298 文件、Python 849 passed / 2 skipped；其中 origin parity 在常规环境按设计跳过，但已在隔离 origin/main worktree 显式通过。前端 ESLint / guards / tsc 全绿，Vitest 54 files / 302 tests。已具备执行一次性 2144-call factorial holdout 的条件。 |
| 2026-07-15 | 完成一次性 Flash 终局 2×2 holdout。 | 31 cases、E0/E1 共 2144 次调用；E0R0 `0.372821`、E0R1 `0.424010`、E1R0 `0.369322`、E1R1 `0.434399`。完整终态提升 `6.16pp`，95% CI 严格为正；extraction non-regression 未通过，总 gate 为 false。 |
| 2026-07-15 | 用户明确授权带验收例外合入 main。 | 不修改预注册 AC、不宣称全部门禁通过；以完整方案相对并发 baseline 显著提升作为本次合入依据，抽取独立退化事实继续保留。 |
