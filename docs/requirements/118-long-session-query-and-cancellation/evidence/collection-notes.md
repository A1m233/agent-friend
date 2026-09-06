# 既有观测的归档口径

本记录只整理需求 118 确认前已经完成的调查、离线测量和参考阅读。本次归档没有访问生产 API、日志、JSONL 或数据库，没有复跑实验，也没有调用外部 LLM。

## 来源、版本与访问范围

三份报告均自报 profiling 源码 HEAD 为 `fba5ea4f71805b6fd58825e7086399abb053ba5d`。需求落盘时的基线为 `f563938bd3f63c88a5e7a5b59825e3da33f06303`；后者只作为当前代码检查锚点，不能改写历史运行身份。

原始目录定位为 `%USERPROFILE%/.codex/worktrees/0bcf/agent-friend/.tmp/cancel-recall-analysis/`，**仅原采集机器可访问**。其中的报告与 probe 在归档时仍存在，但临时 session / SQLite 数据副本已经删除。目录随旧 worktree 清理后可能消失；本仓库的聚合文件不依赖该目录才能理解结论，也不承诺还能完整重跑。

| 原文件 | 归档内容 | 保留边界 |
|---|---|---|
| `baseline-report.json` | [recall-profile-aggregates.json](recall-profile-aggregates.json) 的 E-002 | 白名单聚合；原文件 SHA256 与字节数保留 |
| `incident-prefix-baseline-report.json` | 同上，E-003 | 白名单聚合；补充前缀与反扫边界 |
| `exclude-ineligible-current-report.json` | 同上，E-004 | 白名单聚合；保留最终排序 ID 哈希 |
| `profile_recall.py` | 同上，`retained_harness` 与本记录 | 只保留脚本 SHA256、字节数和本机定位，不复制为可运行脚本 |
| 原运行日志 / ring buffer | [prior-runtime-observations.json](prior-runtime-observations.json) | 来自已完成调查的脱敏摘要；完整原日志没有归档，线上耗时不能从三份 profile 报告重新算出 |

三份 report 含真实 user / persona / session / event 标识、源码个人目录等字段；probe 硬编码真实 session 标识，并具备访问本机实际服务、生产 session 与数据库的能力。本归档不复制这些字段，也不复制原始正文、query、事件列表、数据库、PID、索引 generation 标识。这里的 JSON 是 **derived aggregate，不是原 artifact 的完整副本**。原文件 SHA256 只能核对仍可访问的原文件，不能恢复已删输入。

当前留存 probe 的文件时间晚于前两份报告；前两次实际执行的脚本字节没有单独锁定。不能把当前 probe SHA256 追认成所有历史运行的 harness 身份。离线 source HEAD 也不能单独证明事故时长驻进程已经加载同一源码。

## 已有测量的输入与采集方式

- 每个 variant 只有一次记录，按 current-snapshot baseline、排除当前会话、incident-prefix baseline 的顺序完成。
- 三份报告都从事后数据复制 74 个 session，总计 344,466,289 字节，其中当前大会话为 335,264,600 字节。复制前后检查文件大小与 mtime；这不等于完整内容哈希或跨 session / SQLite 的原子时间点快照。
- query 通过唯一 user-message event 绑定，长度为 25；query 和标识未进入归档。`top_k=8`，自动召回 scope 没有当前会话的合格边界，因此当前会话在该输入下没有可注入的历史内容。
- SQLite 使用只读源连接、`query_only` 和 backup API 复制；检索子进程使用隔离副本、固定查询时钟、本地量化 `bge-small-zh-v1.5` ONNX embedding，并先做一次 warm-up。留存脚本禁止子进程 socket 连接，用 NoLLM 阻断外部 LLM，并断言后台 memory / index worker 未启动。
- 上述隔离只属于 profiling 子进程。留存 probe 的 supervisor 仍具有本机真实服务与生产数据读取能力，不是可以直接交给后来者运行的通用离线脚本。
- 计时使用 wall time 与 process CPU time；函数 wrapper、周期性 checkpoint 写入存在测量开销。phase 多为包含子调用的累计时间，不能把所有行相加；多线程 CPU time 也可能高于 wall time。
- 所有快照删除状态在原报告中均为 true；本次归档没有恢复、保留或再次读取这些原始数据。

`incident-prefix` 只把当前会话的隔离副本截到 335,263,838 字节、37,435 行的完整换行边界，去掉事后追加的 `run_terminal` 500 字节和 `model_config_change` 262 字节。其余 session 和 SQLite 状态没有回退到事故时刻。报告里的 `snapshot.bytes` / `target_bytes` 是截断前的值；截断后的值在 `incident_prefix` 中。因此它修正了 current-snapshot 的尾部缺口，但不是完全忠实的事故快照。

排除当前会话的 variant 使用 probe 内部的 store subclass，在生成 summary 前跳过这一个无资格会话；这是反事实测量路径，不是已经选择或合入的生产实现。它与 incident-prefix variant 的快照尾部也不同，不能把二者的耗时差当成仅改变一个变量的严格因果估计。

## 分阶段结果与质量边界

| 指标 | E-002 事后快照 | E-003 事故前缀 | E-004 排除无资格当前会话 |
|---|---:|---:|---:|
| retrieve wall，秒 | 7.5392225 | 11.5544693 | 0.9358425 |
| session history search，秒 | 7.0766485 | 11.0452791 | 0.5034220 |
| store list，秒 / 次数 | 0.5459110 / 2 | 3.3444595 / 2 | 0.4057733 / 1 |
| store load，秒 / 次数 | 5.9775078 / 148 | 7.0830969 / 148 | 0.0845582 / 73 |
| structured search，秒 / 次数 | 0.0296410 / 2 | 0.0312290 / 2 | 0.0290846 / 2 |
| load 累计读取字节，含重复读 | 688,932,578 | 688,931,054 | 9,201,689 |
| 最终 context 项数 | 12 | 12 | 12 |

E-001 原运行 memory total 为 136.628 秒；同一进程前一次观测为 1.683 秒，但前次 history 被跳过，不能作为同路径重复样本。E-003 复现了原 trace 的 source 计数，耗时仍相差约 125 秒。锁等待、GC、竞争、进程积累、环境等没有被这组记录区分，不能选其中一种写成已确认根因。

原 trace 与 E-002 / E-003 的 structured source 计数为 raw 126、suppressed 89、最终 source candidates 24、扫描 2 次；history 为 raw 18、suppressed 2、最终 8、扫描 2 次。E-004 的 structured 计数不变，history 为 raw 9、suppressed 0、最终 8、扫描 1 次。raw、suppressed 和最终截断后的 candidate_count 不是简单守恒关系。

只有 E-003 与 E-004 保存了最终 injected evidence ID 的排序哈希，两者都是 12 项，SHA256 为 `d97b7b725a3f8fa38476e73637f1bd179bed5a4cc1c72526fb797dcfa6286807`。算法是对排序后的 ID 列表按 `json.dumps(..., ensure_ascii=False)` 序列化，再对 UTF-8 字节计算 SHA256。E-002 没有该字段。这个 guardrail 仅支持这两份记录的最终 ID 集合一致；没有校验正文、最终呈现顺序或语义质量。

## 既有代码发现的定点核对

下列链接固定到当前需求基线 `f563938`，只支持代码机制，不替代事故运行时证据。

| 证据 | 代码支持的最小发现 | 入口 |
|---|---|---|
| E-005 | history source 先列出 summary、逐会话 load，再按内容生成候选；scope 在 source 返回后评估，不足时扩大 limit 重跑。读取前资格排除在该路径尚未实现。 | [session_history_source.py:31](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/agent/src/agent/memory_search/session_history_source.py#L31)、[service.py:233](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/memory/src/memory/search/service.py#L233) |
| E-006 | list 的 summary 读取耦合最新 model config 的反向扫描；长尾距离会放大列表成本。E-003 中末次配置 offset 为 196,792,202，至前缀末尾跨度为 138,471,636 字节；该反扫每次经过 26,098 行。 | [store.py:464](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/agent/src/agent/sessions/store.py#L464)、[store.py:519](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/agent/src/agent/sessions/store.py#L519)、[store.py:587](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/agent/src/agent/sessions/store.py#L587) |
| E-007 | AG-UI 在 runtime.submit 前先用 session_exists 打开一次，再为 current_model 打开一次；尚未登记时 cancel 会返回 run_not_found 404。历史检索入口未带取消参数；source 层捕获 Exception，未来若取消异常继承 Exception，必须避免被降级成 source failure。 | [AG-UI routes.py:71](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/agent_bridge/src/agent_bridge/protocols/ag_ui/routes.py#L71)、[session_bridge.py:89](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/agent_bridge/src/agent_bridge/session_bridge.py#L89)、[runs.py:106](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/agent_bridge/src/agent_bridge/routes/runs.py#L106)、[service.py:153](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/memory/src/memory/search/service.py#L153) |
| E-008 | refreshCurrentSession 每次发起详情请求，用序号丢弃旧响应，但没有在途请求合并；send finally、stop terminal 和 terminal push 均可触发。后端详情会 open 并投影 active events，因此丢弃旧响应不消除后端读取。 | [conversation.ts:835](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/frontend/src/stores/conversation.ts#L835)、[conversation.ts:1478](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/frontend/src/stores/conversation.ts#L1478)、[conversation.ts:1912](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/frontend/src/stores/conversation.ts#L1912)、[sessionSync.ts:94](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/frontend/src/stores/sessionSync.ts#L94)、[meta.py:270](https://github.com/A1m233/agent-friend/blob/f563938bd3f63c88a5e7a5b59825e3da33f06303/agent_bridge/src/agent_bridge/routes/meta.py#L270) |

E-006 的字节跨度由已归档 offset 相减得到；probe 的 `reverse.self.chars` 是字符数，不能当作字节数。E-007 的“未来取消异常可能被捕获”是兼容性风险，不是声称当前系统已存在并吞掉了该异常。E-008 支持重复请求的可能机制，不提供事故中重复请求次数或单独耗时。

## 已读外部参考的适用边界

这里只索引前期已阅读的固定官方源码，本次没有扩大外部调研。它们支持设计讨论，不是 agent-friend 实测结果，也不代表方案已经确认。

| 来源锚点 | 已读机制 | 可借鉴范围与限制 |
|---|---|---|
| Codex `3525845978210579572bd8ec4fe8c8296445fb8f`，[tasks/regular.rs:40](https://github.com/openai/codex/blob/3525845978210579572bd8ec4fe8c8296445fb8f/codex-rs/core/src/tasks/regular.rs#L40) | 准备工作前发出 TurnStarted，并传递取消 token | 讨论可见、可取消生命周期的开始边界；不是 Codex Desktop 内部实现证据 |
| 同 commit，[tasks/mod.rs:900](https://github.com/openai/codex/blob/3525845978210579572bd8ec4fe8c8296445fb8f/codex-rs/core/src/tasks/mod.rs#L900) | 有界取消与终态收口 | 讨论取消传播和清理边界；不直接照搬具体 timeout |
| 同 commit，[state/runtime/threads.rs:504](https://github.com/openai/codex/blob/3525845978210579572bd8ec4fe8c8296445fb8f/codex-rs/state/src/runtime/threads.rs#L504) | SQL 层列表分页 | 仅支持分页可行性评估；不能推出本需求必须改用 SQL 或立即实现分页 |
| Mem0 `dae67f74f5cc7bf138c7d7d6f9cec5ce4b4373b3`，[memory/main.py:1628](https://github.com/mem0ai/mem0/blob/dae67f74f5cc7bf138c7d7d6f9cec5ce4b4373b3/mem0/memory/main.py#L1628)、[vector_stores/qdrant.py:415](https://github.com/mem0ai/mem0/blob/dae67f74f5cc7bf138c7d7d6f9cec5ce4b4373b3/mem0/vector_stores/qdrant.py#L415) | 候选限额与底层查询 limit | 讨论限制读取与候选量的落点；向量存储接口不等同于本项目 JSONL 会话历史 |

前端详情请求合并来自本项目调用路径分析；没有证据证明它是 Codex Desktop 的内部实践。

## 后续复查与完整性

归档原文件 hash、采集口径和输入恢复限制，是为了区分已有证据与将来的新测量。若之后确需重跑，需由数据拥有者重新提供经授权、具有明确时间边界的私有输入，核对 query 的唯一绑定、scope、会话及 SQLite 一致性、模型资源、源码与 harness 身份。当前归档不能恢复这些输入，不应直接运行留存 probe。

新测量应获得独立证据 ID，不能覆盖这里的三次结果。若要解释原运行缺失的约 125 秒，最有区分力的是绑定源码与运行态身份的原进程分阶段观测，包含取消请求、登记、读取 / 解析与终态时间；这只是未决证据需求，不是本次授权执行项。

[MANIFEST.sha256](MANIFEST.sha256) 覆盖本目录两份 derived JSON、本说明和局部 `.gitattributes`。归档文件保持 UTF-8 without BOM、LF；局部 Git 属性固定 LF，避免 Windows checkout 改写字节造成哈希漂移。索引是持续维护入口，不包含在本批 artifact 的 immutable 内容清单中。原 report 与 probe 的 SHA256 单独保存在聚合 JSON 内；未把派生文件 hash 冒充原文件 hash。
