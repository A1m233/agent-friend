# Exploration · 中文 PerLTQA 差距与记忆架构演进

> 状态：探索中，不构成架构决策或需求承诺
> 当前快照：2026-07-15
> 范围：先把中文长期记忆做好；暂不建立英文评测线

## 1. 为什么要单独讨论这件事

[`048-memory-retrieval-quality-pass-2`](../../requirements/048-memory-retrieval-quality-pass-2/progress.md) 完成后，当前完整终态 E1R1 在中文 PerLTQA dialogues 子集上达到：

- anchor coverage macro：`0.434399`
- 零分率：`35.90%`
- 相对同轮 E0R0：提升 `6.16pp`
- paired bootstrap 95% CI：`[0.046784, 0.076369]`

这证明 048 的完整终态相对同轮 baseline 有稳定提升，但不能回答另外两个问题：

1. 我们与公开前沿方案究竟相差多少？
2. 下一轮投入应该继续调 embedding / top-k，还是升级证据组织方式？

本 exploration 用于把这两个问题拆开。它只记录研究判断与待验证假设；一旦某条路线需要正式交付，应另开 requirement，并在那里确定 AC、实现范围和资源预算。

## 2. 第一条边界：现有公开分数不能直接横比

### 2.1 我们测的是什么

当前 `memory_eval` 使用 PerLTQA **中文数据的 dialogues 子集**，排除了 profile、social relationship、events 等结构化记忆类型。评测对象是 memory 召回结果，不包含最终回答生成：

- judge：`AnchorRecallJudge`
- 指标：召回文本覆盖多少个题目标注 anchor
- 计分：逐题 `命中 anchor 数 / anchor 总数`，再取 macro
- 当前完整终态：`0.434399`

这个指标适合定位“证据有没有被 memory 找出来”，但不是端到端问答准确率。

### 2.2 xMemory 公开结果测的是什么

[xMemory 论文](https://arxiv.org/html/2602.02007v4)在 PerLTQA 上报告 BLEU、token-level F1、ROUGE-L 和每题 token 用量，评测包含检索后的答案生成。[官方仓库](https://github.com/HU-xiaobai/xMemory)说明其使用 PerLTQA `en_v2`，不是我们当前的中文 dialogues-only 投影。

论文 Table 2 中 xMemory 的结果如下：

| Reader backbone | BLEU | F1 | ROUGE-L | Token/query |
|---|---:|---:|---:|---:|
| Qwen3-8B | 36.24 | 47.08 | 42.50 | 5087.18 |
| Llama-3.1-8B-Instruct | 42.68 | 52.37 | 47.84 | 6066.40 |
| GPT-5 nano | 36.79 | 46.23 | 41.25 | 7307.30 |

这些数字与我们的 `43.44% anchor coverage` 看似处在相近量级，但分母、语言、数据投影和 reader 都不同，**不能做减法，也不能据此宣称我们大幅落后、接近或超过 xMemory**。

### 2.3 当前能下的结论

- 048 已证明：在同一轮中文语料和同一问题集上，完整终态优于并发 baseline。
- 公开前沿方案证明：分层证据结构与自适应展开在 PerLTQA 上可能优于 flat top-k RAG。
- 我们与公开前沿方案的真实量化差距仍然未知。
- 在中文评测线尚未统一前，不建立“业界排行榜”，也不为了对齐论文而切去英文数据。

## 3. 架构对照：差距主要不在向量模型

### 3.1 我们当前的结构

048 之后的生产 memory 已具备：

- `core / priority / normal` 记忆等级，以及 semantic / episodic / raw utterance 证据类型；
- 受限 `fact_key`、current / historical 查询、`supersedes_id` 版本链；
- 中文 FTS5 + 本地量化 BGE-small-zh ONNX + sqlite-vec；
- keyword / dense 候选经 RRF、规则加权、去重和固定配额融合；
- 结构化证据不足时，小预算回退 SessionStore 原始对话；
- automatic 与 explicit recall 共用检索服务、有效性规则和 trace；
- 可恢复的 embedding manifest / generation，以及完整 generation 原子切换。

这些能力更接近可落地的个人记忆基础设施：本地 Windows 可运行，身份事实与版本更新有确定性语义，索引失败不会破坏正文，线上行为可解释。

但从检索视角看，它仍然接近一个“多来源、扁平候选池”：每条 semantic、episodic 或 utterance 独立竞争名额，RRF 主要解决不同召回器的 rank 融合，并不显式优化“一组证据是否共同覆盖回答所需的多个事实”。

### 3.2 xMemory 的关键结构

xMemory 使用四级结构：

```text
raw messages → segments → memory components → groups
```

- segment 保存相邻多轮构成的完整局部事件；
- component 从事件中拆出事实、约束、关系和状态更新，同时保留回到 segment 的指针；
- group 聚合相关 component，作为高层导航单元；
- 新记忆到来后允许 attach、split、merge，并追溯性重组旧结构。

检索分两阶段：

1. 先在 group / component 层选择相关且互补的证据骨架，兼顾 query relevance 与结构覆盖；
2. 只有当 segment 或 raw message 能降低 reader 的不确定性时，才展开更细原文。

它针对的不是“相似度不够准”，而是 agent memory 中高度相关、近重复的历史会让固定 top-k 浪费预算，同时漏掉细微更新或多跳前提。

### 3.3 双方各自更强的部分

| 维度 | agent-friend 当前实现 | xMemory 公开方案 |
|---|---|---|
| 身份与事实版本 | core、受限 fact key、current/history、原子 supersede | 论文重点不在产品级身份槽位与版本契约 |
| 本地产品约束 | Windows CPU、本地 embedding、SQLite、失败可降级 | 公开实验使用外部 embedding，并提示 A100 80G 环境 |
| 索引生命周期 | generation 原子激活、可恢复回填、stale validity recheck | 重点是 hierarchy 动态维护 |
| 候选组织 | 多源扁平候选 + RRF + 固定预算 | component/group 层级 + 动态 split/merge |
| 多证据覆盖 | 间接依赖 rank、规则和配额 | Stage I 显式选择互补证据骨架 |
| 原文展开 | 结构化低置信时按规则回退原始历史 | Stage II 依据 reader uncertainty 自适应展开 |
| 可观测性 | source run、rank、fusion、预算、index state trace | 论文更关注实验效果和结构分析 |

因此，不应把下一阶段简化成“换一个更强 embedding”。048 的冻结 DB 结果已经显示，unified-hybrid 相对 unified-keyword 的净增益很小；更值得验证的差距是：

> 从“候选融合”升级为“证据结构”，让检索目标从单条相关性变成相关性、互补性与证据完整性的联合选择。

## 4. 中文优先的公平比较

短期不复刻 xMemory 的英文 `en_v2` 结果，也不把英文 leaderboard 当目标。下一轮公平比较应固定在中文 PerLTQA，并把检索与回答两个问题分层测量。

### 4.1 召回层

继续复用现有中文 dialogues 语料、问题集和 anchor judge，对相同的冻结 memory corpus 比较：

- 当前 unified retrieval；
- 分层索引原型；
- 两者相同的 evidence budget 与 renderer budget。

除 macro 和零分率外，新增更能反映结构价值的观测项：

- 多 anchor 题的完整覆盖率；
- 命中多个独立证据来源的比例；
- 重复证据占用的预算；
- 召回证据字符数 / token 数；
- current / historical、时间题和知识更新题的分切片结果。

### 4.2 回答层

anchor coverage 只能说明关键字是否进入上下文，不能说明 reader 是否正确组合证据。中文端到端比较应在召回语料冻结后，给各检索方案使用同一个 reader、同一 prompt、同一解码参数和交错调度，并报告：

- 中文答案的 token-level F1 / ROUGE-L 等确定性指标；
- 对时间、更新、多事实题的规则型正确性；
- 如确需 LLM judge，再单独预注册 rubric、调用量与 stop rule。

这条回答层是未来实验建议，不代表已经决定调用外部 LLM；实际执行仍需逐次授权。

## 5. 建议的验证路径

### 阶段 A：只改离线索引，不碰生产存储语义

用现有数据构造一次性原型：

- semantic / episodic 先视为 component；
- provenance 相邻关系与 SessionStore 局部窗口视为 segment；
- 用 embedding 邻域或轻量聚类派生 group；
- group / kNN 图全部作为可丢弃索引，不改 memory.db 正文，也不改变 core、fact_key、supersede 语义。

这一阶段先回答“结构化导航有没有增益”，避免一开始把写入、存储、检索和产品行为全部重做，导致实验无法归因。

### 阶段 B：比较证据选择目标

在同一套派生层级上对比：

- 当前 RRF + policy bonus；
- relevance + diversity；
- relevance + anchor/evidence coverage 的离线近似；
- 固定预算下的 group-first → component refinement。

只有分层选择在多事实题、时间题或更新题上形成稳定净增益，才值得继续投入。

### 阶段 C：验证自适应原文展开

先用不依赖额外 LLM 的确定性信号建立便宜基线，例如 query token 覆盖、候选分差、证据来源数和是否存在 current/history 冲突。再与 reader uncertainty 方案比较质量、延迟和调用成本。

如果确定性信号已经足够，不必为了复刻论文而把每次 memory retrieval 都变成额外 LLM 推理。

### 阶段 D：最后再研究动态维护

attach / split / merge 和旧结构追溯重组会影响写入成本、索引一致性与调试复杂度。只有静态派生层级已证明有效，才继续验证：

- 增量 group attach；
- 过大或语义异质 group 的 split；
- 过小或孤立 group 的 merge；
- 新事实到来后是否需要重组旧 component。

## 6. 暂不建议做的事

- 不直接复刻 xMemory 全套实现；它的研究运行环境和我们的 Windows 本地产品约束不同。
- 不把更换 embedding 当作主优化方向；现有证据不支持它是主要瓶颈。
- 不在验证结构收益前引入在线动态图维护或每题额外 LLM uncertainty 调用。
- 不用不同语言、不同数据投影、不同 reader 的公开数字给当前系统下行业排名结论。
- 不为了提升 anchor substring 分数而牺牲语义正确性；召回层和回答层必须同时看。

## 7. 当前研究判断

截至 2026-07-15，可以堂堂正正下的结论是：

1. 048 的架构升级不是失败：同轮 factorial 证明完整终态相对 baseline 有显著提升。
2. 我们仍没有证据证明已经达到或大幅低于 PerLTQA 前沿，因为当前公开数字不可比。
3. 当前系统的产品工程底座并不弱，特别是身份事实、版本治理、本地运行和索引生命周期；不应推倒重来。
4. 最值得验证的下一跳不是继续堆召回器，而是在现有底座上增加可丢弃的层级证据索引，并检验“相关性 + 互补覆盖”是否胜过扁平 RRF。
5. 中文公平评测是进入下一项正式需求前的先决条件；英文评测暂不进入路线图。

## 8. 参考资料

- Hu et al., [Beyond RAG for Agent Memory: Retrieval by Decoupling and Aggregation](https://arxiv.org/html/2602.02007v4), arXiv:2602.02007v4, 2026-05-12.
- [HU-xiaobai/xMemory](https://github.com/HU-xiaobai/xMemory)，论文配套代码与运行说明。
- Du et al., [PerLTQA: A Personal Long-Term Memory Dataset for Memory Classification, Retrieval, and Fusion in Question Answering](https://aclanthology.org/2024.sighan-1.18/), SIGHAN 2024.
- 本项目 [`048` 技术方案](../../requirements/048-memory-retrieval-quality-pass-2/design.md)与[终局进度记录](../../requirements/048-memory-retrieval-quality-pass-2/progress.md)。
