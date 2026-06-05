# Exploration · Memory

> 沉淀"高级记忆系统"方向的多轮讨论。本文件是当前快照，会随讨论推进追加 / 修订。
>
> 与项目愿景的关系：
> - 对应 [`docs/decisions/0001`](../../decisions/0001-product-vision-and-roadmap/README.md) 的 **M2 · 基础记忆**（需要落地）+ **M9 · 强记忆系统升级**（演进方向）
> - 本 exploration 服务于"M2 怎么落 + 朝 M9 怎么演进"两个问题
>
> 当前快照：2026-05-25 · 首轮讨论沉淀

---

## 1. 用户什么时候需要记忆（需求侧锚点）

后续所有设计决策都应回到这一节验证。记忆使用场景分四类：

### 1.1 显性引用（用户嘴里说出来要 agent 想起）

- "上次你说的那个..." / "我之前提过 X 没？" / "你还记得 Y 吗？"
- **对召回要求**：高精准。用户给了明确锚点，找不到 = 体验大破。允许"我记得是...但具体细节模糊"，**不允许**"我没有印象"（除非真没有）

### 1.2 隐性应用（用户没明说，但希望 agent 自动带上下文）

- 用户说"今天又见到 Tom 了" → agent 该知道 Tom 是用户的猫
- 用户问"晚上吃啥推荐" → agent 该想起"用户讨厌香菜"
- **对召回要求**：宽召回 + 自动注入。靠 agent 自己捞。**召回失败代价较小**，**捞错代价高**（把不相关的事塞进来，反而像"突然多嘴的 AI"）
- **这是最高频的记忆使用方式**，决定了形态选择（见 §3.3）

### 1.3 连续感（让 agent 感觉"一直是同一个朋友"）

- 新会话开场不能像"我们刚认识"
- 隔久没聊后开场应自然带"好久没聊了" / "上次咱们聊到 X"
- 反复出现的主题，agent 心里应有"哦又来了"的底色
- **对召回要求**：时间维度强。pinned identity 总是注入 + episodic 按时间衰减

### 1.4 维护写入（系统自动触发）

不是"用户需要"，是写入路径：
- 新事实 → 抽取入库
- 状态更新（"我换工作了"）→ 冲突检测 + 更新
- 强情感事件 → 高重要性入库
- 用户纠正 → 覆盖错记

### 1.5 反推：每类场景靠哪层

| 场景 | 主要靠哪层 | 召回策略 |
|---|---|---|
| 显性引用 | episodic + semantic | 高精准、可按关键词 / 时间限定 |
| 隐性应用 | semantic + pinned | 宽召回、相似度 × 重要性排序 |
| 连续感 | pinned + episodic（时间） | pinned 总是注入；episodic 按时间衰减 |
| 维护写入 | 全部 | 不是召回路径，是写入管线 |

→ 反过来证明：episodic / semantic / pinned 这三种确实需要分别处理，不是"一个向量库一把梭"能解决的。

---

## 2. 主流方案与"朋友 AI"的差距

### 2.1 主流方案简表

| 方案 | 核心思路 | 对"朋友 AI"的差距 |
|---|---|---|
| **裸 RAG / 向量记忆** | 对话切块 → 嵌入 → 相似度检索 | 无结构、无时间、无重要性；噪声大 |
| **MemGPT / Letta** | OS 分页式：主上下文 + 召回库 + 归档库，LLM 自管 | 偏长上下文助理；自管负担重；不天然区分重要性 |
| **Mem0** | 抽取事实 → 去重合并更新 → 相关性 + 时间召回 | 工程化最接近 production；但偏 fact-centric，**情感语境 / 共同经历**覆盖弱 |
| **Zep / Graphiti** | 时序知识图谱：实体 + 关系，带 valid_from/until | 关系查询强、状态变迁清晰；图谱抽取贵；模糊记忆塞不进 |
| **Generative Agents（Stanford 2023）** | 记忆流 + 重要性打分 + 反思循环 | 概念最接近"像真人"；落到聊天应用要解决大量工程问题 |
| **认知心理学分层** | working / episodic / semantic / procedural 分治 | 概念清晰、匹配人类记忆；多数停在论文层 |

### 2.2 三条核心差距

通用方案多为"工作助手"优化（事实准、知识检索），朋友 AI 的差距在：

1. **Salience（什么值得记）**：全记 → 噪声爆炸；只记关键事实 → 失去"朋友的纹理感"
2. **Consolidation（怎么从经历提炼出认知）**：用户提了 5 次猫 → 应沉淀为"喜欢猫"；偶发的"猫去世"应单独高权重保留
3. **时间感**：通用方案对"上次聊到时" / "几周前你提过"的支撑都不强；朋友 AI 离不开这个

---

## 3. 当前已成型的方向

### 3.1 分层模型：working / episodic / semantic

- **Working**：当前会话的上下文（其实就是现有 `Conversation.history`，**不归记忆模块管**）
- **Episodic**：发生过的事件 / 对话片段的摘要，带时间戳、参与者、原文指针、重要性
- **Semantic**：被沉淀出的事实 / 偏好 / 关系（"用户养了一只叫 Tom 的猫"），带 valid_from / valid_until、provenance（指向产出它的 episode）

### 3.2 不单开 Identity 层，用 `pinned` 标记替代

"用户叫小明 / 用户的关键关系"本质都是 semantic 事实。开 identity 层会立刻撞到"哪些算 identity"的归类问题。真正驱动需求只有一条：**这类事实必须每次都进 context**——一个 `pinned: bool` flag 就能解决。

工程上：取上下文时分两路——pinned 全量注入（小池子，几十条上限）；其余 semantic 走"相似度 × 重要性 × 时间"召回。

**唯一会重新考虑开层的情况**：identity 类事实需要完全不同的写入 / 校验 / 冲突解决流程（如"姓名只能有一个"硬约束）——撞到再说。

### 3.3 形态：interface（不是 tool，不是 sub-agent）

agent 编排代码主动调 `memory.retrieve(query)`，结果作为 context 注入。**模型对此无感**——它看到的就是一段 system context，看不出"这是记忆"。

选 interface 的理由按权重排序：

1. **隐性应用需要"默认就召回"**（最重）
   tool 形态要求模型主动决定"我要查记忆"——但模型未必识别出"Tom 是需要查的实体"，**隐性应用会直接失效**。interface 默认每轮就跑一次召回，相关内容自动入 context，模型不需要决定。
2. **persona 不被污染**
   tool / sub-agent 的 prompt 必含"你有 memory 工具"，副作用是模型可能在回复里说出来（"让我查一下我的记忆…"——一秒变 AI 客服），或对"自己有外挂"的认知渗透到表达方式里。interface 形态下模型视角是"这是我的 system context"，没有"工具"概念。
3. **成本和延迟可预测**：interface 每轮固定 1 次；tool 可能 0-N 次（曾见过爆炸到 10+ 的情况）；sub-agent 至少 2-N 次（自带循环）
4. **跟既有契约对得上**：[`001/design.md §6`](../../requirements/001-foundation-chat-and-memory/design.md) 阶段 2 接口预留点本来就是 interface 形态的；改 tool / sub-agent 会破坏已承诺接口
5. **调试 / 观测代价低**：一次输入一次输出，召回了啥一眼看穿；tool / sub-agent 都要 trace 视图

**Tool / Sub-agent 不是不能用，只是不适合做 v1 主路径**。它们的强项（多跳推理 / 深度回忆 / 跨长程聚合）属于"加分项"，不是 v1 必需。

**未来演进路径**：v1 走 interface，未来撞墙时可按顺序加强——(1) interface 本身增强 → (2) interface + 增配工具（默认还是 interface 自动注入，**额外**开 `memory_recall` 工具供深挖）→ (3) interface + 子 agent 委托复杂查询。**v1 走 interface 意味着这三种加强都不需要废 v1 代码**。

> **一句话总结**：朋友的记忆理想效果是"它本来就知道"，不是"它知道怎么去查"——这就是 interface vs tool 的差异。

### 3.4 Episodic ↔ Semantic 转化：v1 只做 Extraction，Reflection 留位

两条转化路径并存设计，但**实现优先级不同**：

- **Extraction（提取）— v1 实现**
  - 时机：每段对话结束后（同步路径，主线程跑）
  - 做什么：LLM 扫这段对话原文 + 已有 episodic → 新增 episodic 摘要 + 新增 / 更新 semantic 事实
  - 单次原子操作，输入输出明确
- **Reflection（反思）— v1 只留 schema 位，不实现**
  - 时机：异步 / 定期（待定）
  - 做什么：LLM 扫多条 episodic 找模式 → 产出更抽象的 semantic（"用户对工作压力反应强烈"）
  - **预留方式**：semantic 表加 `source: "extracted" | "reflected"` 字段
  - 推迟理由：(a) 提取价值确定，必须先做；(b) 反思价值需要"积累一定量 episodic"才显现，v1 不够量；(c) 反思工程复杂度（异步 worker / 触发条件 / 产物去重）不小

### 3.5 存储策略：混合（原始 + episodic 半结构化 + semantic 结构化）

- **原始对话**：本来就在 `data/sessions/` 里（[001 的 JsonlSessionStore](../../requirements/001-foundation-chat-and-memory/design.md)）——是 episodic 的**事实来源**和 **provenance**
- **Episodic**：存"对一段对话的摘要 + 元信息"，**不重复存原文**，存 `session_id + range` 作为指针。需要原文细节时按指针回去取
- **Semantic**：完全结构化（statement + 元信息 + provenance 指向 episode）。是最常被读、最值得索引的层

为什么不"episodic = 直接拿原文"：我们其实不想把整段原文塞进未来召回的 context（贵且嘈杂）。Episodic 是**"对这段经历的认知摘要"**，本身就是一种压缩。

### 3.6 LLM 复用 `llm_providers`，预留独立切换

memory 模块构造时注入 `LLMClient`，**v1 默认与主对话同模型**（DeepSeek，依 [`0002 §3.13`](../../decisions/0002-incubation-tech-stack/README.md)）。

接口层面预留：将来可以给 extraction / reflection / retrieval-rewrite 各自挂不同模型（如 extraction 用小模型省成本），构造时传不同 client 即可。

### 3.7 memory 怎么"看到"对话历史

Memory 需要直接读历史对话（否则没法做 episodic）。**走 `SessionStore` 只读接口**注入，不读文件：

```python
class Memory:
    def __init__(self, session_store: SessionStoreReader, llm_client: LLMClient, ...): ...
```

理由：SessionStore 是 agent 的所有权，memory 只读不写；接口比文件路径稳，未来 session 存储换 backend（JSONL → SQLite → ...）memory 不动。

---

## 4. 接口契约草案

跟 [`001/design.md §6`](../../requirements/001-foundation-chat-and-memory/design.md) 的预留点对照：原 `retrieve(query) -> list[Message]` 扩展为返回更结构化的 `MemoryContext`（向后兼容，预留 list[Message] 的语义可由 `items` 字段还原）。

```python
class Memory(Protocol):
    def retrieve(
        self,
        query: str,
        *,
        session_id: str | None = None,
    ) -> "MemoryContext": ...

@dataclass(frozen=True)
class MemoryContext:
    rendered: str                       # 渲染好的字符串，可直接塞 system 段
    items: list["MemoryItem"]           # 结构化每条记忆（observability / 调试用）

@dataclass(frozen=True)
class MemoryItem:
    text: str
    layer: Literal["episodic", "semantic", "pinned"]
    source_ref: str                     # episode_id / semantic_id / session_id+range
    score: float                        # 召回得分（排序展示用）
```

**调用方使用约定**：
- 默认只用 `rendered`（接口契约简单，agent 那边数据结构不绑定）
- 调试 / observability 时摸 `items`（哪些被召回、得分多少）—— 服务于 [`001 AC-5`](../../requirements/001-foundation-chat-and-memory/requirement.md)
- `ContextManager` 把 `rendered` 当一段 system 内容注入，不需要理解内部结构

---

## 5. 已定的几条小原则

按"未来代码层会落到这里"维度记录，方便实现时回查：

| 原则 | 表现形式 |
|---|---|
| user-said 比 agent-said 写入权重高一档 | 写入 episodic / semantic 时给 user-said 一档基础重要性加成；具体公式待 §6.2 定 |
| 召回失败 → memory 如实返回，agent persona 自己挑话术 | memory 只返回"找到了 / 模糊匹配 / 没找到"，**不编不藏**；坦诚类话术（"我有点记不清了"）由 persona 那边的 fallback 池负责 |
| schema 预留位 | `id` / `pinned: bool` / `deleted_at` / `importance: float` / `source: extracted\|reflected` / `valid_from` / `valid_until` / `provenance: list[ref]` 从 v1 备好 |
| inspect / forget API 暂不开 | "用户翻 agent 的记忆 / 让 agent 忘掉某事"未来必做（信任、隐私、纠错），但 v1 不暴露方法；schema 字段为它预留即可 |
| 冷启动不归 memory 管 | 第一次见面怎么自我介绍 / 怎么问基础问题，是 persona / orchestration 的事，不在 memory 职责内 |
| agent 是否记得自己说过的话 | 记得，但权重低于用户说的 |
| 多 user / 多 persona 留扩展位 | schema 加 `owner_user_id` + `persona_id` 两列，默认填固定值；查询锁死单一 (user, persona)；v1 不暴露多 user / persona 行为 |

---

## 6. Still Open（按优先级，下次讨论从这里挑）

每条标注了"不收敛会卡到什么"。

1. **多 persona 时 semantic 跨 persona 共享 vs episodic 按 persona 隔离的粒度**
   倾向：semantic（user 维度事实）跨 persona 共享 + episodic（"我说过的话"）按 persona 隔离。
   卡点：不定这条，schema 没法画。
2. **召回打分公式**：relevance × importance × time decay 的具体形式（参考 Generative Agents 公式但要简化）
   卡点：不定，"宽召回 + 隐性应用" 的体验调不准。
3. **重要性打分由谁来打**：LLM（贵但准）/ 规则（便宜但糙）/ 混合
   卡点：影响 extraction 管线成本和质量。
4. **schema 具体字段**：在 §5 预留位基础上敲定具体列名 / 类型 / 索引
   卡点：要落代码前必须定。
5. **Reflection 启动时机**：v1 已确定不实现，但需要回答"什么信号触发要开始做反思"——episodic 累积量？时间间隔？用户主动触发？
   卡点：不急，但定了能让 schema 的 `source` 字段更稳。
6. **Embedding 策略**：是否启用向量检索；若启用，用 sqlite-vec（轻量）还是 Chroma（[0002 §3.16 预备栈](../../decisions/0002-incubation-tech-stack/README.md)）
   卡点：影响"宽召回"的实现路径，但可以先用关键词 / FTS 跑通再加。
7. **Extraction 的具体 prompt 设计**：怎么让 LLM 又准又稳地从对话里抽事实
   卡点：跟主对话 prompt 隔离设计，是工程细活。

---

## 7. 跟下游文档的衔接

- **当某条 Still Open 收敛到能拍板时**：
  - 属于 v1 范围 → 写到 [`001/design.md`](../../requirements/001-foundation-chat-and-memory/design.md) 的记忆章节
  - 属于跨需求方向 → 评估是否新建 decision（如"记忆模块对 agent 模块的耦合度上限"这类项目级原则）
- **M9（强记忆系统升级）正式立项时**：新建对应 `docs/requirements/0XX-strong-memory-upgrade/`，其 `requirement.md` 应引用本 exploration 作为思考过程，但**结论 single source of truth 必须在 requirement / design 里**
- **本文件本身**：随讨论推进追加 / 修订；不强制"出栏台账"格式（参见 [`../README.md`](../README.md)）
