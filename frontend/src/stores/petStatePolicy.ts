/**
 * 18 R-4.3 · 桌宠状态机的"事件 → 态决策"策略。
 *
 * 与 015 `petBubblePolicy` 完全对称（都是把同一份 envelope 投影到不同关注点）：
 * - 015 policy → `BubbleItem | null`（**内容路由**：决定这个 envelope 是否冒气泡 + 冒什么内容）
 * - 18 policy  → `PetStateTransition`（**FSM 投影**：决定桌宠态从哪儿切到哪儿）
 *
 * 两者正交：agent thinking 时气泡可能 idle；agent done 后气泡可能仍 showing（015 常驻语义）。
 *
 * 详见 18 design §3.2 / §5.2。
 */

import type { PushEnvelope } from "@/types/push";
import type { PetPhase } from "./petState";

export interface PetStateTransition {
  from: PetPhase;
  next: PetPhase;
  /** 切换前可选延迟（ms）。speaking → idle 用 300ms 留窗口给 lip-sync 收尾。 */
  delayMs?: number;
}

export type PetStatePolicy = (env: PushEnvelope, current: PetPhase) => PetStateTransition;

const EVENT_KIND = {
  TOOL_CALL_REQUEST: "tool_call_request",
  TOOL_CALL_RESULT: "tool_call_result",
  TEXT_DELTA: "text_delta",
  DONE: "done",
} as const;

/**
 * 默认 transition 优先级：
 * 1. envelope 含 text_delta → speaking（避免同帧 tool_call + text_delta 闪进 thinking）
 * 2. envelope 含 tool_call_* → thinking
 * 3. envelope 含 done：
 *    - 当前 speaking → idle 延迟 300ms（lip-sync 收尾窗口）
 *    - 当前 thinking 或其他 → idle 立即（silent turn / 错误恢复）
 *
 * user_turn / heartbeat / 空 envelope 不改变态。
 */
export const defaultPetStatePolicy: PetStatePolicy = (env, current) => {
  if (env.kind !== "agent_turn") return { from: current, next: current };
  const types = new Set(env.events.map((e) => e.type));

  if (types.has(EVENT_KIND.TEXT_DELTA)) {
    return { from: current, next: "speaking" };
  }

  if (
    types.has(EVENT_KIND.TOOL_CALL_REQUEST) ||
    types.has(EVENT_KIND.TOOL_CALL_RESULT)
  ) {
    return { from: current, next: "thinking" };
  }

  if (types.has(EVENT_KIND.DONE)) {
    if (current === "speaking") {
      return { from: current, next: "idle", delayMs: 300 };
    }
    return { from: current, next: "idle" };
  }

  return { from: current, next: current };
};
