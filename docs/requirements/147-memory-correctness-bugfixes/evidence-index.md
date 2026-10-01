# 147 · 证据索引

## 范围与版本

本索引服务 [147 记忆正确性修复](requirement.md)，区分实际运行时观察、源码机制与构造复现。用户原始合同、个人身份、地址、完整聊天、模型请求与待处理正文不入仓库。

- 初次诊断工作区与当前安装版锚点：`f22fbb6d4df7db3f600a7bb5c35d73e05419abf2`。
- 指定附件交互的运行日志身份：`38a2811a18080eb4141e1adaa03614ba600365a6`。
- 147 起始主干：`71cc9094a89d96408aa24be314698809c885eab0`。
- 本轮离线探针运行于初次诊断工作区，使用固定模型输出及内存 SQLite，无真实 LLM 调用。旧工作区到起始主干的差异已核对：本需求相关 extractor、worker、reconciler、store、Core contracts、memory feed 与 facade 未变化；搜索模块另有 146 的字段与诊断变更。该核对提供复现锚点，不把旧探针改写成运行于新主干。
- 所有日期以北京时间描述；数据库原始时间采用 UTC。

## 当前判断

已确认五项可重复的实现问题，编号 E-002～E-006。E-001 是触发本次调查的已确认附件输入缺口，按用户边界不在本期实施。冷却、恢复批量及模型选择策略作为约束，不因产生积压直接归为 bug。

技术方案已获确认，五项生产代码修复与确定性回归已落地，进入最终门禁与用户验收阶段。个人记忆、用户运行实例和用户抽取配置未修改。既有 88 项基线不能代替新回归；实施验证见 E-010。

## 证据账本

| ID | 主张 | 基础 / 置信度 | 状态 | 决策影响 |
| --- | --- | --- | --- | --- |
| E-001 | 附件不进入记忆素材投影 | runtime-supported + code-supported + reproduction-supported / confirmed | retained | 本期排除，不用历史补建掩盖输入缺口 |
| E-002 | 非协议对象被提交为空成功 | reproduction-supported + code-supported / confirmed | active | R-1 |
| E-003 | 来源失效漏掉已退休版本 | reproduction-supported + code-supported / confirmed | active | R-2；未发现本机现存数据命中 |
| E-004 | 延后入库改变有效时间起点 | runtime-supported + reproduction-supported + code-supported / confirmed | active | R-3 |
| E-005 | 无法解码的队首阻断后续正常记录 | reproduction-supported + code-supported / confirmed | active | R-4；异常数据构造场景 |
| E-006 | 部分 Core 损坏绕过不可用处理 | reproduction-supported + code-supported / confirmed | active | R-5；异常数据构造场景 |
| E-007 | 有界恢复及部分生命周期是已确认设计 | document-supported + code-supported / confirmed | active | 约束修复，不扩大机制 |
| E-008 | 既有专项回归通过但未覆盖新增反例 | deterministic-tests / confirmed | retained | 补回归，不能只靠原测试全绿 |
| E-009 | 官方同类实现区分协议版本、任务状态与来源/生成时间 | upstream-source / confirmed（仅限参考实现） | active | 为方案取舍提供参考，不替代 AF 契约 |

## E-001 · 指定附件案例

- 2026-09-22 23:19，用户在已保存会话中上传个人合同，请求检查内容。助手随后讨论附件中的个人资料及合同条款。
- 主对话成功调用的日志 code identity 为 `38a2811a...`；同窗后台记忆调用出现 `LLMStreamTimeoutError`。这只证明超时类型，不证明供应商具体根因。
- `agent/src/agent/memory_feed.py::_project_utterance` 读取 `content`、文字 `references` 及非 partial 助手文本，不传 `attachments`。上述版本与初次诊断版本的该路径相同。
- 只读重放生产 `project_turn`、`_project_utterance` 与文字引用渲染函数，输入为保存事件字段。带附件与删除附件字段的输出完全相同；投影保留三条发言，没有文件正文和其中的姓名。
- 后续待处理记录的只读邻近上下文仍保留相同三条投影，支持该投影确实存在于运行时，而非仅为源码推测。
- 对该轮首尾 source ref 精确查询 `extraction_commit`，确认在 2026-09-29 02:27 提交；对应 6 条 Semantic、5 条 Episodic、0 条 Core。写入内容对应助手回复中的条款讨论。
- 边界：这能证明附件证据未传入，不能证明任意合同字段都应成为 Core。法定姓名不自动等于期望称呼，文件地址不自动等于当前住处。

## E-002 · 非协议结果误提交

- 入口：`memory/extraction/extractor.py::_parse_output_with_status`、`_parse_entries`，以及生产 Extractor → worker → Reconciler → SQLite commit。
- 固定返回 `{}`、仅无关键的对象，或 `episodic_entries` 内含对象而非字符串时，解析产生 `empty_output`，无 validation/failure 标志。
- 内存库通过真实 worker 提交同一来源两次：假模型只调用 1 次，pending 为 0，extraction commit 为 1。证明该错误形态被当作已处理，并由幂等阻止再次处理。
- 对照：完整的三个合法空数组也产生空成功，但这是需要保留的正常行为。
- 与 136 已确认的“调用失败、解析/校验失败、成功空结果及成功结果分别处理”不一致。
- 局限：生产未保留原始抽取响应，不能据此统计实机发生次数；旧格式兼容消费者需在方案中逐一确认。

## E-003 · 历史版本未随来源失效

- 入口：`memory/store/sqlite_store.py::soft_delete_by_source_events` 的 Semantic/Core 查询仅选 `deleted_at IS NULL AND valid_until IS NULL`。
- 用生产 Reconciler、ContextBuilder 与内存库构造：会话 A 先陈述 Acme，再陈述 Beta；会话 B 更新为 Gamma。随后撤销 A 的最后一条 Beta 来源。
- 结果：Gamma 仍正常可用，但 `search_semantic('Beta', include_historical=True)` 仍返回来源已撤销的 Beta。它已退休，因此未进入来源失效的扫描范围。
- 这是可由“另一会话更新 + 编辑原会话最后一条”触发的路径，不依赖产品支持任意历史编辑。
- 反证/实机限制：按现存会话 rewrite 事件收集 610 个失效事件 ID，对未软删 Semantic 的直接 provenance 做匹配，未发现现存数据命中。该查询只覆盖直接 event provenance，不声称覆盖所有间接来源形态。
- 修复边界：保留审计历史；不擅自新增恢复先前事实的策略。

## E-004 · 观察时间被入库时间替代

- 入口：`memory/extraction/reconciler.py::apply` 中 Core/Semantic 新行 `valid_from=now`，退休边界同样传 `now`；Episodic 使用 fragment 最后一条发言时间。
- 指定附件案例的来源日期为 9 月 22 日，实际提交为 9 月 29 日；新 Semantic 有效期起点取提交时间，Episodic 保留来源时间。
- 内存库探针：固定 9 月 22 日发言，延迟执行 Reconciler；Semantic/Core 起点变为探针执行当天，Episodic 仍为 9 月 22 日。
- 对照查询：对新 Semantic 的精确词，限制有效期至来源日结束，返回 0 条；无该时间限制时事实存在。
- 048 技术方案区分原始发生时间与写入时间，不能把延迟处理本身当成事实后来才成立的证据。
- 未决：片段观察边界、历史事实精确发生时间、跨会话迟到处理不能混为一谈；本期不增加自然语言日期推断协议，不批量重写旧库。

## E-005 · 单条解码失败阻塞队列

- 入口：`memory/extraction/worker.py::_drain` 在队首执行 `decode(payload)`，异常交给 `_run`，后者对全部 pending 设置 `worker_failed` 与冷却，但不改变问题项的可处理状态。
- 构造内存库队首的 unsupported payload version，再提交其他会话三条正常素材，每次将假时钟推进超过冷却时间。
- 结果：正常模型调用数为 0，pending 从 1 增至 4，每次仍遇到同一队首。无需网络即可复现。
- 局限：实际检查时本机 pending 为恢复冷却状态，未发现 unsupported version 或 `worker_failed` 记录。该证据只支持异常数据隔离缺陷，不支持本机此前积压由坏记录引起。
- 边界：连接错误不能当成单条永久坏数据；不能静默删除失败素材，也不能通过无界跳过引发请求放大。

## E-006 · Core 损坏异常分类不一致

- 入口：`memory/core_profile/contracts.py::build_core_profile` 在字段校验前排序，`_entry_sort_key` 对未知字段调用 `tuple.index`；`memory/store/sqlite_store.py::_to_core_profile_entry` 的 JSON/类型/时间解码也可能抛出普通异常。
- 自动装配 `memory/facade.py::_with_core_profile` 只捕获 `CoreProfileError`。
- 内存库先写合法 Core，再只改变 field_key 为未知字段；调用真实 Memory.retrieve，抛出 `ValueError: tuple.index(x): x not in tuple`，未返回 Core unavailable 状态。
- 局限：当前个人 Core 表为空，未发现实际损坏；正常写入校验会阻止多数坏数据。本项是已有存量数据校验与降级契约的补齐。
- 边界：不通过跳过坏行生成半份 Core，不掩盖数据库整体故障、程序错误或用户取消。

## E-007 · 明确保留的行为

- 136 已确认：只由后续活动提供恢复机会，每次最多两段；失败停止批次；恢复禁用模型内部重试；重启、flush、close 不自动排空积压。
- 136 已确认：基础抽取成功、协调失败时提交合法基础结果，记录协调降级，不重抽已提交基础结果。
- 042 已确认：删除会话不联动清理已经抽取的记忆；136 对未处理私有素材补充了删除联动。不能把保留已提交记忆直接列为本期 bug。
- 084 已确认：Core 固定字段、保守迁移、完整装配；普通 Semantic 和 Core 不能凭任意个人信息直接互相替代。
- 145、146 的主干变更已读取，当前需求不重复展开性能及资源治理。

## E-008 · 验证记录

在共享 Python 环境中显式将当前工作区的 memory、llm_providers、runtime_config 源码目录置于模块搜索路径前部；禁用字节码与 pytest cache。固定输出探针只写内存 SQLite。

执行 `pytest.main`，参数为 `-q -p no:cacheprovider`，覆盖：

- `memory/tests/test_extraction.py`
- `memory/tests/test_core_profile.py`
- `memory/tests/test_deferred_extraction.py`
- `memory/tests/test_store.py`
- `memory/tests/test_facade.py`
- `memory/tests/test_search_service.py`

结果：88 passed，5.70 秒。此为专项机制回归，不是完整项目门禁或真实安装版 E2E。

此前尝试直接加载 agent 整包运行投影重放时，共享环境缺少 `dateutil`；未安装依赖或修改产品环境。改为隔离加载原始投影函数、实际 contracts 与文字引用实现完成 E-001 的有界反事实检查。一次测试命令误列不存在的测试文件，未执行测试；修正路径后得到上述有效结果，不计入通过数。

## Artifact 与隐私边界

- 原始运行材料位于本机产品 `sessions_dir()`、`memory_db_path()` 和 `log_dir()`；均按产品路径入口解析，SQLite 使用 URI `mode=ro` 查询。可变数据仍由用户程序使用，不是冻结数据库快照。
- 本索引只存自包含的时间线、统计、代码 SHA、查询方法与合成输入，不复制合同、个人身份、用户会话 ID 或完整抽取素材。
- 离线探针为当次只读调查命令，原始完整输出保留于调查会话，未作为仓库 artifact 固化；不能假称已有可独立运行的回归文件。实现阶段须将五项合成反例落实为正式回归测试。
- 无需凭据或外部模型即可重建 E-002～E-006 的合成反例。

## E-009 · 方案参考与本地消费者核对

2026-10-01 读取 OpenAI 官方 `openai/codex`，固定 revision 为 `d6c3b448a41311ece3255c52ec3dbfd9ff36f154`，不从第三方源码分析文章推定实现：

- [phase1_output.rs](https://github.com/openai/codex/blob/d6c3b448a41311ece3255c52ec3dbfd9ff36f154/codex-rs/memories/write/src/phase1_output.rs)：按 MemoryVersion 显式选择输出结构，使用类型化解码及未知字段拒绝。与 AF 的“任意对象误判为空成功”直接相关，可借鉴协议版本分界；不复制其字段、摘要截断或记忆产品形态。
- [phase1.rs](https://github.com/openai/codex/blob/d6c3b448a41311ece3255c52ec3dbfd9ff36f154/codex-rs/memories/write/src/phase1.rs)：分别统计成功有输出、成功无输出和失败。该区分适合核对 AF 的提交状态，不要求 AF 重建同样的异步任务体系。
- [state/runtime/memories.rs](https://github.com/openai/codex/blob/d6c3b448a41311ece3255c52ec3dbfd9ff36f154/codex-rs/state/src/runtime/memories.rs)：记录来源更新时间及生成时间；来源水位约束覆盖更新，任务选择区分运行、退避和重试耗尽。支持把来源新鲜度与处理时间分开考虑，但不能直接用全局线程水位代替 AF 的字段和事实级有效期。

AF 本地消费者核对：

- `factory.py` 默认使用当前 prompt，`extraction_keep_specifics=False` 的受控切片读取旧 prompt。
- `memory_eval/harness/factorial.py`、`paired_extraction.py` 及历史 cassette 测试直接构造 Extractor，并传入不同 prompt。不能仅在生产 parser 中删掉全部兼容分支后声称无下游影响。
- `extraction_pending.reason` 同时承担就绪/延后原因，`defer_extractions` 会批量重写该字段。若借它保存永久失败标记，会被后续冷却覆盖；方案需要把调度资格与原因明确区分。
- 现有 Reconciler 把有效期结束时间与 `updated_at` 绑定为同一值；时间修复需拆开语义，不能只改新行的 `valid_from`。

以上是方案确认前的参考与现状记录；用户随后确认的实现边界见 design.md。

## 方案前问题及当前去向

1. 当前生产协议默认严格；历史 prompt/cassette 消费者显式启用兼容。
2. 确定性素材异常保留 payload，以 blocked、error_code、blocked_at 区分调度资格；不新增恢复 UI 或自动修复。
3. 有效期使用片段最大观察时间，审计使用写入时间；已明确的迟到冲突整批回滚并隔离。
4. Core 已知数据异常归一为 CoreProfileError，基础设施异常继续上报；完整 Profile 失败时普通召回仍可用。
5. 以上实现已覆盖确定性回归；模型实际抽取质量、安装态与 PDF 接入未由本轮离线验证覆盖。

## E-010 · 实施与回归验证

- 工作树：`feature/147-memory-correctness-bugfixes`，基线 `71cc9094a89d96408aa24be314698809c885eab0`，当前未提交 overlay。新增 [正确性测试](../../../memory/tests/test_memory_correctness.py) 固化 38 个参数化用例，全部使用 fake 与隔离 SQLite。
- 首轮先补测试：12 failed / 2 passed，其中来源失效测试首次有调用参数错误，修正该 fixture 后才作为产品断言；不能将此 TypeError 算作产品复现。其他失败分别落在 E-002、E-004、E-005、E-006 的预期断言。
- 修复后初轮 `scripts/test/run.ps1 memory/tests memory_eval/tests -q --tb=short`：263 passed / 2 skipped。后续补充时间冲突隔离、持久化迁移、普通召回降级、失效 dense 命中过滤、数值类型、身份一致性与服务退避组合边界。
- 最终专项 `scripts/check/run.ps1 --stage backend memory/tests memory_eval/tests`：lint、mypy（773 files）通过；287 passed / 2 skipped。两项 skip 分别需要显式的 origin/main 对照工作树、本地 ONNX 模型资产，不代表真实模型验证。
- 特别保护：时间冲突的 blocked 状态不能绕过同轮第二阶段服务失败的 Retry-After；已补组合回归。畸形时间值不会经异常链正文进入普通日志。
- `scripts/check/run.ps1 --stage frontend`：lint/typecheck、162 files / 1352 tests、production build 通过。构建仍有现存 CSS highlight 与大 chunk 提示，没有前端改动。
- `scripts/check/run.ps1 --stage rust`：cargo fmt、cargo test、cargo check --release 通过。Cargo target 由现有 `scripts.package.paths` 解析至工作树外 build cache；没有构建安装包或启动桌面运行态。现存 dead_code warning 保留。
- 全量 `scripts/check/run.ps1 --stage backend` 已通过：3785 passed / 5 skipped / 8 warnings，进程退出码 0；最后的限定改动（数值/身份校验、隐私错误信息、隔离与退避组合分支）已经以上专项复验，不把较早启动的全量 pytest 说成覆盖了随后新增的用例。
- 限制：未请求真实 LLM、未回填/改写个人记忆、未执行安装态或桌面视觉验收；此轮不能宣称合同信息已经补齐。

## 对账记录

| 日期 | 结果 |
| --- | --- |
| 2026-10-01 | 用户确认五项范围及 147 编号；从 `71cc9094...` 创建独立 feature 分支并远程占号。落盘需求与证据，未实施产品修复。 |
| 2026-10-01 | 方案前置核对：明确旧输出协议消费者、冷却覆盖 reason 的约束及有效期/审计时间耦合；核验官方同类源码，等待技术方案确认。 |

| 2026-10-01 | 用户确认方案，五项代码修复与 38 项新回归落地；专项、frontend、Rust 门禁通过，全量 backend 3785 passed / 5 skipped，最终改动专项 287 passed / 2 skipped；用户验收待完成。 |

全量运行额外打印第三方 LiteLLM coroutine 与 Bilibili atexit 清理 warning；没有失败测试，门禁返回成功。原始终端输出为本轮验证记录，未把依赖警告伪装为产品修复，也未声称所有测试无警告。

## E-011 · 记忆响应等待增补

- 用户确认两阶段非流式记忆响应等待改为 600 秒，保持默认连接 15 秒、写入 90 秒、空闲 90 秒及既有重试控制；普通调用不变。
- `memory/tests/test_extraction.py` 捕获基础抽取与 Semantic 协调的 ModelRequest，覆盖 allow_retries 两种状态并对照全局默认；结合 `llm_providers/tests/test_timeout_policy.py` 共 52 passed。不以 fake 验证声称真实服务一定在 10 分钟内返回。
- 用户已确认既有修复边界并授权合入、推送与本机打包；仍不包含历史资料补齐或自动安装。

- E-011 最终全量后端门禁覆盖全部最终源码：3796 passed / 5 skipped / 8 warnings，lint 与 mypy（773 files）通过。frontend、Rust 代码未变化，沿用 E-010 的通过证据。

## E-012 · 交付与制品核验

- 完整交付绑定、Guide r72、安装包 build ID、SHA-256 及范围见 [交付结果](progress.md#交付结果)。
- 最终源码后端 3796 passed / 5 skipped；前端 1352 tests 与 Rust gate 沿用相同代码输入的通过记录。
- standard 完整打包成功，冻结 doctor 全部通过；安装器实算 SHA-256 与两份清单匹配，临时 Guide overlay 恢复后工作区 clean。没有执行真实安装或线上模型行为验收。
