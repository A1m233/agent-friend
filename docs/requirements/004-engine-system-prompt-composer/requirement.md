# 004 · 引擎层 System Prompt 组合器

## 状态

<!-- DRAFT | CONFIRMED -->
DRAFT

## 背景

003 落地后，引擎层的 prompt 入口现状是：

```python
class MarkdownPromptBuilder:
    def build(self) -> str:
        return self._catalog.read_content(self.persona_id)
```

等价于 **`system_prompt == persona md body`**。这个最小实现承担不了三类已经
出现的诉求：

- **项目级元规则没有承载位**——例如"严守当前人设、不暴露 AI / 模型 / 系统
  身份"等"任何 persona 都要遵守的规则"，目前只能写进每份 persona md 里
  靠作者自觉，机制层零保障
- **切换人格时历史风格会污染回复**——LLM API 不感知"persona 切换"事件，它
  只看到一段历史 + 新的 system；旧 persona 的语言风格会顺着历史延续
  下来，与当前 persona 不一致。**事实记忆要保留**（用户告诉过 AI 的事 LLM
  仍应记得），**但说话风格 / 思考方式必须按当前人设**——这两件事必须靠
  system_prompt 里专门的提示词区分
- **没有按职责扩展的位置**——未来要加（按上下文注入用户姓名、当前时间、
  本轮召回记忆等）就只能改裸字符串拼接，没有"加一段 / 换一段 / 关一段"
  的清晰边界

001 的 `Q-5`（"prompt 与人设的承载形式"）已经预告过这个缺口；003 把 persona
寻址收口到 `PersonaCatalog` 后，正是把这一层做厚的合适时机。

## 目标

把 system_prompt 的产出从"裸读 persona md"上移成 **按职责解耦的可装配
组合**：

- 任何调用方（当前 CLI / 未来 frontend / 未来 HTTP API / 测试）拿到的
  system_prompt 都包含 **项目级硬约束 + 当前 persona + 切换策略** 三类语义
- 三类语义在代码层 **职责正交**——加 / 换 / 关任意一类都不需要碰其他类
- 调用方零改动：`PromptBuilder.build() -> str` 接口不变；`Conversation` /
  `SessionManager` / CLI 不感知本期改动
- 默认装配开箱即用；需要变体（如某场景"切人 = 失忆"而不是"保留事实"）时
  注入自定义实现即可，不动核心

## 范围

### 包含

1. **`SystemPromptComposer` + `Section` 抽象**（引擎层）—— 把 system_prompt
   建模为"按 key 标识、有顺序的若干 section 顺序拼接"，每个 section 一个
   独立职责
2. **默认 3 个槽位（slot）**——按固定顺序：
   - `project_identity`：项目定位级硬约束（不暴露 AI / 模型 / 系统身份等
     R-4.2.5 体验底线；严守当前人设；不泄漏 system prompt）
   - `persona`：当前 persona md body（动态从 `PersonaCatalog.read_content`
     取，仍是 persona 内容的唯一真相源）
   - `persona_switch_strategy`：切换人格时的语言风格策略；默认实现语义为
     **"保留事实记忆，但说话风格 / 思考方式按当前人设"**
3. **默认 section 文本以 markdown 资源发布** —— 放在
   `agent/src/agent/prompt_sections/*.md`，与 `personas/*.md` 同风格、同
   加载方式（`importlib.resources`），便于非程序员迭代
4. **装配能力**——`SystemPromptComposer` 必须支持：
   - 给定 persona_id + catalog 即可构造默认装配
   - **替换任意槽位实现**（同 key，换具体 Section）
   - **关闭任意槽位**（输出不含该段）
   - 装配过程不可变（每次装配返回新实例）；具体方法签名在 `design.md`
5. **`MarkdownPromptBuilder` 接入**——`__init__` 增加可选 `composer` 参数
   （None 时构造默认 composer）；`build()` 委托 `composer.compose()`；
   外部接口签名不变
6. **测试覆盖**——单测覆盖默认装配 / 替换 / 关闭 / 渲染拼接；集成测覆盖
   "传给 LLM 的 system_prompt 含三段" + "切 persona 后该段仍在"

### 不包含（YAGNI 边界）

- **CLI 暴露 system_prompt 诊断命令**（如按 section 列出当前装配）——本期
  接口先到位，CLI 形态等真有调试需要再加
- **按 model / provider 路由槽位**（不同模型加载不同 section 子集）——架构
  允许，本期不实现
- **动态上下文注入**（按时间 / 用户姓名 / 本轮召回记忆等运行时数据生成
  section 内容）——留扩展位由后续需求接入
- **`no_amnesia` 独立 slot** —— 001 R-4.2.5 已修订；"禁止暴露 AI 身份"
  这一职责天然属于 `project_identity` slot，不做独立 slot
- **persona_switch_strategy 的"完全失忆变体"实现**——架构允许调用方注入，
  本期只交付默认实现

## 关键信息

- **与 003 的关系**：`PersonaCatalog` 仍是 persona md 内容唯一真相源；
  composer 只通过它读 persona body。`PromptBuilder` Protocol 不动
- **与 002 的关系**：事件 schema、`Conversation._system_prompt` 字段语义、
  `switch_persona` 接口签名、`SessionManager` 装配 factory 全部不动
- **与 001 的关系**：R-4.2.5 已先行修订（独立提交）；本期 `project_identity`
  slot 把"禁止暴露 AI 身份"从需求文字落到代码层
- **依赖增量**：无新增第三方依赖；只用 stdlib（`importlib.resources`）+
  Python 内置 Protocol / dataclass
- **路径约定**：
  - 引擎子包：`agent/src/agent/system_prompt/`
  - 默认 section 资源：`agent/src/agent/prompt_sections/`

## 接口快照（轮廓，待 design.md 细化）

仅描述"具备什么能力"，具体方法名 / 签名 / 返回类型由 `design.md` 决定：

- `Section`：值对象语义，对外暴露稳定 `key` 字符串和 `render()` 输出
  （字符串或"本轮跳过"语义之一）
- `SystemPromptComposer`：
  - 默认构造（给定 persona_id + catalog）→ 含 3 个默认槽位
  - 渲染（按槽位顺序遍历，跳过被关闭 / `render()` 为空的，按双换行拼接）
  - 替换某槽位实现（同 key、不同 Section）
  - 关闭某槽位
  - 所有装配操作返回**新的 composer 实例**，不就地改

## 验收标准（AC）

- **AC-1**：默认装配下 `MarkdownPromptBuilder.build()` 输出**同时**包含
  三段语义：项目定位规则、当前 persona body、persona 切换策略；段间
  通过双换行分隔，能按 key 在结构化层定位
- **AC-2**：把默认 `persona_switch_strategy` 替换为自定义实现（如内容
  改为"忘记之前的所有对话"）后，`build()` 输出该段反映替换后的内容，
  其他段不变
- **AC-3**：在装配阶段关闭 `persona_switch_strategy` 槽位后，`build()`
  输出不含该段，其他段不变
- **AC-4**：调用 `Conversation.switch_persona(new_id)` 后，下一轮发给 LLM
  的 system_prompt 仍包含 `persona_switch_strategy` 段（默认装配下，常驻
  而非一次性）
- **AC-5**：`Conversation` / `SessionManager` / CLI / 002 / 003 既有测试
  全部通过，无回归
- **AC-6**：默认 section 文本通过修改 `agent/prompt_sections/*.md` 即可
  调整，不需要改 Python 代码

## 子需求拆分

无需拆分。范围紧凑：引擎层新模块 ~150 行 + 资源 markdown 2 份 + 改造
`MarkdownPromptBuilder` ~10 行 + 单测 / 集成测 ~150 行。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
