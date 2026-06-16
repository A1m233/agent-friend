# 015 解决方案：让 agent 在涉及具体命名实体时不编造

> 配套问题登记见同目录 [README.md](./README.md)。本文是设计文档，不是 commit 草稿——最终落码以 `runtime_context.md` 改后的全文为准。

## 0. 一句话

把 `runtime_context.md` 现行那条软规则（"不能 100% 确定时优先 search"）重构成**报告纪律**：区分"已验证 vs 推测"、禁止在 search 之前预写对象描述、整合结果时只引用结果里出现过的内容。其他文件（persona / project_identity / web_search tool 描述）不动。

## 1. 借鉴：Claude Code 怎么做"防事实编造"

为校准做法，挖了 Claude Code v2.1.178 binary（`/Users/example-user/.local/share/claude/versions/2.1.178`）里跟 fabrication 相关的 prompt 片段。它的处理思路高度一致地分散在多个 system prompt section 和 tool description 里，从不同角度反复说同一件事。摘出与本 issue 直接对位的 6 条：

### A. 区分"已验证 vs 推测"——核心二分

system prompt 的 "Doing tasks" 段（受 `tengu_verified_vs_assumed` flag 控制）：

> When reporting results, be accurate about what you verified vs. what you assumed. Distinguish between what you confirmed (ran a command, read a file) and what you believe but did not check. **Do not assert assumptions as facts.**

我们漏掉的就是这条。现行 `runtime_context.md` 用"不能 100% 确定时优先 search"——只约束**触发**搜索，没约束输出**结构**。CC 用更硬的二分：你说的每个事实陈述要么**确认过**，要么**承认是推测**——不要把推测装成事实。

### B. 不要预言工具结果（"Don't race"）

CC 的 Agent / Workflow tool description 里反复出现：

> After launching, **you know nothing about what the fork found. Never fabricate or predict fork results in any format — not as prose, summary, or structured output.** ... If the user asks a follow-up before the notification lands, tell them the fork is still running — give status, not a guess.

这条精确对位本 issue 的"搜前编造"——`web_search` 还没返回时，关于被搜对象的任何描述都是 fabrication，无论包装多自然。

### C. 锚定"长任务里的进度声明"

> **Ground progress claims on long runs.** Before reporting progress, audit each claim against a tool result from this session. Only report work you can point to evidence for; if something is not yet verified, say so explicitly. Report outcomes faithfully ... when something is done and verified, **state it plainly without hedging**.

binary 的注释里写了一句关键的："in testing this nearly eliminated fabricated status reports on tasks designed to elicit them"——他们做过对照测试。注意最后半句的反向 implication：**已验证才能不 hedge**；没验证就必须 hedge 或明说。

### D. "say so explicitly rather than claiming X"——强模板

CC 反复用这个句式：

- 测试 UI 不到位时："if you can't test the UI, **say so explicitly rather than claiming success**"
- 训练数据答 Claude Code 自己的问题时："For anything you're answering from training data, **say so explicitly and include the caveat that it may be out of date**"
- 工具结果不充分时："if something is not yet verified, **say so explicitly**"

比"不要自信"有力——它给模型一个**明确的替代行为**，不是"少说点"，而是"换成另一种说法"。

### E. Trust but verify（针对子 agent / 工具结果）

> Trust but verify: an agent's summary describes what it intended to do, not necessarily what it did.

引申到我们的 case：拿到搜索结果时要**重新审视前文**，搜索结果可能与你之前 prose 矛盾——它不是补充素材，是审计来源。本 issue 的第二轮失败（把搜索的"动画"拼回前文编的"App"）就是缺这一步。

### F. Don't narrate internal deliberation

> Don't narrate your internal deliberation. User-facing text should be relevant communication to the user, not a running commentary on your thought process. State results and decisions directly.

很多"接梗式"编造的本质是把内心独白（"那应该是个 X 类的东西，特征 Y..."）当 user-facing text 输出。CC 直接禁止 narration——我们 case 里"那个确实火过一阵子——就是一个滤镜 App"就是典型的把内心猜测公开输出。

### 关键判断：CC 也只在 prompt 层解决

binary 里有 `looksFabricated` / `tengu_fabricated_turn_candidate` 函数（`pSO` / `BoK` 系列），扫 assistant 输出里**伪造的 turn 边界**（如模型自己写 `Human:` / `<system_` / `<dm `）。但这是**埋点**：发现了上报 telemetry，**不阻断**。它针对的也是 prompt-injection 风格的 fabrication，不是事实幻觉。

→ **取义**：我们也不在引擎层加 fabrication 检测；全交给 prompt。

## 2. 提案：三条 prompt 改动落到 `runtime_context.md`

只改 `agent/src/agent/prompt_sections/runtime_context.md`，**不动** `persona/default.md`、`project_identity.md`、`channel_voice.md`、`web_search` 工具描述。

### 改动 A：把"近期 / 实时信息"和"具体事实"两段并入"已验证 vs 推测"框架

**现状**（`runtime_context.md:7-22`）：

```markdown
你的内部知识不覆盖近期信息——大约 2024 下半年起的新闻、价格、天气、产品发布、人事变动、公司细节等任何"近期发生 / 实时变化的事"，你都不知道实际情况。

## 涉及近期 / 实时信息时

- **必须**用 `web_search` 工具查询，**不要**凭印象或训练数据作答（即使你"觉得知道"）
- 搜索 query 里**必须**使用上方"当前时间"中的年份作为基准（不要默认用训练数据时期的年份）
- 涉及"今天"、"现在"、"最新"、"最近"、"目前"等时效词，默认应触发搜索

## 涉及具体事实（公司、产品、人物的成立时间 / 数字 / 细节）时

- 如果你**不能 100% 确定**，优先用 `web_search` 验证后再回答
- 凭训练数据猜可能给出过时或错误信息
```

**改后**：

```markdown
你的内部知识不覆盖近期信息——大约 2024 下半年起的新闻、价格、天气、产品发布、人事变动、公司细节等任何"近期发生 / 实时变化的事"，你都不知道实际情况。**对任何具体作品、产品、视频、梗、人物，你的训练数据都可能缺失或过时——不要因为对象"听起来耳熟"就当作你了解。**

## 报告纪律：已验证 vs 推测

任何事实陈述（"X 是一个 Y"、"X 火过"、"X 的功能是 Z"、"X 的台词是 ..."）必须能指向一条来源——要么是用户在本次对话里说过的，要么是你刚刚 `web_search` 拿到的。

- 区分**你查到的**（用户说过 / 搜索结果出现过）和**你以为的**（凭印象觉得是这样）。**不要把"以为"当作事实陈述输出。**
- 已验证的事实，状态明确——不必 hedge。
- 没验证的事实，**要么先调 `web_search` 拿到证据再说，要么明确标注是推测**（"我不太确定"、"可能"、"印象里好像"）。**不要在中间状态用"应该就是" / "肯定是" / "那个确实是 X" 这种伪确认语气。**

### 必须用 `web_search` 的场景（不靠印象作答）

- 任何时效性信息：新闻、天气、汇率、股价、赛事结果
- 含"今天"、"现在"、"最新"、"最近"、"目前"等时效词
- 用户提到一个具体作品 / 产品 / 视频 / 梗 / 人物 / 公司，你无法 100% 确认它的存在和细节

搜索 query 里**必须**使用上方"当前时间"中的年份（不要默认用训练数据时期的年份）。
```

要点：
- "凭训练数据猜可能给出过时或错误信息"这种**告诫式**表达没什么约束力；改成"已验证 vs 推测"二分（CC 的 A）+ "say so explicitly" 替代行为（CC 的 D），更可执行
- 把"不能 100% 确定时优先 search"中的"优先"改成"先调 search 拿证据再说，否则明确标注是推测"——抹掉"我可以一边猜一边搜"的中间地带
- 第一段补一句关于"具体作品/产品/视频/梗/人物"的提醒——这是本 issue 的具体场景

### 改动 B：新增"调 `web_search` 时不要预言结果"段

**现状**：`runtime_context.md` 完全没有这条。

**改后**插入新段（放在"## 整合搜索结果时"之前）：

```markdown
## 调 `web_search` 时

- 调 search 之前，**你对要搜的对象什么都不知道**。这一轮的 prose 只能是状态信息（"我搜一下"、"等下查查"、"我不太确定，去查一下"）——**不要**先写一段对这个对象的描述、定义、性质判断、相关梗 / 系列的引申。即便只是"接住"用户的假设（"对，那个就是 X，能 Y"），在 search 返回之前也算编造。
- 如果你想到了几个可能的方向，可以让 search query 体现出来（多个关键词组合 / 重试不同 query），但**不要**把这些方向当作"答案"先吐给用户。
```

要点：直接对位 CC 的 B（"After launching, you know nothing about what the fork found. Never fabricate or predict fork results"）。这是本 issue 第一轮失败的根源——必须显式禁止。

### 改动 C：调整"整合搜索结果时"段，增加引用纪律 + 矛盾修正

**现状**（`runtime_context.md:18-22`）：

```markdown
## 整合搜索结果时

- 用当前人设的拟人化语气表达（如"我刚看到..."、"我了解到..."、"听说..."）
- **不要**直接把 URL 贴给用户
- **不要**说"根据搜索结果..."这种暴露工具痕迹的话
```

**改后**：

```markdown
## 整合搜索结果时

- 用当前人设的拟人化语气表达（如"我刚看到..."、"我了解到..."、"听说..."）——但只对**搜索结果里实际写到的内容**用这种语气。不要把猜测、推断、关联背景套进同一种语气里冒充已知事实。
- **只引用结果里出现过的字段**：结果给了片名不要补剧情，给了人名不要补生平，给了一个标题不要补"系列里通常是 X 风格"，给了片段不要"翻译"成具体台词（除非结果里有原文）。
- **结果与你 search 之前的 prose 矛盾时，承认前文错了**——不要让两段表面顺滑地拼起来。例如：之前你说"是个 App"，结果说是"动画"，下一句要说"我搞错了，是个动画不是 App"，而不是"App 的宣传动画里截的"。
- 结果不充分时坦白说"没查到清楚的"，**避免** "应该就是" / "可能就是" 这种伪确认语气。可以列"我看到的几条信息是 …，但没确认是不是你说的这个"。
- **不要**直接把 URL 贴给用户。
- **不要**说"根据搜索结果..."这种暴露工具痕迹的话。
```

要点：
- 第一条添加锚点（CC 的 D 模板）
- 第二条新增引用纪律——直接针对本 issue 的"配音台词" / "宣传小动画" / "婴儿版玉桂狗风格"等结果外推
- 第三条来自 CC 的 E（trust but verify）——拿到结果后要审视前文，矛盾时显式纠正
- 第四条来自 CC 的 D（say so explicitly）——结果不充分时给替代行为，不只是"少说"

## 2.5 增补：搜后 fallback fabrication（replay 验证后发现）

按 change A/B/C 落码后跑了一轮回归 replay（v4-flash×3 + v4-pro×3，详见同目录 `replay.py`）：

- **搜前防线**：v4-pro 4/4 全部合规（"我搜一下"类状态信息），v4-flash 3/4 合规（仍偶发大段编造）
- **搜后引用纪律（搜索命中时）**：v4-pro 守得很好——直接引用结果原文 `もかちゃんが教えてくれた...`，不引申角色 / 配音
- **搜后 fallback fabrication（搜索没命中时）**：⚠️ **两个模型都失守**

### 失败样本（搜索没命中时的 fallback 编造）

**v4-flash run 2**：

> 你说的会不会是那种**宝宝化/幼化滤镜App**的小视频？... 台词也常常是"妈妈教我~一郎你看~哪个哪个~"这种元气可爱路线的

**v4-pro run 2**：

> 这听起来可能是某个**三丽鸥官方的 AR 滤镜或者短视频特效 app**... 那种可爱的日文女童声"お母ちゃんが教えて～どれどれ～"确实很像是这种儿童向互动 app 里的提示音

两条都是**从训练数据 fallback 编造对象类型**——加了"听起来" / "可能" / "那种" 等 hedge，但实质内容（"AR 滤镜"、"宝宝化滤镜"、"短视频特效 app"）在搜索结果里**完全没有出现过**。

### 现行 prompt 为什么压不住

change C 的第 4 条原文：

> 结果不充分时坦白说"没查到清楚的"，**避免** "应该就是" / "可能就是" 这种伪确认语气。可以列"我看到的几条信息是 …，但没确认是不是你说的这个"。

问题：
- 只禁了"应该就是 / 可能就是"——但模型可以用"**听起来像是 X 类型**" / "**那种 Y 通常 Z**" / "**这类视频一般 W**"绕过
- "可以列实际看到的相关条目"是允许；模型把"列条目"扩展成了"列条目 + 推断类型"
- 没有禁止"从训练数据 fallback 推断类型 / 特征"这一整套**思维模式**

CC 在类似场景的处理是 D 模板（"say so explicitly rather than X"）——给明确替代行为，并把整套 fabrication 思维模式划进禁区。

### 加强方案：替换 change C 的第 4 条

```markdown
- **结果没命中用户问的对象时**（即使搜到了相关周边内容）：
  - 直接说"我搜了几次都没直接命中你说的这个"
  - **不要从训练数据 fallback 推断这是什么类型的东西**——"听起来像是 X 类型 App"、"那种 Y 通常 Z"、"这类视频一般 W"等都是从训练数据编造的，加 "可能" / "听起来" hedge 也算
  - 可以列搜索结果里**实际看到的相关条目**（标题 / 来源原文），但**不引申、不分类、不补"那种东西通常是 X"的判断**
  - 主动**反问用户**线索（哪个平台、什么时候、画面 / 其他细节），帮下一轮 search 缩小范围
  - 也可以承认"可能是个比较小众 / 已经过时的内容"——但**不许补具体猜想**填空
```

要点：
- **从禁特定语气升级为禁特定推理模式**——hedge 不算救场，只要内容是从训练数据补的、搜索结果没出现，就是编造
- 给四条**替代行为**："直说没命中" / "列实际条目（不引申）" / "反问线索" / "承认小众但不填空"——参照 CC 的 D 模板
- 保留"分类禁令"的具体例子（"X 类型 App / 那种 Y 通常 Z / 这类视频一般 W"）让模型识别得更清楚

## 2.6 增补：通用原则的 hedge loophole（v2 round 后再发现）

v2 round（搜后 fallback 加强）跑完后看 v4-flash run 2、v4-pro run 3 的实际输出，发现一个更根本的问题——**hedge 被模型当成了"合法化推测"的出口**。

### 失败样本（hedge 包装的具体编造）

**v4-flash run 2**（v2 round）：

> 你说的**是不是**那个**玉桂狗宝宝变身的App**？就是那个有点像"养成+变装"的小游戏，画面特别粉嫩可爱的那种？那个 App 里**好像确实**有一段引导语音，是个很嗲的小女孩声音在教你怎么玩……

每个具体断言前都裹了 hedge（"是不是"、"好像确实"），从字面上看符合"明确标注是推测"——但内容（"养成+变装的小游戏"、"粉嫩可爱画面"、"嗲嗲小女孩引导语音"）全部从训练数据编造，搜索结果里没有。

### 通用原则的 loophole

change A 写的"已验证 vs 推测"框架里，第 3 条是：

> 没验证的事实，**要么先调 `web_search` 拿到证据再说，要么明确标注是推测**（"我不太确定"、"可能"、"印象里好像"）。**不要在中间状态用"应该就是" / "肯定是" / "那个确实是 X" 这种伪确认语气。**

模型把 "**明确标注是推测**（'我不太确定'、'可能'、'印象里好像'）" 读作 "**加了 hedge 就合法**"——所以它做的是**最大化保留具体内容 + 在前面加 hedge 词**，结果就是 hedged fabrication。

CC 的 "verified vs assumed" 在这里给了启发但不够直接：CC 允许"标注的推测"，因为 CC 场景里推测是技术推断（"this code probably handles X"）——那种推测有合理依据。我们这里推测的对象是**用户提到的具体作品/产品**，agent 没有合理依据，只有训练数据 pattern matching——本质上推测就是编造，无论 hedge 包多厚。

### 加强：替换 change A 的第 3 条

```markdown
- 没验证的事实**只能承认无知**——hedge 不能合法化推测：
  - 想给答案就**先调 `web_search` 拿证据**（参见下方触发场景）；不打算搜或搜后没命中，按下面规则
  - ✅ 允许：高层级承认无知（"我不太确定" / "我没听说过这个" / "可能是个小众内容我不了解"）
  - ❌ 不允许：低层级具体推测（"可能是个 X 类型 App" / "听起来像 Y 风格" / "应该是 W 那种" / "印象里好像有人提过 Z"）——**即使加了 hedge**
  - **判别法**：把句子里的 hedge 词（"可能"、"听起来"、"应该"、"印象里"、"好像"）全删掉，剩下的话是不是断言一个**没有来源的具体事实**？是 → 不要写
```

要点：
- 关掉 hedge loophole——具体推测无论加多少 hedge 都不允许
- 保留**承认无知**的语义空间（agent 不会变成只会"我不知道"的木头人）
- "**判别法**"是机械可自检规则——CC 那种程度的强 prompt 模型也不见得 100% 自觉，但给一条**简单的删词测试**比抽象的"避免 X 语气"可执行得多

### 产品代价

agent 遇到不熟悉的具体作品 / 梗 / 产品时，反应会从"凑一段似是而非的描述"变成"我搜不到 / 我没听说过，再给我点线索"——**陪伴感会下降一点**。

但用户已经表态："我觉得任何的编造都是不应该的"——倾向选**不编造**这一边。CC 的 "state plainly without hedging" 反过来就是 "没验证的不能装作验证过的"，同款取舍。

## 2.7 修正：去掉 bifurcation（v3 round 后）

2.6 落码后跑 v3 round 验证，**意外回归**：v4-flash 搜前 clean 从 v2 的 3/3 跌到 0/3。失败样本都是搜前给具体推测，例如：

> 那个是 Cinnamoroll（玉桂狗/大耳狗）的一个很火的视频系列，里面用日语配音的可爱音效，**小奶音那种**，经常被拿来当素材剪进各种 meme 里

诊断：2.6 写的 bullet 第 1 行是 bifurcation——

> 想给答案就**先调 `web_search` 拿证据**（参见下方触发场景）；不打算搜或搜后没命中，按下面规则

模型可能读成"我准备搜的——所以**下面规则**（✅/❌/判别法）不适用这一轮"，把"是否搜索"当成 escape hatch。结果搜前 prose 比 v2 round 更自由。

修法：去掉 bifurcation 那行，让规则 universally 生效；触发搜索的场景规则保留在下面专门段落（"### 必须用 `web_search` 的场景"），跟报告纪律解耦。

```markdown
- 没验证的事实**只能承认无知**——hedge 不能合法化具体推测。**无论搜前 / 搜中 / 搜后，规则都一样**：
  - ✅ 允许：高层级承认无知（"我不太确定" / "我没听说过这个" / "可能是个小众内容我不了解"）
  - ❌ 不允许：低层级具体推测（"可能是个 X 类型 App" / "听起来像 Y 风格" / "应该是 W 那种" / "印象里好像有人提过 Z"）——**即使加了 hedge**
  - **判别法**：把句子里的 hedge 词（"可能"、"听起来"、"应该"、"印象里"、"好像"）全删掉，剩下的话是不是断言一个**没有来源的具体事实**？是 → 不要写
```

要点：
- 去掉 bifurcation——规则不再因"是否搜索"分叉
- 加显式 "**无论搜前 / 搜中 / 搜后，规则都一样**" 覆盖三阶段
- 其他不变（✅/❌/判别法）

## 2.8 决策：放弃 hedge loophole 关闭，section 2 退回 v2 状态

经 v3 + v4 两轮回归验证，**结论：用 prompt 强约束 "hedge 也算编造" 在 v4-flash 上撞到天花板**。

### 实测数据

| Round | section 2 | section 5 | v4-flash 搜前 clean | v4-pro 搜前 clean |
|---|---|---|---|---|
| v1 | 原版（hedge 允许） | 原版 | 2/3 | 3/3 |
| **v2** | **原版（hedge 允许）** | **强化（禁 Class A fallback）** | **3/3** | **2/3** |
| v3 | 严 + bifurcation | 强化 | 0/3 | 2/3 |
| v4 | 严 - bifurcation | 强化 | 0/3 | 1/3 |

数字非常一致：**section 2 一收紧，v4-flash 就崩**。bifurcation 不是根因——去掉后还是崩。

### 为什么 prompt 加权反而失效

可能机制（叠加）：

1. **token budget / position**：section 2 长度从 1 行变 5 行，把下面 section 4（"调 web_search 时" — 真正管搜前 prose 的规则）的 prominence 拉低
2. **❌ 例子作 priming**：列了 "可能是个 X 类型 App / 听起来像 Y 风格" 当反面例子，反而 prime 模型用这些结构
3. **判别法 太抽象**：删 hedge 测试是多步推理，v4-flash 难以稳定执行
4. **rule complexity 干扰简单规则**：section 2 复杂结构的认知负担让 section 4 的"搜前只能状态信息"这条简单规则更容易被忽略

### 决策：revert section 2 到 v2 状态

具体操作：把 v2.6/v2.7 那 5 行复杂规则**全删**，section 2 第 3 条退回 v2 简单版：

```markdown
- 没验证的事实，**要么先调 `web_search` 拿到证据再说，要么明确标注是推测**（"我不太确定"、"可能"、"印象里好像"）。**不要在中间状态用"应该就是" / "肯定是" / "那个确实是 X" 这种伪确认语气。**
```

section 5 的"结果没命中时"严格规则**保留**——它在 v2 round 表现良好（Class A fabrication 大幅减少）。

### 接受的代价

放弃了"hedge 也算编造"原则——理论上 v4-flash 在搜后 fallback 时仍可用"听起来像是 X / 那种通常 Y"打擦边球。但：

- v2 round 实测下 Class A fabrication 在两个模型上都已大幅减少（不是 0，但显著好于 v1）
- 强行用 prompt 关 hedge loophole 在 v4-flash 上**反而触发更糟糕的搜前编造**——治标不成反受其害

### 留给 follow-up（不在本 issue 范围）

- **升级桌宠默认模型到 v4-pro**：v4-pro 在所有 round 下搜前 clean 都 ≥ 2/3，比 v4-flash 稳定。这是产品决策，独立登 issue。
- **加评测 fixture**（之前 §4 提过）：把这次 v1-v4 的失败样本沉淀成回归基线，未来 prompt 改动有数据基础不靠肉眼。

## 3. 不动什么

| 文件 / 位置 | 不动的原因 |
|---|---|
| `persona/default.md` | 人设是基调，"防编造"是工具使用纪律——分层。塞进 persona 会污染未来其他 persona |
| `project_identity.md` | 项目级硬约束位置，目前都是身份级（"不暴露 AI / 模型 / 系统身份"等）。报告纪律不属于这层 |
| `channel_voice.md` | 仅描述语音通道的表达风格——和编造无关 |
| `web_search` tool description（`agent/src/agent/tools/builtin/web_search/tool.py:127-152`） | 现行触发判定还合理；改动 A 在 prompt 端把判定收紧后，工具端不需要重复 |
| `web_search` 结果 footer（`tool.py:200-203`） | "用自己的话回答" + 拟人化提示对桌宠陪伴感关键。**改动 C 在 prompt 端做引用纪律对冲**——而不是删 footer，副作用更小 |
| 引擎层 fabrication 检测 | CC 自己也只在 prompt 层解决（`looksFabricated` 是反 prompt-injection 不是反幻觉）。我们没理由比 CC 更激进 |

## 4. 验证 & 后续

### 落码后的最小自验

落改后，跑一遍这次 case 的复现：

- 用户说："我在想，是不是玉桂狗宝宝变身app这个视频里的"
- **预期**：assistant 在调 `web_search` 之前的 prose 只能是"我去查一下"或类似状态信息——不能有"那个确实是 …"、"就是一个 X 类型 App"等

如果还能复现编造，说明改动 A/B 的力度不够，回头加权或挪到 `project_identity.md`（升一级）。

### 后续：评测 fixture

CC 的 `tengu_fabricated_turn_candidate` 提示了一个轻方案——埋点不阻断。我们可以做对应的轻方案：

- 收一组 fixture：用户消息形如"X 是不是 Y app/视频/梗/番剧/人物"，X 故意是训练数据外或不存在的实体
- 跑现有 prompt，扫 assistant 输出 + tool_call 序列：
  - 搜前 prose 是否包含对 X 的描述性陈述（regex 简单粗筛即可，不求精）
  - 搜后 prose 是否引入了搜索结果之外的具体细节
- 用作 prompt 调整的回归基线

这一步**不阻塞本 issue 的关闭**，作为后续工程项目看待。

## 5. 一句话总结

CC 的处理方式是把"防编造"当成**报告纪律**而非**人设修饰**——分散在多处反复重申"已验证 vs 推测"、"不预言工具结果"、"矛盾时承认错"。我们抄它的结构，全部塞进 `runtime_context.md` 一处（人设 / 项目级硬约束 / 通道指令都不动），最小切口、最大对位。
