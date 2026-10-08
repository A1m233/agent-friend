import type { ChatMessage, ContextRecoveryMode, RunRecoveryAction } from "@/types/chat";

const RECOVERY_ACTIONS = new Set<RunRecoveryAction>([
  "compact_then_resume", "retry", "adjust_input", "adjust_configuration", "wait",
]);

/** Decode optional protocol details without inferring actions from user-facing prose. */
export function parseRunErrorRecovery(
  value: unknown,
  casing: "camel" | "snake",
): Pick<ChatMessage, "errorReason" | "recoveryAction"> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { errorReason: undefined, recoveryAction: undefined };
  }
  const record = value as Record<string, unknown>;
  const action = record[casing === "camel" ? "recoveryAction" : "recovery_action"];
  return {
    errorReason: typeof record.reason === "string" && record.reason ? record.reason : undefined,
    recoveryAction: typeof action === "string" && RECOVERY_ACTIONS.has(action as RunRecoveryAction)
      ? action as RunRecoveryAction : undefined,
  };
}

/** Select how an already eligible task continues; never grant canContinue here. */
export function continuationRecoveryMode(message: ChatMessage): ContextRecoveryMode {
  if (message.canContinue !== true) return "automatic";
  return message.recoveryAction === "compact_then_resume"
    || (message.recoveryAction === undefined && message.errorCode === "context_compaction_unavailable")
    ? "compact_then_resume" : "automatic";
}

/** Display grouping only. Stores keep each run separate for event routing/fencing. */
export function groupRecoveryMessages(input: ChatMessage[]): ChatMessage[] {
  const output: ChatMessage[] = [];
  for (const message of input) {
    const previous = output.at(-1);
    if (message.history || previous?.history || message.role !== "assistant" || previous?.role !== "assistant"
      || !message.sourceRunId || previous.runId !== message.sourceRunId) {
      output.push(message);
      continue;
    }
    const start = Date.parse(previous.runStartedAt ?? "");
    const end = Date.parse(previous.runFinishedAt ?? "");
    const prior = previous.priorExecutionSeconds;
    const duration = prior === null || !Number.isFinite(start) || !Number.isFinite(end) || end < start
      ? null : (prior ?? 0) + (end - start) / 1000;
    output[output.length - 1] = {
      ...message,
      rootRunId: previous.rootRunId ?? previous.runId,
      blocks: [
        ...previous.blocks.filter((block) => block.kind !== "text" || !block.partial),
        ...message.blocks,
      ],
      priorExecutionSeconds: duration,
    };
  }
  return output;
}
