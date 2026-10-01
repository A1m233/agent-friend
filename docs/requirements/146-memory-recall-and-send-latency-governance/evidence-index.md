# 146 证据索引

## 范围与当前判断

服务于 [需求](requirement.md) 的发送前延迟盘点；2026-09-30 首轮只读盘点的源码锚点为 `ab9a04ce2971c78b3a45a3d12045d693b8638003`，当时技术方案尚未确认。

2026-10-01：用户已确认方案方向并授权 M0 观测与离线基线；计时代码和离线入口已实现，产品性能策略未改。新增 E-009～E-012 记录真实发送路径的离线测量。未调用外部模型、操作已运行的 AF 或改写原始会话。

同日后续：用户确认 M1 具体方案并授权实现。轻量 SQLite 召回投影、增量维护及 UUID 当前轮边界已接入；最终离线候选见 E-020，确定性边界见 E-021。首轮候选 E-018 保留，取消故障 E-019 已修复。原始基线及旧安装版证据保持独立。

已确认查询缓存存在长会话互相淘汰及重复解析原始日志的路径；本轮样本支持召回所需内容远小于通用业务快照。不能据此宣布完整发送已优化，或认为全部等待都来自同一处。原始日志中召回前约 9.2 秒仍未归因。

## 证据账本

| ID | 问题 / 主张 | 类型、锚点与观察 | 置信度 / 状态 / 局限 |
| --- | --- | --- | --- |
| E-001 | 旧安装版热会话等待主要发生在本地准备阶段 | runtime-supported；[145 诊断](../145-session-memory-and-storage-governance/evidence/20260930-send-latency-diagnosis.json)：本地 request_started 28.218 s，召回 18.297 s；其中结构化来源 47 ms | confirmed / active；单次日志，不是服务器收包或 UI 点击；模块身份绑定的局限保留在原记录 |
| E-002 | 当前查询缓存可互相淘汰 | reproduction-supported；同一 145 诊断的 B,B,A,B,A 序列：A/B 估算合计 67909147 bytes 超过 67108864 bytes 上限；跨会话访问后再次扫描 | confirmed / active；一次隔离序列，不是完整发送分布 |
| E-003 | 召回内容比通用查询及原始文件小很多 | reproduction-supported；[投影探针](evidence/20260930-projection-probe.json)：B 有效对话正文 503953 bytes、A 208698 bytes，原始文件分别 933172807/1112568824 bytes | confirmed / active；仅 UTF-8 正文大小，不包含元数据、Python 堆或数据库开销；不等于预计索引大小 |
| E-004 | 压缩边界重建不能仅凭静态代码认定为 9 秒的根因 | reproduction-supported；同一探针对活动业务事件进行消息投影及 business checkpoint 解析，B 三次合计约 42–53 ms，A 约 30–36 ms | confirmed / active，仅证明该隔离输入上的耗时；当前源码与旧安装版不同，不能证伪旧版本或其他准备步骤的成本 |
| E-005 | 召回范围与历史来源依赖通用快照 | code-supported；`SessionStoreSourceOrderProvider.active_conversation_event_uuids_cancellable` 与 `SessionHistorySource._rank` 调用 `query_session`；`store._query_snapshot` miss 后进入 `iter_events`，先 decode 再过滤 rollout | confirmed / active；静态调用关系不代表每次都会 miss；没有召回 cutoff 时 scope preparation 可跳过顺序查询 |
| E-006 | 历史召回已有必须保持的检索语义 | code-supported；`session_history_source.py` 的 `_recall_content`、`_conversation_turns`、`_match_quality`、`_select_with_session_coverage`；`memory/search/service.py` 的 scope 过滤及 source 扩容 | confirmed / active；子串/casefold、整轮证据、短词、过滤、跨会话覆盖不能被新排名方式静默改变 |
| E-007 | Codex 分离原始历史与查询元数据 | external-source；官方 `openai/codex` revision `b1e72963c3b71a9265a551e54beff078384efed9` 的 [thread-store README](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/codex-rs/thread-store/README.md) 与 [LiveThread](https://github.com/openai/codex/blob/b1e72963c3b71a9265a551e54beff078384efed9/codex-rs/thread-store/src/live_thread.rs) | confirmed / active；仅支持持久化边界借鉴，不代表 Codex 有 AF 相同的跨会话召回或证明其性能 |
| E-008 | FTS 不是当前子串语义的无损替换 | external-source；[SQLite FTS5 官方文档](https://www.sqlite.org/fts5.html)，§4.3.4 trigram 短查询限制、§4.4.4 外部内容一致性、§6.12 rebuild；2026-09-30 查阅 | confirmed / active；Context7 已查但未命中相应内容，改读官方文档；只说明语义和维护风险，未做本项目 FTS 实验 |
| E-009 | 重复原始文件扫描出现在真实发送调用链内 | reproduction-supported；[热发送](evidence/20261001-hot-alternating-02.json)，B,B,A,B,A,B 六轮，每轮 1–2 次查询重建，读取约 0.93–2.05 GB，发送至 fake 请求 6.737–14.220 s | confirmed / active；无结构化 DB，使用历史召回的隔离场景；不代表自然请求平均耗时或安装版 E2E |
| E-010 | 工具事务恢复是另一项可见准备成本 | reproduction-supported；同一报告 `tool_transaction_repair` 611.878–1384.505 ms，真正的 `history_reconcile` 5.896–10.741 ms；`Conversation._repair_incomplete_model_tool_transactions` 会对历史 interrupted run 再调用 `_append_turn_aborted_history_fact` | confirmed / active：阶段耗时已证；究竟哪些历史重处理可以安全复用，仍需细化证明，不能直接跳过恢复 |
| E-011 | 冷恢复和逐次发送是分开的成本 | reproduction-supported；[运行态重建](evidence/20261001-reload-01.json) 两轮：加载 12.972/13.656 s，加载之后发送至 fake 请求仍需 13.161/13.587 s | confirmed / active；仅重建 runtime/store，不清 OS cache；第二轮副本已经包含前一轮正常写入 |
| E-012 | 新计时基本覆盖当前离线请求前路径 | reproduction-supported；[摘要](evidence/20261001-baseline-summary.json)：热发送未分配时间 32.326–42.702 ms，重建场景 26.961/30.657 ms；[小样本 CLI](evidence/20261001-synthetic-cleanup-final.json) 连续两轮成功 | confirmed / active；按顶层 phase 加 context_build 的非重叠口径，accepted_rollout 已包含在 input_persistence；不能外推旧安装版 9.192 s 已归因 |
| E-013 | 原始写入与派生维护已有明确失败边界 | code-supported；`62a4ee259cd727990e5d93106a2ffedb1f502b14` 的 `JsonlSessionStore._append_events` 在 fsync 后释放文件锁，再调用 `_advance_read_caches`；派生维护异常丢弃缓存并记录，不使持久化成功变失败 | confirmed / active；新投影必须沿用该边界，不能把跨 JSONL/SQLite 的两次写入视为一个原子事务 |
| E-014 | 编辑与归档属于不同的有效性边界 | code-supported；同一 revision 的 `Session.active_events` 对所有 `turn_rewrite.inactive_event_uuids` 求并集；`SessionManager.archive` 只改管理 metadata，`JsonlSessionStore.list_activity` 不按归档过滤 | confirmed / active；保持现状意味着编辑后的失效内容不可召回，而不能顺手把归档改成“不可检索”；删除仍以原始文件存在性为准 |
| E-015 | 重建不能阻止原始追加前进，且查询可返回固定版本快照 | code-supported；同一 revision 的 `iter_events` 固定 descriptor/长度后在读锁外解析，`_query_snapshot` 拒绝把过期构建发布为当前缓存；`test_builders_are_per_session_and_append_can_progress_during_parse` 等既有测试固定并发边界 | confirmed / active；这些是既有契约，本轮未重跑；派生索引不能用长期全局写锁覆盖完整 JSONL 扫描 |
| E-016 | SQLite 事务可保证其内部数据与来源版本一起提交 | external-source；2026-10-01 查阅 [SQLite Atomic Commit](https://www.sqlite.org/atomiccommit.html) §1、§3、§4：单事务变化全部提交或回滚，支持故障恢复 | confirmed / active；仅支持同一数据库事务边界，不覆盖外部 JSONL，也不能把事务成功当作源文件仍未变化的证明 |
| E-017 | 当前轮边界接入需要覆盖精简执行态与原始位置的对应关系 | code-supported；同一 revision 的 `Conversation` 从 `len(self._session.events)` 形成工具当前轮边界，`load_execution_session` 不驻留 rollout 事件；`SessionHistorySource._current_turn_uuids` 使用 query 的原始事件位置比较 | unconfirmed / active；存在位置口径不一致风险，尚未完成真实调用回放；不能直接宣称已发生召回错误，也不能让轻量投影重新编号后跳过该验证 |
| E-018 | 轻量持久投影可消除重复热发送的原始文件读取 | reproduction-supported；[首轮候选对比](evidence/20261001-candidate-summary.json)、[六轮发送](evidence/20261001-candidate-hot-alternating-01.json)、[两轮 runtime 重建](evidence/20261001-candidate-reload-01.json)：投影建立后的六次发送均为零原始 load、零重建，查询命中；对应阶段召回约 67–86 ms | confirmed / retained；`62a4ee2 + M1 overlay`，每份报告绑定源码/脚本哈希；后续取消故障修复尚未包含，不能冒充最终候选。首次构建仍完整扫描，首次 reload 样本发送 14.057 s 高于旧样本 13.161 s，冷成本没有消除 |
| E-019 | 取消回调异常不能被派生存储降级吞掉 | deterministic reproduction；`test_one_shot_io_shaped_cancellation_is_not_treated_as_storage_failure` 首次失败：一次性 `InterruptedError` 被当作存储故障处理后继续查询；query_recall 增加取消异常来源跟踪，向上传播原异常 | confirmed / active；这是候选实现中的边界错误，非旧安装版根因；修复后需回归并重测最终候选，保留首轮有效热路径观察 |
| E-020 | 最终候选在热发送及重建 runtime 后均复用持久投影 | reproduction-supported；[最终对比](evidence/20261001-candidate-final-summary.json)、[六轮发送](evidence/20261001-candidate-hot-alternating-02.json)、[两轮 runtime 重建](evidence/20261001-candidate-reload-02.json)：首建之后共六次发送，raw load/rebuild 均为 0，每次三次 recall query 均命中；source failure 均为 0，每轮 fake 调用一次 | confirmed / active；supersedes E-018 的最终候选身份，不否定其观察。运行身份为 `62a4ee2 + final runtime overlay`，报告保存源码和 harness SHA；与基线 fixture、序列及配置逐项一致，测量时未并发执行后端检查 |
| E-021 | 派生维护与召回语义具有确定性回归覆盖 | deterministic tests；`agent/tests/test_session_recall.py` 28 项通过，覆盖与业务投影的非空候选等价、短词/casefold、persona/时间、重写/删除/归档、外部变化、模式/表/行/文件损坏、持久化失败、取消原异常、并发写入、独立进程复用和 UUID 边界；工具集成测试固定隐藏参数不进入持久化工具参数 | confirmed / active；E-019 修复已验证。E-017 的位置风险由带 rollout 的用例固定并改用 UUID；不据此宣称旧安装版已发生同一错误。fake 证明机制，不证明真实模型行为；Windows 本机验证，未做 mac/linux 执行 |

## M1 最终候选与成本（2026-10-01）

| 场景 | 基线发送至 fake 请求 | 候选发送至 fake 请求 | 行为证据 |
| --- | --- | --- | --- |
| 首次构建（热序列首轮 B） | 14.220 s | 13.531 s | 两条原始文件仍读取约 2.05 GB，建立派生库；不是已完成构建后的热发送 |
| 构建后的五轮 B/A 交替与重复 | 6.737–13.793 s | 0.884–2.023 s | 原始 load 0 次、投影重建 0 次；召回 70.687–83.587 ms |
| runtime/store 重建后的第二轮 B | 13.587 s | 2.722 s | 原始 load 0 次；召回 89.589 ms；执行态加载仍单列为 13.166 s |

两条固定会话派生库合计 2437120 bytes（约 2.32 MiB）；后续五轮总大小未增加。候选热序列进程 private memory 约 399–474 MiB，加载 A 后约 474 MiB；这是整个隔离 Python 进程，不是全产品占用，也不是闲置会话预算。工具事务恢复仍占约 0.45–1.37 s，本轮未修改其策略。

数值只描述这些样本，不设中位数/p95 门槛，也不据此承诺安装版发送秒数。首次构建和原始执行态冷恢复仍有成本；无真实结构化库、Skills、附件、UI 或厂商 SDK，旧安装版约 9.2 秒间隔仍保留缺口。候选测量脚本新增的派生文件计数发生在轮次结束后，源码摘要采集发生在测量结束后；发送输入、计时边界和配置与 M0 保持一致。

最终后端检查 3747 passed / 5 skipped，lint/format/mypy 通过；固定 fixture 日期并要求非空候选后的定向 28 项通过，防止测试随日期变化失去覆盖。详见 [M1 验证清单](evidence/20261001-candidate-validation.json)。前端在 M0 后无变化，沿用此前验证；未执行本需求 Rust gate、真实安装版 E2E 或用户验收。

## M0 基线与验证（2026-10-01）

| 场景 | 加载执行态 | 发送至首次 fake 请求 | 主要成本 |
| --- | --- | --- | --- |
| B 连续发送第 1/2 次 | 首次 14.085 s / 后续 27.615 ms | 14.220 / 13.793 s | 召回 12.094 / 12.114 s；每轮两次大文件扫描 |
| 后续 A/B 交替四轮 | A 首次 13.086 s，已加载后约 37–42 ms | 6.737 / 8.104 / 6.927 / 7.221 s | 每轮仍扫描另一大会话；召回 5.716–6.349 s |
| 重建 runtime/store 两轮 | 12.972 / 13.656 s | 13.161 / 13.587 s | 每轮两次大文件扫描；不是启动慢的重复计入 |
| 合成小会话两轮 | 16.195 / 6.179 ms | 542 / 65 ms | 首轮还包含约 0.47 s 分词初始化；不作为长会话性能样本 |

长会话热场景进程 private memory 为约 521–624 MiB；这是整个隔离 Python 进程及两个活跃会话的描述值，不是产品全进程内存、Idle 预算或候选优化收益。每轮 fake provider 恰好调用一次，召回 source failure 均为零。样本仅用于复现和分阶段描述，不给出有统计可靠性的 p95。

测量使用 `b3e25d2ed3bd07bf272d8f13abe5caabe2f98882 + M0 overlay`，不是在未来提交上运行。报告保存当时 tracked overlay 摘要、fixture SHA、脚本 SHA 和限制；最终业务源文件已逐项核对与热基线摘要相同。基线脚本保存在私有 `baseline-harness.py`（SHA 见摘要）；之后仅调整测量区间外的 Windows 事件循环初始化/退出清理，`measure` 与 `copy_fixture` 的 AST 已核对一致。

最早 `hot-alternating-01.json` 使用较宽 DEBUG 日志范围，并将工具恢复包含在 `history_reconcile` 中，保留为 pilot，不合并进最终基线分布。最初未给 PowerShell 逗号序列加引号的命令在参数解析时失败，未进入测量。早期离线 CLI 退出出现 ProactorEventLoop 清理警告；修正本地控制管道初始化与 SDK 清理顺序后，小样本 CLI 退出码 0 且无该警告。未修改第三方包。

检查及源码校验见 [validation](evidence/20261001-validation.json)。后端 3719 passed / 5 skipped，前端 1342 tests、lint/typecheck/build 通过；最终脚本清理修订另经定向测试。Rust 无输入变化，未重复该阶段。没有真实桌面视觉、真实上游或真实结构化记忆数据库验证。

## 当前调用链盘点

| 边界 | 当前 owner / 入口 | 已有观测 / 缺口 |
| --- | --- | --- |
| 前端发送与传输 | `frontend/src/services/stream.ts`；`agent_bridge/protocols/ag_ui/routes.py` | 发送前等待 ensureLocalServiceReady；前端单调时钟与后端时钟不能直接相减；需区分客户端等待和后端接收 |
| 排队与执行 | `agent/runtime/scheduler.py::_run_one`；`runtime/__init__.py::_execute_request_guarded` | 已有 pre_submit/queue、conversation_prepare、conversation_bound；需沿同一 run 关联 |
| 召回前准备 | `Conversation._stream_user_turn` | reconcile、历史 snapshot、summary/boundary 推导、Skill、tool evidence、user append 与 accepted rollout commit 缺少完整分段 |
| 当前来源顺序 | `SessionStoreSourceOrderProvider` → `query_session` | 通用快照含非对话业务事件；无 cutoff 时当前顺序不必准备 |
| 结构化及历史检索 | `MemorySearchService.search` → 各 source；`SessionHistorySource._rank` | 已有 source duration，但日志需关联 run；历史来源会遍历符合条件的会话，limit 不能限制完整候选构建成本 |
| 快照重建及追加维护 | `JsonlSessionStore._query_snapshot` / `_advance_read_caches`；`SessionQuerySnapshot.appended` | 已有 query/file observation；追加后虽不重读全文件，仍可能复制历史容器；不能把“增量”当作成本恒定 |
| 装配及 provider 边界 | `Conversation._assemble` 相关上下文计时；`llm_providers/client.py` 与 `responses/stream.py` 的 request_started | 本地 transport milestone 不是供应商确认收包；需要分开 provider 投影、序列化和网络等待 |

## 未决问题与对方案的影响

- E-001 的约 9.2 秒不能用 E-004 或缓存复现代替。M0 已补齐当前离线路径观测，但旧安装版身份、配置及未覆盖入口仍使该历史间隔保持未确认。
- E-002/E-003 支持评估独立轻量对话投影与可重建持久索引；尚未确定格式、生命周期、字段或算法，更没有实测候选收益。
- E-005/E-006 要求比较 scope decisions、evidence identity、来源、顺序、过滤和取消结果；仅比较最终文本或 Top-K 数量不足。
- E-007/E-008 作为设计参考；不能从参考来源反推本项目应直接引入 FTS、BM25 或改变召回质量策略。
- 热发送、进程冷启动、索引首次构建及源文件替换须分别测量。用户已确认不设置固定数值门槛；以避免重复完整扫描、正确复用/更新及失效恢复作为行为目标，数值用于评估回退与取舍，不以隔离查询的约 5 秒直接充当完整发送基线。
- E-013～E-016 补齐性能方案的持久化、归档和并发约束；E-017 要求接入前明确当前轮边界的位置口径。下一项有区分力的验证是带 rollout 事件、精简执行态和显式历史工具的确定性回放，而不是继续追加耗时样本。

## Artifact 与复查

- 原始 A/B 固定副本仍位于本机私有研究目录，位置和内容哈希由 [145 baseline manifest](../145-session-memory-and-storage-governance/evidence/20260930-baseline-manifest.json) 管理。没有把聊天正文复制进本需求。
- 本轮一次性探针在 `%LOCALAPPDATA%/agent-friend-research/146/20260930-projection-probe/probe.py`，结果在同目录 `result.json`；脚本 SHA-256 为 `d9676666b6a9a433f7794ade7b48705453c974b5aa342e6bab0f5f9352deabb7`。运行参数为产品根和 145 固定副本目录。正式可重复测量应在确认方案后进入双平台 scripts 入口。
- 已将不含正文的数值结果归档到本需求 evidence；网络连接在探针进程中被禁用。数据文件查询前后 size/mtime/inode 一致，未做内容改写；锁文件沿用读取协议，不将该检查表述为全目录零写入。
- 三次边界探针共享同一查询快照、运行顺序为 B 后 A，未控制 OS 文件缓存；不能对样本外、其他平台或安装版作统计外推。

## 对账记录

- 2026-09-30：需求已确认并建立远端 146 分支；本索引仅记录当前证据，design 尚未创建，产品实现尚未开始。
- 2026-10-01：M0 方案及观测实现完成，新增发送基线与验证索引；后续性能方案、量化目标和实施仍待确认。既有 E-001 旧安装版未归因间隔保持 unconfirmed。
- 2026-10-01（基线讨论后）：用户否决固定中位数 ≤2 秒、p95 ≤3 秒的提案，确认按行为作为目标，避免为数值达标破坏架构平衡。需求 AC-03 和方案推进依据已同步；数值证据保留，后续性能细化及实现仍未完成。
- 2026-10-01（性能方案细化）：补充 E-013～E-017；已向用户提交轻量召回投影、原始持久化优先和版本失效恢复的具体方案，尚待本轮方案及实现确认。工具事务恢复先保留既有行为，不以其耗时继续扩大本轮改动。
- 2026-10-01（M1 实现）：用户确认落盘并实现；已接入轻量投影和宿主 UUID 边界。首轮测量报告追加归档；候选故障注入发现取消与 I/O 异常分类重叠，修复后进行复验。未调用真实模型或改变既有 AF 运行态。
- 2026-10-01（M1 复验）：E-019 取消回归通过；追加 E-020 最终候选复测及 E-021 边界验证。早期完整检查的格式失败和修复前候选记录均保留，不将其混入最终结果。
