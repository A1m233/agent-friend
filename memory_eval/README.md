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
│              # + report（控制台展示 + macro 汇总）+ baseline（落 JSON 归档）
└── __main__.py # CLI 入口
```

## 数据集（不入 git）

数据文件较大，**不纳入版本控制**（被根 `.gitignore` 的 `data/` 规则忽略）。按需下载：

- **PerLTQA**（默认）：从 <https://github.com/Elvin-Yiming-Du/PerLTQA> 的 `Dataset/zh/` 下载 `perltmem.json` + `perltqa.json`，放到 `memory_eval/data/perltqa/`（CC BY-NC 4.0，仅非商用研究，勿分发 / 勿用于商用训练）。
- **LoCoMo**：从 <https://github.com/snap-research/locomo> 下载 `data/locomo10.json`，放到 `memory_eval/data/locomo10.json`。

## 运行

> ⚠️ **会触发真实 LLM 抽取调用**（每段对话一次抽取）。按项目 `llm-api-confirm` 规则，运行前需获授权，并在项目根 `.env` 配置 `DEEPSEEK_API_KEY`。单测用 fake LLM，不触发真实调用。

```bash
# 默认 PerLTQA（中文）+ AnchorRecallJudge
./scripts/memory-eval/run.sh --limit-samples 3 --limit-questions 10
# 英文基线 LoCoMo + NoopJudge（不打分，仅展示召回）
./scripts/memory-eval/run.sh --dataset locomo --limit-samples 1 --limit-questions 5
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--dataset` | `perltqa` | `perltqa`（中文，dialogues 子集）/ `locomo`（英文基线） |
| `--limit-samples` | `1` | 只跑前 N 个样本（控制成本） |
| `--limit-questions` | `5` | 每个样本只问前 N 个问题 |
| `--model` | `.env` 默认 | 覆盖抽取用 model |

## 跑出来会得到什么

1. **控制台逐题输出**：问题 / 标准答案 / 召回到的记忆条目 / 判分（PerLTQA 显示 `hit/total` + 未命中 anchor 清单）。
2. **跑完汇总**：macro 平均 + 0 分错题完整清单（含 anchor 与召回条目，便于复盘"为什么完全没召回"）。
3. **baseline 文件落盘**：`memory_eval/baselines/<ISO-datetime>-<short-sha>.json`，含 macro 平均 + 每题分数 + 运行条件（commit / 模型 / limit 参数 / 时间）。

baseline 文件**随代码入 git 归档**，作为可在历史上溯源、跨设备一致的对比基线。字段语义见 [`baselines/README.md`](baselines/README.md)。

## 改 memory 时怎么用 baseline

1. **改之前**：跑一次评测，记下 macro（如 `0.133`）；baseline 文件已自动入仓。
2. **改 memory 代码**：抽取策略 / 召回排序 / 向量召回 / reflection 等。
3. **改之后**：再跑一次评测；新 baseline 文件自动入仓。
4. **对比**：`git log -- memory_eval/baselines/` 看历次 baseline；macro 是涨是跌一目了然；逐题分数能定位"哪些题从对变错 / 从错变对"；v2 schema 还存了**完整召回内容**，能逐题对照"召回到的具体记忆"是怎么变的。

> 注：baseline 不是 CI 门禁 —— 真实 LLM 调用、成本与稳定性都不适合做强制门禁。它是**改动证据**，不是**合并卡口**。

## 噪声与单次 vs 多次

抽取链路是真实 LLM、有抖动；同一份代码 + 同一份数据多跑几次，macro 也会有几个百分点级别的浮动。这套工具**不内置自动多跑取均值**——保持 baseline 模块的简单。

实际使用建议：

- **小变动 / 直觉不确定**：手动跑 N=3 次（建议 N≥3），人工记录三次 macro 取均值，再判断 delta 是否真实。
- **想要确定性更强**：把 `.env` 的 provider 参数设 `temperature=0`，或在 `--note` 里明示这次跑的温度设定，减少抖动。**baseline 文件的 `run.provider.defaults.temperature` 字段会忠实记录**。
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

memory_eval 自身的测试覆盖：基准解析（`test_perltqa.py` / `test_locomo.py`）、observe→retrieve 端到端（`test_adapter.py`）、AnchorRecallJudge 行为（`test_anchor_judge.py`，含 sanity case）、baseline schema 锁定（`test_baseline.py`）。
