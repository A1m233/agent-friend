# 006 · BedtimeSource 提示词被 conversation history 拽走，产出虚构 user 视角回答

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-06-13
- **关联需求**：[014-engine-main-loop-and-bridge-push](../../requirements/014-engine-main-loop-and-bridge-push/)

## 现象

015 M15.8 端到端真跑中发现：

session 历史末尾是上一轮 agent 提的问题（如"你这是还没睡还是醒了？"），此时触发 BedtimeSource → LLM **没有按 BedtimeSource prompt 主动提醒"该睡了"**，而是**以 user 口吻继续上一轮对话**虚构了一段回答（"还没睡，打了几把游戏，越打越精神了"）；后续每次再 fire bedtime，conversation history 又加了上一轮的虚构内容，LLM 顺着编（"刚打了几把瓦罗兰特（Valorant），之前组了个车队，打到后面就剩我一个人单排了"）。

每次输出在协议层都是合规的 assistant_message（user-visible）—— 015 PetBubble 正确呈现 + chat 窗正确不显示。**机制无误**，但**LLM 输出内容方向跑偏**。

## 根因初判

`DEFAULT_BEDTIME_ADDENDUM`（`agent/src/agent/runtime/sources.py:34`）现文案：

> 现在是约定的休息时间，请按你当前 persona 自然地说一句提醒——口吻温和，不长篇大论，不强迫，让用户感到陪伴而不是被监督。

这段 system_prompt_addendum 注入到 `dispatch_system_turn` 的 trailing system message，但**不够强势**：

1. 没明确指出"无视 conversation history 末尾的对话走向"
2. "按你当前 persona 自然地说一句"被 LLM 误读成"接着上一轮对话自然延续"
3. LLM 看到 history 末尾 agent 提问 `"你这是还没睡还是醒了？"`，把新一轮当成"回答这个问题"
4. 它不知道自己"应该是 agent 角色"，因为没强提示 ——结果模拟 user 视角答复
5. 后续 history 累积虚构内容，错误自我强化

## 影响与判断

- **机制无误**：015 AC-4 / AC-5 都成立（push channel + 气泡呈现 + chat 跳过）
- **产品体验差**：bedtime 提醒的核心价值（"晚了该睡了"）荡然无存
- **会在多种 conversation history 走向下出问题**：不只是"agent 提问" 这一种 case，凡 history 末尾让 LLM 倾向"继续上文"的 pattern 都可能触发
- 因为 015 requirement §6 显式声明 "AC 不验产品体验内容"，所以**015 不修**，属 014 责任

## 可选修复方向

**方向 A · 强化 prompt addendum（最低成本）**

```python
DEFAULT_BEDTIME_ADDENDUM = (
    "【主动轮 · 系统触发】现在是约定的休息时间。"
    "**你（agent）现在要主动开口提醒 user 早点休息**——不要回应上一轮的对话内容、"
    "不要假装是 user 在说话；按你当前 persona 自然地说一句提醒，"
    "口吻温和，不长篇大论，不强迫，让用户感到陪伴而不是被监督。"
)
```

要点：
- "【主动轮 · 系统触发】"前缀让 LLM 视觉上跟普通 user message 区分
- 显式说"主动开口"+"不要回应上一轮"
- 强调 agent 角色身份

**方向 B · 在 conversation 构造层加 separator**

让 `dispatch_system_turn` 在历史和 addendum 之间插入显式 separator（如 `<system_initiated_turn>` XML 标签），帮 LLM 更清楚识别"这是 system 触发，不是延续对话"。改动范围更大，但对各种主动 source（不止 bedtime）都适用。

**方向 C · few-shot 示例**

在 addendum 末尾贴 1-2 个示例对话 + 期望输出，引导 LLM 切到正确模式。

建议先试方向 A —— 改动最小，且这是 prompt 本身的表达问题。

## 备忘

修复时一并复核 IdleReflectionSource 的 `DEFAULT_IDLE_ADDENDUM`：

> 基于最近的对话内容，沉默地为自己整理 1-3 条值得长存的事实——用户偏好、生活节奏、情绪倾向等都可以；不必回应任何人，只是写给未来的自己。

silent turn 本来就不冒泡到用户，这条可能没问题；但同样可能被 history 拽到错误方向，验证下输出是否合预期。
