# 011 · chat 窗用户对话不驱动桌宠状态机 / lip-sync

- **状态**：open
- **严重度**：medium · 影响产品核心体感
- **发现日期**：2026-06-15
- **发现于**：[018-pet-live2d-state-and-lipsync](../../requirements/018-pet-live2d-state-and-lipsync/) M18.9 真跑
- **关联需求**：[014-engine-main-loop-and-bridge-push](../../requirements/014-engine-main-loop-and-bridge-push/)、[015-desktop-pet-bubble-and-conversation-owner](../../requirements/015-desktop-pet-bubble-and-conversation-owner/)

## 现象

- 用 `./scripts/dev-fire-source/run.sh cron:bedtime` 触发主动轮时，pet 窗状态机正常切换 `idle → speaking → idle`，Hiyori lip-sync 嘴部跟说话节奏开关 ✓
- 在 **chat 窗用户对话**时（user 在 Composer 打字 + Enter 发送 + agent 回复），**桌宠状态机不切换**、**Hiyori 嘴不跟说话动**

## 根因

014 / 015 既有设计：

- chat 窗用户对话经 `POST /ag-ui/run` → AgentRuntime 触发 **UserEvent** → 整个轮标 envelope `kind="user_turn"`
- 主动轮（BedtimeSource / IdleReflectionSource）经 SystemTriggerEvent → 整个轮标 envelope `kind="agent_turn"`
- 015 既有 `frontend/src-tauri/src/push_subscriber.rs:48` 订阅 URL 写死 `?kinds=agent_turn` —— **user_turn 被服务端过滤掉**，前端永远收不到
- 015 设计意图是"bubble 气泡只显示主动陪伴，不重复显示 user 自己在 chat 看到的对话"，bubble 端这是合理 filter
- 但 018 状态机 / lip-sync 的语义跟 bubble 不同——**应该跟随所有 agent 输出**（不论 user 触发还是 system 触发）

## 影响与判断

- 影响产品核心体感：用户主要使用场景是 chat 对话，但 Hiyori 不跟着动嘴 = lip-sync 实际产品价值打折
- 不影响功能（chat 对话回复 / 状态机本身 / 主动轮 lip-sync 都正常）
- 18b AC-3 / AC-4 / AC-5 spec 没要求覆盖 user_turn 场景，用主动轮触发已 close

## 可选修复方向

1. **最小补丁**（5 分钟）：
   - `push_subscriber.rs:48` URL 从 `?kinds=agent_turn` 改成 `?kinds=agent_turn,user_turn`
   - `frontend/src/stores/petStatePolicy.ts` 移除 `if (env.kind !== "agent_turn") return { from: current, next: current }`，让 user_turn envelope 也走状态机
   - bubble 窗不动（015 既有 `petBubblePolicy.ts` 第一行 `if (env.kind !== "agent_turn") return null` 自己过滤 user_turn 不冒气泡）
2. **更彻底**：在 015 design 重新评估 push channel `kinds` 语义——bubble / pet / 未来 voice 各自需要不同 filter，可能要按 surface 而非 kind 分流

倾向方向 1（最小补丁，复用既有 channel 不重新设计）。

## 关联

- 18b AC-3/4/5 用主动轮 (cron:bedtime) 触发已通过；本 issue 仅补 chat 对话 lip-sync 这一缺失场景
- 修复时建议把 chat 对话端到端 driver attach + 嘴动作为 18b AC-5 的补充真验项
