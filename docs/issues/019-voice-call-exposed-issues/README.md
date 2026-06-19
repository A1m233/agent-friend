# 019 · 语音通话真机 smoke 暴露的多项体验与数据问题

- **状态**：open
- **严重度**：high
- **发现日期**：2026-06-19
- **关联需求**：007-voice-call

## 现象

2026-06-19 在个人 Windows 机器上跑通 `voice_bridge/smoke/index.html` 真机语音通话后，暴露出以下问题：

### 1. 用户说完后 AI 开口慢（延迟体感差）

浏览器 timeline 节选：

```
+8095ms volc LISTENING → THINKING (+643ms) round=1
+11237ms volc THINKING → SPEAKING (+3142ms) round=1
+11577ms subtitle 首字 user=bot since THINKING +3482ms text="还行"
```

用户说完第一句话后，约 **3.5s** 才出现 AI 字幕首字；后续多轮普遍在 **2.5~4.9s**，最长近 5s。整段通话中 `volc THINKING → SPEAKING` 耗时明显高于 spike 期目标（spike 测得 LLM 首 token 是瓶颈，切非思考模型后应大幅下降）。

### 2. AI 语音朗读中读出 KaTeX / Markdown 语法

AI 在语音回复中读出了 `\(` `\)`、`\[` `\]`、`****` 等标记。该问题在涉及数学公式 / 强调语法时必然出现。

### 3. 记忆自相矛盾：先承认聊过，后说查不到

用户先问"我们最近说了啥"，AI 提到"流水行船"；用户接着说"这道题我忘了，你和我讲讲怎么做"，AI 却在使用记忆查询工具后回复"没查到流水行船的记录"。

### 4. 语音通话对话记录未落盘

通话结束后检查 `C:\Users\example-user\AppData\Roaming\agent-friend\sessions\` 下对应 session 的 jsonl 文件，只有 `session_meta` 一行，没有 `user_message` / `assistant_message` 事件。`agent_bridge` 日志显示每次 `/v1/chat/completions` 调用都走了 OpenAI 无状态 transient 路径。

## 根因初判

| 问题 | 根因初判 |
|---|---|
| 开口慢 | DeepSeek v4-flash 首 token 本身 0.8~1.7s（`bridge_ms` 日志），但端到端 `THINKING → SPEAKING` 仍有 2.5~3.5s，额外时间花在哪待定位：火山 VAD 判停？TTS 首包？SSE 转发到火山？ |
| 读出 KaTeX/Markdown | `channel_voice.md` 只约束 LLM "不要输出 markdown"，但**没有后处理过滤**或 prompt 里明确排除 KaTeX 公式语法；工具结果 / 记忆里的数学公式直接流给 TTS |
| 记忆自相矛盾 | 工作记忆（上下文摘要）记得"流水行船"，但 `recall_past_chats` 工具检索记忆库时未命中；可能是 query 生成问题、记忆写入延迟、或记忆库与工作记忆不同源 |
| 对话未落盘 | `voice_bridge` LLM proxy 注入了 `X-Agent-Friend-Session-Id` header，但 `agent_bridge` 的 `/v1/chat/completions` 路由**没有读取该 header**，一直走 `SessionBridge.start_transient()`，导致语音对话写在内存临时 session 里 |

## 影响与判断

- **数据正确性（问题 4）**：语音通话产生的对话完全丢失，违反 007 需求中"通话 ↔ session 桥接"的设计目标，也违反用户对"同一 session 续聊"的合理预期。属于 high。
- **核心体验（问题 1、2、3）**：开口延迟、读语法、记忆自相矛盾都会让语音通话显得"不聪明"，直接影响 007 smoke 通过标准里"贴近真人"这一项。
- 这些不是单点 bug，而是 007 真机验收时一次性暴露的**综合症候群**，适合统一登记后逐个拆解修复。

## 可选修复方向

1.  **session 不落盘**：改 `agent_bridge/src/agent_bridge/protocols/openai/routes.py`，让 `chat_completions` 读取 `X-Agent-Friend-Session-Id`，有值时走持久化 session（`bind_persistent` 或等效逻辑）。
2.  **开口慢**：加更细粒度的时间线日志（已完成初步版），定位是 VAD、TTS 还是代理转发；必要时调火山 `VADConfig.SilenceTime`、换更快模型、或对欢迎语做预生成。
3.  **读出 KaTeX/Markdown**：在语音通道 response 编码层加**语音化后处理**，把 `\(` `\)`、`\[` `\]`、`****` 等过滤或转义成口语表达；或在 `channel_voice.md` 里明确禁止输出 KaTeX。
4.  **记忆自相矛盾**：调 `recall_past_chats` 工具 query 生成逻辑，或检查记忆库（`memory` 模块）在语音 session 下是否写入异常；必要时在工作记忆与工具检索之间做一致性兜底。

## 修复记录

- **问题 4（session 不落盘）**：已在 commit `ad8984e` 修复。`agent_bridge` 的 `/v1/chat/completions` 现在会读取 `X-Agent-Friend-Session-Id` header；存在则绑定持久化 session，不存在则保持原有 transient 行为；session_id 不存在时返回 404。
