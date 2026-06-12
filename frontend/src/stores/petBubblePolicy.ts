/**
 * 015 R-4.3 · pet 气泡的"事件 → 出口"分发策略。
 *
 * 本期出口只有 pet-bubble（chat 窗不接 push channel，见 015 design §3.2）。
 * Policy 通过依赖注入而非闭包绑死，是为了后期产品迭代（"主动轮要不要也镜像到
 * chat 窗" / "勿扰模式直接丢弃" / "加新出口"等）能改 policy 不动 store / UI 框架——
 * 呼应 .cursor/rules/coding-design.mdc "易变维度留扩展点"。
 *
 * 详见 015 design §5.2。
 */

import type { PushEnvelope, SessionEvent } from "@/types";

/** 气泡条目；id 用 `${session_id}:${seq}` 单调可比，便于 store 做替换判定 / 防重。 */
export interface BubbleItem {
  id: string;
  text: string;
  /** 来源标识透传（如 "cron:bedtime" / "idle_reflection"），UI 可据此差异化呈现（本期不用）。 */
  sourceKind: string | null;
}

export type PushPolicy = (env: PushEnvelope) => BubbleItem | null;

/**
 * 用户可见的 assistant 输出 event type 集合。
 *
 * 014 design §3 把主动轮"用户可见"输出落为 `assistant_message`；silent turn
 * （IdleReflectionSource）只落 `memory_observation` 而无 `assistant_message`，
 * 因此 envelope.events 里有无该 type 是"是否要冒气泡"的最干净判据——比硬编码
 * source_kind 黑名单（"idle_reflection" 等）更可演进。
 */
const ASSISTANT_TEXT_TYPES: ReadonlySet<string> = new Set([
  "assistant_message",
]);

function extractText(ev: SessionEvent): string | null {
  if (!ASSISTANT_TEXT_TYPES.has(ev.type)) return null;
  const content = ev.payload?.content;
  return typeof content === "string" && content.length > 0 ? content : null;
}

/**
 * 第一版默认策略：
 * 1. 只看 agent_turn（user_turn / heartbeat 直接丢；Rust 订阅已过滤，TS 侧再兜底）
 * 2. envelope.events 中无 user-visible assistant 文本 → silent turn，丢弃（R-4.4.4）
 * 3. 拼接所有 assistant_message 的 content（按 turn 边界打包，通常 1 段；
 *    防御性兼容多段时用 `\n\n` 连）
 */
export const defaultPolicy: PushPolicy = (env) => {
  if (env.kind !== "agent_turn") return null;
  const texts = env.events
    .map(extractText)
    .filter((t): t is string => t !== null);
  if (texts.length === 0) return null;
  return {
    id: `${env.session_id}:${env.seq}`,
    text: texts.join("\n\n"),
    sourceKind: env.source_kind,
  };
};
