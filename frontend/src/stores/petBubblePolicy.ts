/**
 * 015 R-4.3 · pet 气泡的"事件 → 出口"分发策略。
 *
 * 本期出口只有 pet-bubble（chat 窗不接 push channel，见 015 design §3.2）。
 * Policy 通过依赖注入而非闭包绑死，是为了后期产品迭代（"主动轮要不要也镜像到
 * chat 窗" / "勿扰模式直接丢弃" / "加新出口"等）能改 policy 不动 store / UI 框架——
 * 呼应 .cursor/rules/coding-design.mdc "易变维度留扩展点"。
 *
 * 关键事实（M15.8 真跑暴露）：envelope.events 是 ConversationEvent asdict 后的平铺
 * dict（**不是** session.events JSONL 的 SessionEvent），assistant 文本走的是
 * `text_delta` 流式增量、字段是 `.text`（见 types/push.ts 顶部注释）。
 *
 * 详见 015 design §5.2。
 */

import type { PushEnvelope, PushEvent } from "@/types/push";

/** 气泡条目；id 用 `${session_id}:${seq}` 单调可比，便于 store 做替换判定 / 防重。 */
export interface BubbleItem {
  id: string;
  text: string;
  /** 来源标识透传（如 "cron:bedtime" / "idle_reflection"），UI 可据此差异化呈现（本期不用）。 */
  sourceKind: string | null;
}

export type PushPolicy = (env: PushEnvelope) => BubbleItem | null;

/**
 * 用户可见的 assistant 输出 event type 集合（envelope.events 内 ConversationEvent type）。
 *
 * 014 主动轮"用户可见"的输出经 `conv.stream()` yield 出 `TextDelta` 事件、通过 push
 * channel 透传到这里；silent turn（IdleReflectionSource）不 yield 任何 `text_delta`、
 * 整轮 envelope 里仅 `done` 事件。所以"有无 text_delta"就是"是否要冒气泡"的判据。
 */
const ASSISTANT_TEXT_TYPES: ReadonlySet<string> = new Set([
  "text_delta",
]);

function extractText(ev: PushEvent): string | null {
  if (!ASSISTANT_TEXT_TYPES.has(ev.type)) return null;
  const text = ev.text;
  return typeof text === "string" && text.length > 0 ? text : null;
}

/**
 * 第一版默认策略：
 * 1. 只看 agent_turn（user_turn / heartbeat 直接丢；Rust 订阅已过滤，TS 侧再兜底）
 * 2. envelope.events 中无 user-visible text_delta → silent turn，丢弃（R-4.4.4）
 * 3. 拼接所有 text_delta.text（顺序即 envelope 顺序、即 turn 内时序）
 */
export const defaultPolicy: PushPolicy = (env) => {
  if (env.kind !== "agent_turn") return null;
  const texts = env.events
    .map(extractText)
    .filter((t): t is string => t !== null);
  if (texts.length === 0) return null;
  return {
    id: `${env.session_id}:${env.seq}`,
    text: texts.join(""),
    sourceKind: env.source_kind,
  };
};
