# 006 · BedtimeSource 提示词被 conversation history 拽走，产出虚构 user 视角回答

- **状态**：resolved（2026-06-17，由 [`需求 021`](../../requirements/021-system-trigger-user-injection/) 闭环）
- **严重度**：medium
- **发现日期**：2026-06-13
- **关联需求**：[014-engine-main-loop-and-bridge-push](../../requirements/014-engine-main-loop-and-bridge-push/)（架构来源）；[021-system-trigger-user-injection](../../requirements/021-system-trigger-user-injection/)（修复方案）

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

> ⚠️ **2026-06-17 update**：方向 A 已尝试 + 真跑证伪（DeepSeek v4-flash + v4-pro 均失败），详见文末"方向 A 尝试记录"。**推荐升级到方向 B**——已确认是 LLM API 语义层问题，不是 prompt 文案问题，方向 A 在当前架构下天花板已被打到。

**方向 A · 强化 prompt addendum（最低成本）** ❌ **已证伪**

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

**方向 B · 在 conversation 构造层改注入路径** ⭐ **当前推荐**

让 `dispatch_system_turn` 把 `system_prompt_addendum` 包成 `role="user"` 注入到 messages 末尾（LLM 视图里可见，session.messages 落盘里不写——仿 014 已有 `system_trigger marker` 模式），不再走 `role="system"` 的 trailing_system。这样 LLM 看到 `... assistant, **user**` 而非 `... assistant, **system**`，user role 才是真正的 turn 切换信号——LLM 会切到"生成新一轮 assistant 回应"模式，自然不会续写上一轮 assistant。

落地清单（约 200-400 行 diff 含测试）：

- `agent/src/agent/context/protocol.py`：`build_messages` 加 `trailing_user` 参数；顺序 `[system] + extra + history + [trailing_user] + [trailing_system]`（两者互斥）
- `agent/src/agent/context/{fifo,summarizing,naive}.py`：context manager 透传 trailing_user
- `agent/src/agent/conversation.py`：`dispatch_system_turn` 改用 `trailing_user=addendum`；这条 user message **不**落 session_messages（参考已有 `_append_system_trigger_event` 模式）
- `agent/src/agent/runtime/sources.py`：`DEFAULT_BEDTIME_ADDENDUM` / `DEFAULT_IDLE_ADDENDUM` 文案改成"user 对 agent 的提示"语气
- 单测：protocol.py 顺序 + dispatch_system_turn fake LLM 注入位置 + session.messages 不污染
- `docs/requirements/014-engine-main-loop-and-bridge-push/{design.md,progress.md}`：补一段架构调整

**性质**：这是 **014 需求的架构调整**，按 `dev-workflow.mdc` 应走 `feature/<NNN>-...` 分支（不是 `fix/<NNN>-...` 的 issue 修复路径）。

对所有主动 source 通用（bedtime / idle / 未来其他）——不止修 issue 006，把"trailing_system 被 history 拽走"这类问题在架构层根治。

**方向 C · few-shot 示例**

在 addendum 末尾贴 1-2 个示例对话 + 期望输出，引导 LLM 切到正确模式。

~~建议先试方向 A —— 改动最小，且这是 prompt 本身的表达问题。~~ **已证伪，见下方记录。**

## 方向 A 尝试记录（2026-06-17）

### 改动

- `agent/src/agent/runtime/sources.py:34` `DEFAULT_BEDTIME_ADDENDUM` 文案改成方向 A 推荐写法（"【主动轮·系统触发】" 前缀 + 显式"不要回应上一轮 / 不要假装 user / 主动开口"）
- 新增 `docs/issues/006-bedtime-prompt-history-hijack/replay.py` 真跑验证脚本——复用 015 同一份 session jsonl（`23fe5835-...`）的 line 1-56 截断，末尾刚好是一条 assistant 含两个问句（"……你还记得大概是在哪里看到的吗？……真人小孩？"），完美符合"history 末尾让 LLM 倾向接续上文"的复现条件

### 真跑结果

| 跑次 | model | 输出片段 | 判定 |
| --- | --- | --- | --- |
| #1 | `deepseek/deepseek-v4-flash` | "你说的这个～太熟了！这不是那个经典的声真似素材吗？是一个日本小女孩 meme……" + 想发 `web_search` tool_call | ❌ 完全续写上文，无 "该睡了" 语义 |
| #2 | `deepseek/deepseek-v4-pro` | "搜了好几圈，没能直接定位到你描述的这个梗。我再换个搜法试试。" + 想发 `web_search` tool_call | ❌ 同上 |

### 根因锁定

`agent/src/agent/context/protocol.py:172` 写明 messages 顺序：

```
[system?] + extra_context? + history + [user?] + [trailing_system?]
```

`trailing_system` 已经在最末位置（紧跟 history 末尾 assistant 之后）——位置不是问题。但 DeepSeek（flash + pro 均一致）看到 sequence `... user, assistant(两个问号), system(addendum)` 后，**不把最后那条 system 当独立 turn 切换信号**，而是顺着 assistant 续写惯性 generate。

这是 LLM API 层面的语义问题：**没有 user role 触发的 turn 不是真正的 turn**，trailing system 只是"现有对话的额外提示"，不是"新一轮的开始信号"。prompt 文案再怎么强势、model 再怎么强，只要还是 `role="system"` 接在 assistant 后面，DeepSeek 大概率仍续写上文。

### 结论

- 方向 A（纯 prompt 文案改）在 DeepSeek 上**天花板已打到**——不是 sampling 边缘 case，是架构层语义问题
- 当前留下的强化文案 + replay.py 作为**探索 artifact 保留**——文案对未来方向 B 仍可复用（B 改后会从 `role="system"` 切到 `role="user"`，这段文案微调后仍可用）
- 升级方向 B 才是正解——见上方"方向 B"清单


## 备忘

修复时一并复核 IdleReflectionSource 的 `DEFAULT_IDLE_ADDENDUM`：

> 基于最近的对话内容，沉默地为自己整理 1-3 条值得长存的事实——用户偏好、生活节奏、情绪倾向等都可以；不必回应任何人，只是写给未来的自己。

silent turn 本来就不冒泡到用户，这条可能没问题；但同样可能被 history 拽到错误方向，验证下输出是否合预期。

---

## 方向 B 落地记录（2026-06-17，本 issue 闭环）

- **修复需求**：[`021-system-trigger-user-injection`](../../requirements/021-system-trigger-user-injection/)（feature/021 分支）
- **修复 commit**：`0a90a0b`
- **落地内容**：把 `Conversation.dispatch_system_turn` 注入路径从 `trailing_system` 切到 `trailing_user`——LLM 看到的 messages 末尾从 `role="system"` 变成 `role="user"`，user role 才是 chat-completions 协议的真正"新一轮 turn 开始"信号。配套 Claude Code 同款三层加固：
  - **协议层**：`assemble_messages` / `ContextManager.build_messages` 加 `trailing_user` 槽位，三个 context manager 透传
  - **文案层**：`DEFAULT_BEDTIME_ADDENDUM` / `DEFAULT_IDLE_ADDENDUM` 用 `<system_trigger>...</system_trigger>` tag 包裹；bedtime 文案改为 user 视角"该睡了"提示语气
  - **system prompt 层**：`project_identity.md` 加元规则训练 LLM 识别 `<system_trigger>` tag，避免归因漂移（claude-code [#23537](https://github.com/anthropics/claude-code/issues/23537) / [#27128](https://github.com/anthropics/claude-code/issues/27128) 同类问题）
- **真跑验证**：[`replay.py`](./replay.py)（bedtime 路径）+ [`replay_silent.py`](./replay_silent.py)（silent 路径），v4-flash + v4-pro 各 2-3 次，多数行为成立
- **方向 A 尝试记录保留**：上方"方向 A 尝试记录"小节作为 archaeology 保留——让未来读者看到方向 A 在 DeepSeek 真跑下天花板已打到、必须从架构层切才能根治。
