# memory_eval

`agent-friend` 的**记忆召回质量评测**（当前为 PoC）。

## 定位

把外部 agent-memory 基准的对话历史灌进 `memory` 的公共接口（`Memory.observe`），再用基准
问题走 `Memory.retrieve`，观察召回质量。

边界：本包是 `memory` 公共接口的**消费方**，**单向依赖** `memory` + `llm_providers`，
绝不反向污染 `memory` 核心包的依赖（数据加载、judge 等评测专属依赖只落在这里）。

## 支持的基准

| 基准 | 语言 | 取用范围 | 为什么 |
|---|---|---|---|
| **PerLTQA**（默认） | 原生中文 | 仅 **dialogues 子集** | 对话流 + 真实时间戳，与 memory「从对话抽取→召回」范式严丝合缝；profile/关系/events 是结构化记忆、无对话来源，故不取 |
| LoCoMo | 英文 | 全量 QA | 英文基线，便于对照中英差异 |

> 实测发现：用英文 LoCoMo 测中文 memory 时，「英文 query × 中文记忆」会让 FTS5 关键词召回
> 严重失效，分数不反映真实能力——故默认走原生中文的 PerLTQA。

## 现状（PoC scope）

- ✅ PerLTQA（中文，dialogues 子集）/ LoCoMo（英文）数据加载与归一化（`datasets/`）
- ✅ EvalCase → `observe`/`retrieve` 适配（`adapters/`）
- ✅ 跑流程 + 召回内容展示（`harness/`）
- ⏳ **judge 仅留桩**（`NoopJudge`）：真正的召回质量评分需 LLM-as-judge，PoC 先只展示召回内容供肉眼评估，避免引入第二处真实 LLM 调用

## 内部结构

```
src/memory_eval/
├── datasets/   # 基准数据加载 → 统一 EvalCase（与 memory 无耦合的纯数据层）
├── adapters/   # EvalCase → ConversationFragment（observe）；question → retrieve
├── harness/    # runner（编排）+ judge（判分扩展点）+ report（展示）
└── __main__.py # CLI 入口
```

## 数据集（不入 git）

数据文件较大，**不纳入版本控制**（被根 `.gitignore` 的 `data/` 规则忽略）。按需下载：

- **PerLTQA**（默认）：从 <https://github.com/Elvin-Yiming-Du/PerLTQA> 的 `Dataset/zh/`
  下载 `perltmem.json` + `perltqa.json`，放到 `memory_eval/data/perltqa/`
  （CC BY-NC 4.0，仅非商用研究，勿分发 / 勿用于商用训练）。
- **LoCoMo**：从 <https://github.com/snap-research/locomo> 下载 `data/locomo10.json`，
  放到 `memory_eval/data/locomo10.json`。

## 运行

> ⚠️ **会触发真实 LLM 抽取调用**（每段对话一次抽取）。按项目 `llm-api-confirm` 规则，
> 运行前需获授权，并在项目根 `.env` 配置 `DEEPSEEK_API_KEY`。单测用 fake LLM，不触发真实调用。

```bash
# 默认 PerLTQA（中文）
./scripts/memory-eval/run.sh --limit-samples 1 --limit-questions 5
# 英文基线 LoCoMo
./scripts/memory-eval/run.sh --dataset locomo --limit-samples 1 --limit-questions 5
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--dataset` | `perltqa` | `perltqa`（中文，dialogues 子集）/ `locomo`（英文基线） |
| `--limit-samples` | `1` | 只跑前 N 个样本（控制成本） |
| `--limit-questions` | `5` | 每个样本只问前 N 个问题 |
| `--model` | `.env` 默认 | 覆盖抽取用 model |
