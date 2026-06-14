# 007 · voice-call · 进度

## 状态

代码级完成，合入 main；2026-06-15 已补跑 Win 真机语音 E2E smoke，通过。

## 验证情况（截至合入）

对照 [`test-plan.md §3 合并 main 前的最终清单`](./test-plan.md)：

| 项 | 状态 | 说明 |
|---|---|---|
| §1.1 全量 pytest 全绿 | ✅ | `145 passed`（含 `voice_bridge/tests/integration/test_acceptance_criteria.py` 全部 AC mock 集成测试） |
| §1.4 lint + typecheck 全绿 | ✅ | ruff check + format、mypy strict 均通过 |
| §2 Win 真机 E2E smoke | ✅ | 2026-06-15 补测通过；详见下方“Win 真机 E2E smoke 补测记录” |
| AC-9 `data/` / `experiments/` 不动 | ✅ | 见 `git diff main..feature/007-voice-call` |

## 合入决策与挂账

按 `test-plan.md §3`，严格流程要求 §2 真机 smoke 通过 + 用户确认才可合 main。本次是**有意识的破例**：

- **破例原因**：§2 卡在**环境**（无合规可穿透的机器），而非代码质量；预计短期内无法补测。若继续 gate，007 会长期悬挂、与后续需求（008 起）的冲突面持续累积，越拖越难合。
- **降级后的合入门槛**：代码级 + mock 级 AC（§1 / §1.4）全绿即合，**真机语音端到端体验记为待办**。
- **遗留风险**：真火山 RTC 的拨打 / 进房 / ASR / TTS / 挂断全链路体感未经真机验证；`voice_bridge` 为独立包 + 独立进程，不启动它不影响 CLI / agent_bridge 默认行为，故对 main 其余能力风险可控。

## 后续

- 语音端到端已补测通过；后续重点转为降低“用户说完 → AI 开始回复”的体感延迟。

## Win 真机 E2E smoke 补测记录（2026-06-15）

### 结论

按 [`test-plan.md §2`](./test-plan.md) 在 Windows + cloudflared quick tunnel 环境补跑真火山 RTC 链路，已确认：

- 浏览器 smoke 能拨打通话，AI bot 进房并播放欢迎语。
- 用户麦克风输入可被火山 ASR 识别，字幕回调能看到用户语音文本。
- 火山 `CustomLLM` 回调打到 `voice_bridge`，再转发到 `agent_bridge`。
- 回复由 agent-friend 自己的 `agent` 生成，底层模型走 DeepSeek；不是火山方舟大脑。
- AI 能通过火山 TTS 用语音回复用户，挂断能正常停止任务。

### 本次真机暴露并修复的问题

- smoke 页面缺 CORS 支持：`OPTIONS /voice/calls` 曾返回 405，导致浏览器 `Failed to fetch`。
- smoke 页面引用的火山 RTC Web SDK CDN 路径失效，导致 `window.VERTC` 为空。
- smoke 页面缺少本地麦克风音量诊断，不利于区分“麦克风无输入”和“ASR 未触发”。
- `LLMConfig` 回调字段应为 `Url`，不是 `URL`。
- 本机 `.env` 中 `VOLC_SPEECH_APP_ID` 与已验证 spike 配置不一致，导致 ASR 回调报 `asr:websocket: bad handshake`；修正后 ASR 正常。

### 延迟观测

以一次成功通话 `a602a588-45c2-4498-8b99-a961a24d6f32` 的后端日志为准：

- `02:02:01.453`：`StartVoiceChat` 返回 200。
- `02:02:07.423`：火山状态进入 `listening`。
- `02:02:09.746`：收到用户 ASR 字幕。
- `02:02:09.944`：火山状态进入 `thinking`。
- `02:02:13.209`：`agent_bridge /v1/chat/completions` 返回 200。
- `02:02:18.010`：火山状态进入 `answering`。
- `02:02:30.449`：火山状态进入 `answerFinish`。

粗略看，“用户说完 → AI 开始回答”的后端可见延迟约 8 秒；完整回答结束约 20 秒级。该延迟不阻塞 007 补测通过，但应作为后续语音体验优化项继续跟进。
