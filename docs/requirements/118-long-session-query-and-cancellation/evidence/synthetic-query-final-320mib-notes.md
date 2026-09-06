# E-012 · 最终候选合成查询复测（320 MiB payload）

本记录归档全部代码冻结后已完成的隔离合成复测，supersedes [E-011](synthetic-query-320mib-notes.md) 的“最终候选验证”用途。E-011 的原文件、数值及当时源码身份保持为历史证据，不作废其观察。本次归档不执行新实验、不读取真实会话、不调用 LLM。

## 来源、身份与完整性

- [完整统计报告](synthetic-query-final-320mib-report.json) 自报 `recorded_at_utc=2026-09-06T03:47:01.236771+00:00`；这是脚本生成报告时的 UTC 时间，不是每个测量片段的开始时间。来源为本工作树 `.tmp/query-118-final-320mib.json`，临时位置只在原机器可访问。
- 原文件 9514 字节，SHA256 `6c684540227fac6fdb2a964c46145618307dfa87d00a26ad946b2935cca8a9c6`；归档副本 9236 字节，SHA256 `e2f23986272983120d3bc94c550a52ea57abc875fd57da04ce76c2469b4c4e0a`。仅将 CRLF 规范为既有 Git 属性要求的 LF，全部 JSON 字段及语义一致。
- 报告内 [harness](../../../../scripts/session-query-benchmark/run.py) SHA256 为 `3c435840b805b82e03a8cd2b15584a0a0fcc38cca14798e33a9137d9cef657c6`，归档时与实际脚本字节一致。运行配置为 `--payload-mib 320 --repeats 3`。harness hash 在生成报告时计算，不把它扩张为脚本测量前后独立双检。
- baseline 只固定旧 history adapter，commit `f563938bd3f63c88a5e7a5b59825e3da33f06303`、SHA256 `e573b4515abaab4b395958427ac76d36f3e9cc1881c49fccdbc52d6f3f01bcaf`；归档时再次核对 Git blob 一致。两边仍共享当前 store 和 search service，实现不是完整旧产品基线，也不是原事故进程。
- candidate 是报告 HEAD `f563938bd3f63c88a5e7a5b59825e3da33f06303` 加未提交实现。报告列出 23 个源文件 SHA256，包含新增 `query_observation.py`，且 `candidate_sources_unchanged=true`；归档时与全部 23 个当前文件一致。这不是全部依赖闭包，不能用 HEAD 代替 overlay 或宣称来自 clean commit。
- 环境：Windows-11-10.0.26200-SP0、Python 3.12.6；processor `Intel64 Family 6 Model 198 Stepping 2, GenuineIntel`，逻辑 CPU 数 20。未记录物理核数、内存配置或测量时机器负载。

## 输入与控制边界

17 个合成 session 合计 335,720,327 字节；当前 session 为 335,712,066 字节。主要体积来自 1,280 条约 256 KiB 的内部 `model_transcript_item`，每会话只有一条短 user 和一条短 assistant 消息；当前详情返回 3 个 active events。总字节量不能代替真实可见历史的规模或复杂度。

自动查询为 synthetic `milestone`，当前会话无截止边界、不能参加本次自动召回。仅有 session_history source，没有结构化记忆、向量推理或真实模型调用。tokenizer 在双方查询前共同预热，两边使用独立 store/service 对象并在各自重复间保留缓存。

查询顺序为 baseline→candidate、candidate→baseline、baseline→candidate；三轮查询后才运行详情，详情始终 baseline→candidate。repeat 0 是相应查询缓存首次建立，OS page cache 未控制；不能称为磁盘冷启动，详情测量也不是进程首次访问该文件。

## 实测

毫秒，逐次列出；不计算 p95 或稳定速度倍数。

| 路径 | repeat 0 | repeat 1 | repeat 2 |
|---|---:|---:|---:|
| 旧 history adapter 查询 | 4,560.892 | 1,664.209 | 1,675.045 |
| 最终 candidate 查询 | 20.331 | 2.404 | 3.303 |
| full-load 详情 | 801.819 | 795.120 | 745.754 |
| query projection 详情 | 652.160 | 0.361 | 0.306 |

旧 adapter 查询每次记录 34 次 load / 671,440,654 字节，最终 candidate 首次为 16 次 load / 8,261 字节，后两次无记录 load。scope scan 分别为 2 次与 1 次。详情双方首次均读取 335,712,066 字节；candidate 后两次复用，full-load 仍逐次读取整份文件。

逐路径、逐 repeat 的 load 次数和字节数与 E-011 相同；新增查询观测没有混入 file-operation 计数。这里的字节数不包含 summary 反扫和全部元信息访问，是该观测口径的下界；“0 load”不等于完全没有磁盘 I/O。详情首次仍需扫描和解析大文件。

## 正确性与解释边界

- `results_equal=true`：脚本 `_signature` 比较最终有序 ID、正文、provenance、session 范围、excerpt、截断、失败数量与 scope order status；SHA256 `64f596db9bde5f5c1fffb3e255597c9cf09728be67cf31bbe8db8ecb9569c6c0`。不比较 score；资格排除可能压缩 keyword rank 空位，不能声称评分数值完全相同。
- `detail_events_equal=true`：六次当前会话详情的 active events 序列化内容与顺序一致，未覆盖任意复杂分支或损坏尾部。
- E-012 与 E-011 的时延不同，但二者不是控制 OS cache、负载、执行时间和观测成本的独立 A/B。不能据此把差值归因于新增日志、宣布日志性能回归，或反向宣称新增观测零开销。需要专门控制变量的测量才能回答该问题。
- 每路径仅 3 次、同一 fixture 自然重复。可见正文很少，未验证大规模可见消息、复杂工具分组、并发追加、多窗口、失效重建、首次 tokenizer、CLI/语音/IM 端到端或取消时延；不能外推真实 335 MB 会话及总体 p95。
- 本证据支持最终候选在该合成输入下减少无关读取和重复处理，并保持已比较结果；数值和该 fixture 的检查为 confirmed，真实场景收益仍为 directional。它不取代 E-001 至 E-004，也不解释原现场额外约 125 秒。

本记录不复制全量门禁账本；代码测试与验收状态由 [progress.md](../progress.md) 维护。后续如要扩大性能结论，仍需不同可见历史规模、并发和失效条件下的隔离对照，以及身份绑定的现场阶段观测。

完整性清单见 [MANIFEST.sha256](MANIFEST.sha256)；旧 artifact 保持原字节与原 hash。
