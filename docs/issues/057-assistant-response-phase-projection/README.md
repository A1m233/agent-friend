# 057 · Assistant 中间进展与最终答复缺少 phase 分离和折叠

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-08-23
- **关联需求**：[091-compact-tool-call-activity](../../requirements/091-compact-tool-call-activity/)（相邻但未覆盖 assistant 文本 phase）、[087-context-compaction-progress-feedback](../../requirements/087-context-compaction-progress-feedback/)（仅覆盖运行状态进度）

## 现象

长工具链中，模型可以先输出面向用户的简短进展，再继续调用工具，最后给出正式答复。当前 agent-friend 没有为这些可见文本保留 `commentary` / `final_answer` 一类语义 phase；中间进展和最后一段文本都作为普通 assistant 内容投影到同一条消息中，也没有按 phase 提供折叠入口。

2026-08-23 的一次真实桌面会话中，DeepSeek thinking 已明确关闭，模型仍把“我应该诚实地告诉用户”“让我组织回复”等回复元规划写进普通 assistant content。最后一个 continuation 没有进入真正的用户答复，界面也无法把前面的工作说明与最终答复区别呈现。

这不是 provider 隐藏 `reasoning_content` 的展示问题：隐藏推理仍不应直接暴露。本 issue 关注的是 Codex 式用户可见中间 commentary 与最终答复的协议分层和渐进披露。

## 根因初判

- 当前 LLM activity 只能表达 waiting / reasoning / processing 等运行状态，不携带可见 assistant 文本的语义 phase。
- session / Bridge 将模型产生的普通文本统一投影为 assistant message；协议没有稳定字段区分工具前说明、中间进展和最终答复。
- 前端虽保留 `ThinkingBlock` 类型，但当前链路没有投影可用于折叠的 commentary / reasoning 文本事件；仅凭文案内容推断 phase 也不可靠。

## 影响与判断

- 长任务的工作说明、工具活动和最终结论缺少清晰层级，消息会显得冗长且难以定位最终结果。
- 当模型最后停留在回复元规划时，UI 无法识别“本轮没有真正 final answer”；C12 eval 可以约束模型行为，但不能补齐产品投影能力。
- 该能力横跨 provider、agent event、持久化、Bridge、前端折叠以及历史兼容，不适合作为当前 sandbox 修复的顺手改动，因此暂不修复并独立登记。

## 可选修复方向

- 为用户可见 assistant text 增加向后兼容的 `commentary | final_answer` phase；旧事件默认按现有语义读取，不能根据自然语言关键词猜 phase。
- 由 agent 编排在 continuation / tool-loop 边界显式标记 phase；provider 原生 phase 只在协议证据充分时映射，隐藏 reasoning 与可见 commentary 保持独立。
- 前端默认保留最终答复，允许把已完成的 commentary 渐进折叠；等待审批、失败和仍在运行的内容继续突出显示，并与需求 091 的工具活动层级协调。
- 明确持久化、上下文回放、搜索、编辑重发、IM 投影和上下文压缩对两类 phase 的处理，避免折叠只存在于当前 DOM。
- 增加跨层测试：多次工具 continuation、只有 final、commentary 后失败或取消、缺失 final、历史旧事件与刷新恢复。
