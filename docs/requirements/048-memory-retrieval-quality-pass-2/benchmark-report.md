# 048 · M48.0 记忆检索底座 Benchmark 报告

## 1. 状态

- M48.0a（现有 FTS / history 与预备默认 Chroma + Sentence Transformers + BGE）已完成。
- M48.0a 结论：质量收益成立，但预备默认组合未通过桌面端资源门槛。
- M48.0b（ONNXRuntime + sqlite-vec 轻量候选）已完成并通过 Windows 门槛。
- 最终 `EmbeddingProvider` / `VectorIndex` 方向已锁定；等待用户 review 后解除 M48.1 门禁。
- 本报告全部检索测试均为本地执行，外部 LLM 调用次数为 0。

原始数据：

- M48.0a：[`benchmark-results/2026-07-13T16-37-11-windows.json`](benchmark-results/2026-07-13T16-37-11-windows.json)
- M48.0b：[`benchmark-results/2026-07-13T17-06-54-windows.json`](benchmark-results/2026-07-13T17-06-54-windows.json)

## 2. 环境与输入

| 项目 | 值 |
|---|---|
| 平台 | Windows 11, AMD64 |
| Python | 3.12.6 |
| CPU 逻辑核 | 20 |
| 检索语料 | M48.0a 1000 条；M48.0b 10000 条；均含 12 条目标事实与确定性噪声 |
| 查询 | 16 条：8 条关键词、8 条语义改写 |
| warm 轮数 | 每条查询 5 轮，共 80 个 latency sample |
| FTS | 生产 `SqliteMemoryStore`：SQLite FTS5 + jieba |
| M48.0a dense | Chroma 1.5.9 + Sentence Transformers 5.6.0 |
| M48.0b dense | sqlite-vec 0.1.9 + ONNXRuntime 1.27.0 + tokenizers 0.22.2 |
| embedding | BGE-small-zh-v1.5，512 维，CPU；M48.0b 使用固定 revision 的量化 ONNX proxy |
| history | 只读本机 31 个 session / 887117 bytes JSONL；报告不记录正文 |

固定语料只用于判断机制和候选底座，不替代 PerLTQA / agent_eval 的真实链路验收。

## 3. 质量结果

### 3.1 总体

| 通道 | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| FTS-only | 0.8125 | 0.8750 | 0.9375 | 0.8563 |
| dense-only | 0.8125 | 0.8125 | 0.8125 | 0.8125 |
| FTS + dense RRF | 0.8125 | 1.0000 | 1.0000 | 0.9063 |
| M48.0b quantized dense-only | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

### 3.2 改写查询

| 通道 | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| FTS-only | 0.6250 | 0.7500 | 0.8750 | 0.7125 |
| dense-only | 0.8750 | 0.8750 | 0.8750 | 0.8750 |
| FTS + dense RRF | 0.6250 | 1.0000 | 1.0000 | 0.8125 |
| M48.0b quantized dense-only | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

结论：BGE 对语义改写确有明显增益；等权 RRF 能稳定扩大 top-3 / top-5 覆盖，但没有改善本语料的 top-1。M48 后续仍需在 RRF 后叠加既定 policy bonus，并以 PerLTQA 对照验证最终排序。

## 4. 性能与资源

### 4.1 FTS 与 history

| 路径 | p50 | p95 | 备注 |
|---|---:|---:|---|
| FTS warm query | 0.237ms | 1.040ms | 1000 条记忆 |
| history list | — | — | 单次 4.490ms |
| history full scan | 22.366ms | 39.533ms | 31 session，只读，无命中正文输出 |

当前规模下 history 直扫仍在 75ms 硬门槛以内，因此 M48.1 不预先建立 SessionStore 派生索引。达到 §7 的升级条件时再增加，不让优化先于真实瓶颈。

### 4.2 Chroma + Sentence Transformers + BGE

| 指标 | 结果 |
|---|---:|
| Chroma import | 818ms / +21.4MB RSS |
| Sentence Transformers import | 8567ms / +318.4MB RSS |
| 模型加载 | 14208ms / +16.1MB RSS |
| 1000 条文档编码 | 2584ms，2.584ms / item |
| Chroma 索引构建 | 1093ms |
| warm index reopen | 31.9ms |
| warm end-to-end query p50 / p95 | 12.0ms / 13.9ms |
| dense 链路总增量 RSS | +524.0MB |
| 模型资产 | 91.9MB |
| 1000 条索引 | 6.9MB |

warm 延迟和模型磁盘满足预期，但冷路径与常驻内存明显超标。即使 dense 在后台懒加载，约 524MB 的增量 RSS 仍会成为桌面端长期成本，不能用“启动不阻塞”掩盖。

### 4.3 ONNXRuntime + sqlite-vec + 量化 BGE

| 指标 | 结果 |
|---|---:|
| 依赖 import 合计 | 339ms |
| 模型加载 | 141ms |
| 10000 条文档编码 | 15585ms，1.559ms / item |
| sqlite-vec 流式索引构建 | 647ms |
| warm index reopen | 6.5ms |
| warm end-to-end query p50 / p95 | 29.2ms / 37.8ms |
| dense 稳态增量 RSS | +191.6MB |
| 量化模型与 tokenizer 资产 | 23.2MB |
| 10000 条 sqlite-vec 索引 | 20.7MB |

M48.0b 在十倍语料规模下通过质量、延迟、RSS 和磁盘门槛。索引写入使用生成器流式序列化，并在 generation 构建完成后释放批量文档向量；否则后台 backfill 的 Python 高水位会额外保留约 20MB RSS。

## 5. M48.0 硬门槛

| 门槛 | 阈值 | M48.0a | M48.0b |
|---|---:|---|---|
| keyword-ready 不等待 dense | 必须 | 通过设计保证 | 通过设计保证 |
| FTS warm p95 | 1000 条 ≤ 5ms；10000 条 ≤ 15ms | 1.040ms | 11.841ms |
| history 直扫 warm p95（当前 Windows 规模） | ≤ 75ms | 39.533ms | 42.959ms |
| hybrid warm end-to-end p95 | ≤ 50ms | 13.894ms | 37.750ms |
| dense 常驻增量 RSS | ≤ 200MB | **失败，523.972MB** | 通过，191.648MB |
| embedding 模型资产 | ≤ 120MB | 91.940MB | 23.179MB |
| 10000 条向量索引 | ≤ 50MB | 未实测 | 通过，20.680MB |

## 6. 决策

1. 最终 `EmbeddingProvider` 锁定为 CPU ONNXRuntime + `tokenizers`，不让 Transformers / Sentence Transformers / PyTorch 成为桌面常驻依赖。
2. 最终 `VectorIndex` 锁定为 sqlite-vec。稳定字符串 evidence id 保存在普通 SQLite 映射表，`vec0` 只持有整数 rowid 与派生向量。
3. 逻辑模型锁定 `BAAI/bge-small-zh-v1.5`、512 维、query-only 中文 instruction。生产资产采用项目自行可复现导出的量化 ONNX，固定上游 revision 与文件 hash。
4. M48.0b 的社区 ONNX revision `9507db33464b5da99a532ac26b2a251767cbc62b` 只作为性能/质量 proxy；原始报告保存了四个资产文件的 SHA-256。
5. backfill 必须批量编码、流式写 sqlite-vec，并在 generation 构建后释放整批向量；检索只读 active generation。
6. macOS 当前不在开发范围，不作为本需求门禁；文档标记“未验证”，未来 macOS 进入开发范围后补同脚本验证。

## 7. SessionStore 派生索引升级条件

当前继续使用 session summary 粗筛 + event 扫描。满足任一条件时，另行启用可重建派生索引：

- Windows 当前机器上 explicit history warm p95 连续超过 75ms；
- 自动 fallback 加入其他检索与渲染后，整条主读路径 p95 超过 50ms；
- JSONL 总量达到 10MB 或 session 数达到 100，复测证明扫描呈明显线性卡顿；
- 实际用户反馈出现可感知的历史查询停顿。

## 8. 尚未完成

- 用户对 M48.0b 最终结果的 review；确认前不进入 M48.1。
- migration / resume / generation switch 的生产实现与 smoke：底座已锁定，但该生命周期属于 M48.3；M48.0b 已验证 sqlite-vec 持久 reopen、字符串 ID 映射和 KNN 基础能力。
- PerLTQA keyword-only / hybrid 对照与 agent_eval：属于 M48.5，执行真实抽取前仍需按次确认外部 LLM 调用。
