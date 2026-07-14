# memory_eval

`agent-friend` 的**记忆召回质量评测**：把外部 agent-memory 基准的对话灌进 [`memory`](../memory/) 的真实写路径（`Memory.observe`），再用基准问题走 `Memory.retrieve`，对召回内容**判分** + 落 baseline 文件，作为持续度量记忆策略的工具。

设计文档：[011 评测机制](../docs/requirements/011-memory-recall-eval/) + [012 锚点召回判分](../docs/requirements/012-memory-eval-anchor-recall-scoring/)。

## 边界

本包是 `memory` 公共接口的**消费方**，**单向依赖** `memory` + `llm_providers`，绝不反向污染 `memory` 核心包的依赖（数据加载、judge 等评测专属依赖只落在这里）。

## 支持的基准与判分

| 基准 | 语言 | 取用范围 | 判分 |
|---|---|---|---|
| **PerLTQA**（默认） | 原生中文 | 仅 **dialogues 子集** | **AnchorRecallJudge**：基于 PerLTQA 自带 `Memory Anchors`，substring 匹配召回内容，输出 `命中 anchor 数 / 总 anchor 数 ∈ [0, 1]` |
| LoCoMo | 英文 | 全量 QA | NoopJudge（无 anchor 数据，不打分；保留作英文基线对照、跑通流程） |

> 实测发现：用英文 LoCoMo 测中文 memory 时，「英文 query × 中文记忆」会让 FTS5 关键词召回严重失效，分数不反映真实能力——故默认走原生中文的 PerLTQA。

判分器的已知偏差（substring 看不出同义改写）与未来升级路径见 [`012 design §8`](../docs/requirements/012-memory-eval-anchor-recall-scoring/design.md#8-已知偏差与升级路径)。

## 内部结构

```
src/memory_eval/
├── datasets/   # 基准数据加载 → 统一 EvalCase（与 memory 无耦合的纯数据层）
├── adapters/   # EvalCase → ConversationFragment（observe）；question → retrieve
├── harness/    # runner（编排）+ judge（判分扩展点 + AnchorRecallJudge）
│              # + workspace（持久 DB / checkpoint）+ report / baseline
└── __main__.py # CLI 入口
```

## 数据集（不入 git）

数据文件较大，**不纳入版本控制**（被根 `.gitignore` 的 `data/` 规则忽略）。按需下载：

- **PerLTQA**（默认）：从 <https://github.com/Elvin-Yiming-Du/PerLTQA> 的 `Dataset/zh/` 下载 `perltmem.json` + `perltqa.json`，放到 `memory_eval/data/perltqa/`（CC BY-NC 4.0，仅非商用研究，勿分发 / 勿用于商用训练）。
- **LoCoMo**：从 <https://github.com/snap-research/locomo> 下载 `data/locomo10.json`，放到 `memory_eval/data/locomo10.json`。

## 运行

> ⚠️ 首次 ingest **会触发真实 LLM 抽取调用**（每段对话一次抽取）。按项目 `llm-api-confirm` 规则，运行前需获授权。`--replay` 只读取已完成的 case DB，不初始化凭证、不调用 LLM。单测使用 fake LLM。

```bash
# 首次抽取并跑当前 keyword；workspace 会持久化 DB 与逐 case checkpoint
./scripts/memory-eval/run.sh --retrieval-mode keyword --workspace memory_eval/data/runs/example --limit-samples 3 --limit-questions 10
# 同一份抽取产物复放 legacy keyword（0 次 LLM）
./scripts/memory-eval/run.sh --replay --retrieval-mode legacy-keyword --workspace memory_eval/data/runs/example --limit-samples 3 --limit-questions 10
# 同一份抽取产物复放 hybrid；dense 未 ready 会直接失败
./scripts/memory-eval/run.sh --replay --retrieval-mode hybrid --workspace memory_eval/data/runs/example --limit-samples 3 --limit-questions 10
# 按生产装配同时注册 structured memory + SessionHistorySource；仍是 0 次 LLM
./scripts/memory-eval/run.sh --replay --retrieval-mode unified-keyword --workspace memory_eval/data/runs/example --limit-samples 3 --limit-questions 10
# 英文基线 LoCoMo + NoopJudge（不打分，仅展示召回）
./scripts/memory-eval/run.sh --dataset locomo --retrieval-mode keyword --limit-samples 1 --limit-questions 5
# 固定 30 个 fragment，每个重复抽取 3 次；只测抽取稳定性，不建 memory DB、不跑检索
# ⚠️ 共 90 次真实 LLM 调用，仍须按 llm-api-confirm 在当次运行前获得授权
./scripts/memory-eval/run.sh --stability-probe --temperature 0 --stability-fragments 30 --stability-repeats 3 --limit-samples 31 --workspace memory_eval/data/runs/stability-example

# pass-1 / current prompt 的 AB/BA 小样本诊断（30 × 2 × 3 = 180 次调用）
./scripts/memory-eval/run.sh --paired-extraction-probe --stability-fragments 30 --stability-repeats 3 --workspace memory_eval/data/runs/paired-example

# 048 终局：E0/E1 各抽取 1072 fragments，再复放 R0/R1；真实调用前必须单次授权
./scripts/memory-eval/run.sh --factorial-eval --limit-samples 32 --limit-questions 200 --workspace memory_eval/data/runs/factorial-example
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--dataset` | `perltqa` | `perltqa`（中文，dialogues 子集）/ `locomo`（英文基线） |
| `--limit-samples` | `1` | 只跑前 N 个样本（控制成本） |
| `--limit-questions` | `5` | 每个样本只问前 N 个问题 |
| `--retrieval-mode` | 普通评测必填 | `legacy-keyword` / `keyword` / `hybrid` / `unified-keyword` / `unified-hybrid`；`unified-*` 复用生产 automatic history fallback；hybrid 禁止静默降级 |
| `--workspace` | 自动生成 | 持久化 case DB、抽取完成标记和逐 case 检索结果 |
| `--resume` | 关闭 | 续跑相同配置的 workspace；跳过已完成 case |
| `--replay` | 关闭 | 只复放已有抽取 DB；不读取凭证、不调用 LLM |
| `--stability-probe` | 关闭 | 隔离重复抽取固定 fragment；记录原始响应、解析状态与输出 hash，不建 DB、不跑检索 |
| `--paired-extraction-probe` | 关闭 | 从 `--paired-baseline-ref` 读取 pass-1 prompt，与当前 prompt 做 AB/BA 配对抽取诊断 |
| `--paired-baseline-ref` | `origin/main` | baseline prompt 的 git ref；运行时解析为不可变 commit 并锁入 config |
| `--factorial-eval` | 关闭 | 构造 E0/E1 两份独立语料并复放 legacy / unified-hybrid，输出四条件 baseline 与 paired-bootstrap 报告 |
| `--stability-fragments` | `30` | 稳定性 probe 按稳定 hash 固定选择的 fragment 数 |
| `--stability-repeats` | `3` | 每个固定 fragment 的重复抽取次数 |
| `--dense-model-dir` | 标准用户数据目录 | hybrid 的本地 ONNX 资产目录 |
| `--model` | runtime_config 默认 | 覆盖抽取用 model |
| `--timeout-seconds` | provider 默认（当前 90 秒） | 覆盖单次抽取调用超时；运行较慢的 Pro 全量评测时可设为 `300` |
| `--temperature` | provider / 模型默认 | 显式覆盖抽取 temperature；稳定性对照建议用 `0`，实际值写入 workspace manifest 与 baseline provider defaults |

## 跑出来会得到什么

1. **控制台逐题输出**：问题 / 标准答案 / 召回到的记忆条目 / 判分（PerLTQA 显示 `hit/total` + 未命中 anchor 清单）。
2. **跑完汇总**：macro 平均 + 0 分错题完整清单（含 anchor 与召回条目，便于复盘"为什么完全没召回"）。
3. **逐 fragment / case checkpoint**：每次抽取即时追加 `extraction-attempts.jsonl`，记录 success / empty / invalid / API error、耗时、解析产出数量与原始响应；case 完成后写汇总。进程中断后保留已完成抽取与检索结果。
4. **baseline 文件落盘**：除 macro 与召回正文外，记录实际 retrieval mode、dense index 状态和候选 keyword/dense rank trace。

稳定性 probe 单独写 `stability/config.json`、`attempts.jsonl` 与 `summary.json`。配置包含固定 probe id 和 repeats；`--resume` 配置不一致时 fail-closed。summary 直接报告 stable / unstable、always-empty / always-success / mixed-status、三种不同输出的 probe 数，以及每轮 success / empty / error 和 semantic / episodic 总量。workspace manifest 还会锁定抽取 prompt / 解析 / fragment 投影代码指纹，避免跨实现续跑混料。原始响应可能含对话内容，评测 workspace 不应对外分发。

baseline 文件**随代码入 git 归档**，作为可在历史上溯源、跨设备一致的对比基线。字段语义见 [`baselines/README.md`](baselines/README.md)。

## 改 memory 时怎么用 baseline

1. 首次 ingest 固定一份 workspace，不把不同 LLM 抽取结果混入 retrieval 对比。
2. 在同一 workspace 上依次 `--replay` legacy keyword、current keyword、hybrid。
3. 对比 macro、零分率、逐题召回和候选 trace；只有写路径变更才重新 ingest。
4. 普通单 corpus PerLTQA 全量仍只作观察性 holdout；048 正式终局使用 `--factorial-eval` 的并发 E0/E1 × R0/R1 与 paired bootstrap，不根据 holdout 结果继续调参。

> 注：检索机制硬门禁来自确定性 fixture / 同一冻结 DB paired replay；端到端优化证明来自预注册的一次性 factorial holdout，而不是任意两次独立 LLM baseline 的分差。

## 噪声与单次 vs 多次

抽取链路是真实 LLM、有抖动；同一份代码 + 同一份数据多跑几次，macro 也会有几个百分点级别的浮动。完整 baseline 仍不自动多跑取均值；`--stability-probe` 只隔离重复少量固定 fragment，用于判断模型输出 / 解析是否稳定，不代替全量 AC。

实际使用建议：

- **小变动 / 直觉不确定**：手动跑 N=3 次（建议 N≥3），人工记录三次 macro 取均值，再判断 delta 是否真实。
- **想要确定性更强**：在 runtime_config 当前配置中设置 provider 参数（legacy-dotenv 模式下可继续用 `.env`），或在 `--note` 里明示这次跑的温度设定，减少抖动。**baseline 文件的 `run.provider.defaults.temperature` 字段会忠实记录**。
- **小幅 delta（< 5 个 percent）默认归因到噪声**：MVP 阶段对"真有变化"的判断保持谨慎，不基于单次差异下结论。

## 关于 PerLTQA 的非商用授权

PerLTQA 的原始数据集走 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)，**仅限非商用研究**。本项目本身就是非商用研究 / 兴趣项目，不会触碰这个边界，但仍要明确：

- **PerLTQA 原始 JSON（`memory_eval/data/perltqa/`）不入 git**，由使用者按上面"数据集"段的链接自行下载。
- **baseline 文件中会包含 PerLTQA 的少量片段**（每题的 `question` / `answer` / `anchors`，最多几十题级别的引用），属于研究性引用范畴，附带本属性归属（"数据来自 PerLTQA, CC BY-NC 4.0"）。**不要把这套 baseline 用于商用模型训练或商用产品评测**。
- 如果未来 limit 提到几百 / 几千题级，需要重新评估是否构成实质性数据集再分发，并在 `baselines/README.md` 加更明确的法律声明。

## 测试

```bash
./scripts/check/run.sh   # 包含 memory_eval 单测（fake LLM，不触发真实调用）
```

memory_eval 自身的测试还覆盖：一次抽取复放三种检索模式、hybrid fail-closed、workspace 兼容校验、逐 fragment attempt checkpoint、稳定性 / 配对 probe 配置锁与 resume、factorial corpus 重建、paired bootstrap 门禁和 baseline trace schema。
