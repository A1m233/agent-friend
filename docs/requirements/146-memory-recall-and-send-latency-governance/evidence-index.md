# 146 证据索引

## 范围与当前判断

服务于 [需求](requirement.md) 的发送前延迟盘点；2026-09-30，产品源码锚点 `ab9a04ce2971c78b3a45a3d12045d693b8638003`。本轮只写文档并进行离线只读探针，没有改产品代码、调用模型、操作已运行的 AF 或改写原始会话。技术方案尚未确认。

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

- E-001 的约 9.2 秒需由完整离线发送回放或新增分段观测收敛，不能用 E-004 或缓存复现代替。新观测代码仍属于待确认技术方案。
- E-002/E-003 支持评估独立轻量对话投影与可重建持久索引；尚未确定格式、生命周期、字段或算法，更没有实测候选收益。
- E-005/E-006 要求比较 scope decisions、evidence identity、来源、顺序、过滤和取消结果；仅比较最终文本或 Top-K 数量不足。
- E-007/E-008 作为设计参考；不能从参考来源反推本项目应直接引入 FTS、BM25 或改变召回质量策略。
- 热发送、进程冷启动、索引首次构建及源文件替换须分别测量；正式量化目标尚未确定，不以隔离查询的约 5 秒直接充当完整发送基线。

## Artifact 与复查

- 原始 A/B 固定副本仍位于本机私有研究目录，位置和内容哈希由 [145 baseline manifest](../145-session-memory-and-storage-governance/evidence/20260930-baseline-manifest.json) 管理。没有把聊天正文复制进本需求。
- 本轮一次性探针在 `%LOCALAPPDATA%/agent-friend-research/146/20260930-projection-probe/probe.py`，结果在同目录 `result.json`；脚本 SHA-256 为 `d9676666b6a9a433f7794ade7b48705453c974b5aa342e6bab0f5f9352deabb7`。运行参数为产品根和 145 固定副本目录。正式可重复测量应在确认方案后进入双平台 scripts 入口。
- 已将不含正文的数值结果归档到本需求 evidence；网络连接在探针进程中被禁用。数据文件查询前后 size/mtime/inode 一致，未做内容改写；锁文件沿用读取协议，不将该检查表述为全目录零写入。
- 三次边界探针共享同一查询快照、运行顺序为 B 后 A，未控制 OS 文件缓存；不能对样本外、其他平台或安装版作统计外推。

## 对账记录

- 2026-09-30：需求已确认并建立远端 146 分支；本索引仅记录当前证据，design 尚未创建，产品实现尚未开始。
