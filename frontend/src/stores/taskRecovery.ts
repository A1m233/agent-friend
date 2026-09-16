import type { ChatMessage } from "@/types/chat";

/** Display grouping only. Stores keep each run separate for event routing/fencing. */
export function groupRecoveryMessages(input: ChatMessage[]): ChatMessage[] {
  const output: ChatMessage[] = [];
  for (const message of input) {
    const previous = output.at(-1);
    if (message.role !== "assistant" || previous?.role !== "assistant"
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
