# E-011 · 合成长会话查询对照（320 MiB payload）

本记录归档 2026-09-06 已完成的本地隔离对照，不复跑实验、不读取真实会话、不调用 LLM。证据基础为 controlled-comparison：下面的数值在该次运行中 confirmed；对真实长会话的收益仅为 directional，不能解释 E-001 的 136.628 秒。

## 来源与版本

- [完整统计报告](synthetic-query-320mib-report.json) 来自本工作树 `.tmp/query-118-320mib.json`。该临时位置只在原机器可访问；本归档包含全部 JSON 字段，仅将 CRLF 规范化为既有 Git 属性要求的 LF，JSON 语义核对相同。
- 原文件：9,143 字节，SHA256 `9a47079853026b10e69ef94b795f3138d633c790b2aad55a67ce9053b389e5c8`。归档副本：8870 字节，SHA256 `c79e101eb002e917af60a084bed2591825e2ad7ce289262820e378fce85826ef`。
- baseline 仅固定旧 history adapter：`f563938bd3f63c88a5e7a5b59825e3da33f06303`，其源码 SHA256 为 `e573b4515abaab4b395958427ac76d36f3e9cc1881c49fccdbc52d6f3f01bcaf`；归档时用 Git blob 重新核对一致。两边 store、search service 均使用当前实现，包括 summary 优化，因此不是完整旧产品基线，也不是原事故进程。
- candidate 历史身份为同一 HEAD 加未提交实现；报告列出 22 个候选源文件的 SHA256，`candidate_sources_unchanged=true` 表示这些文件在测量前后未变化。归档时再次与全部 22 个当前文件比对一致。该集合不是所有依赖的完整内容清单；HEAD 本身不能代表候选实现。
- [测量脚本](../../../../scripts/session-query-benchmark/run.py) 的归档时 SHA256 为 `9fb1529103b1bc2f121342126d37266118e3ebb57ab2ce7b2e7416cb603ca152`。报告没有独立记录运行时 harness hash，此处是归档时源码锚点，不追认成报告自证的历史值。运行配置为 `--payload-mib 320 --repeats 3`；脚本读取固定旧 adapter 并生成临时合成 fixture。
- 环境：Windows 11（10.0.26200）、Python 3.12.6；报告没有记录 CPU、内存配置或精确采集时刻。归档日期不替代缺失的运行时间戳。

## 输入、顺序与测量口径

17 个 session 合计 335,720,327 字节。当前 session 为 335,712,066 字节，其中主要体积来自 1,280 条约 256 KiB 的合成 `model_transcript_item`；每个 session 只有一条短 user 和一条短 assistant 消息。当前详情返回 3 个 active events，不能用总字节量声称覆盖了等规模的真实可见对话历史。

查询为 synthetic `milestone`，自动召回的当前会话无截止边界，因此当前会话不具备本次召回资格。只有 session_history 来源，没有结构化记忆、向量推理或模型调用。查询前共同预热 tokenizer；两边分别持有独立 store/service 对象，在各自三次运行内自然保留进程缓存。

查询顺序为 baseline→candidate、candidate→baseline、baseline→candidate。详情在全部查询后执行，每次均 baseline→candidate。查询和详情的 repeat 0 分别表示对应查询缓存首建，不表示磁盘冷态或进程第一次执行 Python；OS page cache 未控制，详情顺序也没有交替。

## 直接观察

单位均为毫秒，保留逐次值，不报告 p95。完整文件等待、持锁、解析时间见 JSON；这些分阶段累计值不能与端到端时长简单相加。

| 路径 | repeat 0 | repeat 1 | repeat 2 |
|---|---:|---:|---:|
| 旧 history adapter 查询 | 4,153.295 | 1,500.120 | 1,475.738 |
| candidate 查询 | 15.773 | 1.781 | 2.064 |
| full-load 详情 | 727.574 | 755.634 | 744.955 |
| query projection 详情 | 593.212 | 0.248 | 0.206 |

旧 adapter 每次查询记录 34 次 load、671,440,654 字节；candidate 首次记录 16 次 load、8,261 字节，随后两次没有记录 load。旧 adapter 每次 scope scan 为 2 次，candidate 为 1 次。详情两边首次都读取 335,712,066 字节；candidate 随后两次没有记录 load，full-load 仍每次读取整份文件。

上述“字节”和“0 load”是 file-operation 观测口径，不含 summary 反扫或所有文件元信息访问，不能写成总磁盘 I/O 为零。首建详情仍扫描并解析大文件，收益主要体现在资格排除和后续复用；首次详情差值不应解释为取消了全部扫描工作。

## 一致性、局限与结论边界

- `results_equal=true`：比较最终 evidence 的顺序、ID、正文、provenance、excerpt、范围及截断等签名字段；`signature_sha256=64f596db9bde5f5c1fffb3e255597c9cf09728be67cf31bbe8db8ecb9569c6c0`。完整字段定义见脚本 `_signature`。本对照不比较 score；资格排除可能压缩 keyword rank 空位，不能声称数值评分完全不变。
- `detail_events_equal=true`：比较当前会话 active events 的序列化内容与顺序，六次详情结果一致；不是跨任意会话、分支或损坏状态的证明。
- 只有同一 fixture 的三次顺序重复，没有三个独立机器或数据样本；不能推断 p95、稳定速度倍数、并发容量或服务时延承诺。OS cache、执行顺序和日志观测成本仍可能影响数值。
- fixture 体积大而可见历史很小；未覆盖大型可见正文、工具分组、复杂分支、并发追加、多窗口、失效重建、首次 tokenizer、语音/IM 或取消时延。不能外推“真实 335 MB 会话已全面解决”。
- 本证据补充 E-004 的读取放大方向，并在该合成样本上增加正文和顺序 guardrail；不替代 E-001 至 E-004，不填补原现场额外约 125 秒的归因缺口。最有区分力的后续证据仍是身份绑定的现场阶段观测，以及不同可见历史规模、并发和失效条件下的隔离对照。
- 两份先期小规模 smoke 只验证脚本入口，已由进度记录说明；未混入本报告指标，也未纳入本批归档。

完整性由 [MANIFEST.sha256](MANIFEST.sha256) 管理。既有原观测和聚合 artifact 保持原字节及历史内容不变。
