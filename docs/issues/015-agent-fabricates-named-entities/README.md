# 015 · 用户提到具体作品/产品/梗时，agent 在 web_search 之前/之后都会编造细节

- **状态**：resolved
- **严重度**：medium
- **发现日期**：2026-06-16
- **关联需求**：[005-engine-tool-calling-and-web-search](../../requirements/005-engine-tool-calling-and-web-search/)（`runtime_context.md` 是 005 起引入的）

## 现象

session `23fe5835-0dde-488e-b3e4-687f20fd3868`（`~/Library/Application Support/agent-friend/sessions/`，第 57–61 行）记录了一轮典型表现：

用户问 _"我在想，是不是玉桂狗宝宝变身app这个视频里的"_，agent 在 `web_search` 调用**之前**就先输出一段确信的"产品介绍"：

> 哦！玉桂狗宝宝变身app！那个确实火过一阵子——就是一个滤镜App，能把人或者宠物变成那种萌萌的"婴儿版"玉桂狗风格。

事实层面这段几乎全部是编造：

- "确实火过" — 模型无依据
- "就是一个滤镜App" — 实际是三丽鸥"玉桂狗小动画第50话"，剧名叫"变身app"，**不是真的有这么个 App**
- "能把人或者宠物变成婴儿版玉桂狗风格" — 完全虚构产品功能

随后 `web_search` 返回 5 条搜索结果（top1 抖音转载提到"玉桂狗小动画第50话（变身app），转自YouTube频道，原音频@三丽鸥/肉桂"，剩下几条与 query 弱相关）。agent 在第二轮回复里**没有发现自己前文与搜索结果矛盾**（搜索说是"动画"，前文说是"App"），反而把搜索片段拼回原本编造的框架：

> 你说的"欧卡酱噶哦洗诶得，以隆诺米待。。。多类多类"很可能就是那段短动画里玉桂狗或者某个小角色的日语配音台词。配上这种软软的小女孩声音说"妈妈教我~让我看看一郎的~哪个哪个~"，确实是那个味儿。原版音频应该就是从三丽鸥官方那个变身App宣传小动画里截出来的。

其中：

- "**配音台词**" / "**软软的小女孩声音**" / "**宣传小动画里截出来**" 全是编造——搜索结果只给了标题和转载来源，没给台词、声音类型、用途
- "宣传小动画" 把第一轮编的"App"延续了下来——让两轮看起来前后呼应

## 根因初判

### prompt 层

`agent/src/agent/prompt_sections/runtime_context.md` 现行规则只约束**是否触发搜索**：

> 如果你**不能 100% 确定**，优先用 `web_search` 验证后再回答

它**没有**约束三件事：

1. **搜索之前的 prose**：模型可以在调 search 的同一轮先写一段"我以为的产品介绍"。规则字面上"调了 search"就算合规——但用户已经看到一段编造内容，且这段内容会进 conversation history 被后续轮次复用
2. **搜索结果与前文矛盾时的修正义务**：模型倾向于把搜索结果"拼接"进既有 prose，让矛盾**看起来**消失，而不是承认前文错了
3. **从搜索结果"推理"未提及内容的边界**：模型把搜索的标题/片段当作起点，去补充剧情/台词/性质等结果里没出现的细节

### 工具描述层

`web_search` 工具结果 footer（`agent/src/agent/tools/builtin/web_search/tool.py:200-203`）：

> 请基于以上信息**用自己的话回答用户**。如果引用了具体内容，用拟人化方式提及来源……

"用自己的话" + persona 要求"自然像朋友" → 模型把搜索结果当**素材**而非**事实约束**，去拼一段更自然的话。自然的代价是脱离结果原文。

## 影响与判断

- **直接信任伤害**：用户把"虚拟朋友"当作可以闲聊作品/梗的对象，agent 编造产品功能/台词/背景，体感是"被骗"
- **复读风险**：编造内容进 conversation history 后会被后续轮次复用（与 [issue 006](../006-bedtime-prompt-history-hijack/) 类似的 history-hijack 模式——错误自我强化）
- **场景普遍**：不止"玉桂狗"这一个 case；凡用户提到 agent 训练数据外的具体命名实体（视频/梗/App/人物/产品/番剧）都可能触发
- **机制无误**：`web_search` 触发了、调用成功了、结果被整合了——`runtime_context.md` 现行规则字面上都满足，但**输出层面已经误导**

## 可选修复方向

详细解决方案见同目录下 [`solution.md`](./solution.md)（参考了 Claude Code 同类场景的 prompt 处理模式，归纳成三条 prompt 改动 + 一条工程延伸建议）。

要点摘要：

1. 把 `runtime_context.md` 现行的"不能 100% 确定时优先 search"改成更硬的"已验证 vs 推测"二分（CC 的 `tengu_verified_vs_assumed` 模式）
2. 新增"调 web_search 时禁止预言结果"段（CC 的 `Don't race` 模式）——搜前 prose 只能是状态信息
3. 调整搜索结果整合纪律——只引用结果里出现过的字段；矛盾时承认前文错了
4. （可选 / 后续）加一组评测 fixture 作回归基线，对应 CC 的 `tengu_fabricated_turn_candidate` 那种"埋点不阻断"的轻方案思路

## 备忘

- **不在工程层加 fabrication 检测**：CC 也是把"防事实编造"全交给 prompt 解决；它 binary 里的 `looksFabricated` 系列函数针对的是 prompt-injection 风格的伪造 turn 边界（如模型自己写 `Human:` / `<dm `），不防事实幻觉
- 与 [frontend-ui-conventions.mdc] / [coding-design.mdc] 等不直接相关；属 prompt 工程纪律

## 修复指向

已修复——`agent/src/agent/prompt_sections/runtime_context.md` 按 [`solution.md`](./solution.md) 落码。改动覆盖三处：

1. 通用原则段 `## 报告纪律：已验证 vs 推测`（新增）——区分"已验证 vs 推测"的报告纪律
2. 工具前段 `## 调 web_search 时`（新增）——搜前 prose 只能是状态信息，不预言结果
3. 工具后段 `## 整合搜索结果时`（扩展）——引用纪律 + 矛盾修正 + 结果没命中时禁 fallback 编造

经 v1-v5 共 5 round replay 验证（每 round v4-flash×3 + v4-pro×3，复现脚本 [`replay.py`](./replay.py)）：

- post-search Class A fabrication（"是 X 类型 / 那种通常 Y"等类别编造）大幅减少
- pre-search 编造在 v4-pro 上压住率 ~80%，v4-flash 上仍偶发（30-70% 区间）——属模型 compliance 局限，强化 prompt 反触发回归（已留档于 `solution.md` §2.5-2.8）

**没解决但留 follow-up**：

- v4-flash compliance 限制——独立产品决策（要不要升 v4-pro 作默认），不在本 issue 范围
- 评测 fixture 作回归基线——本 issue 沉淀的 v1-v5 失败样本可作起点
