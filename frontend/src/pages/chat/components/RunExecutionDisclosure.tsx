import type { ComponentProps } from "react";
import { useConversationStore } from "@/stores";
import type { ChatMessage } from "@/types/chat";
import { ExecutionDisclosure } from "./ExecutionDisclosure";
import { RunDurationLabel } from "./RunDurationLabel";
import { isRunAwaitingStart } from "./runTiming";

/** Run facts control header visibility; ticking stays inside the small duration label. */
export function RunExecutionDisclosure({ message, ...props }: {
  message: ChatMessage;
} & Omit<ComponentProps<typeof ExecutionDisclosure>, "label" | "showHeader">) {
  const snapshot = useConversationStore((state) => message.runId ? state.runSnapshots[message.runId] : undefined);
  const calibration = useConversationStore((state) => state.runCalibrationPhase);
  const keepReveal = useConversationStore((state) => Boolean(props.revealTarget
    && state.searchTarget?.occurrence !== undefined && state.searchTarget.anchorEventUuid === props.revealTarget));
  const run = snapshot?.status === "terminal" || calibration === "ready" ? snapshot : undefined;
  const interactive = run ? run.status === "terminal" : message.status !== "streaming";
  const proactive = message.messageSource?.kind === "agent_initiated";
  return <ExecutionDisclosure {...props} keepReveal={keepReveal} interactive={!proactive && interactive} showHeader={proactive || !isRunAwaitingStart(message, run)}
    collapse={props.collapse && (!run || run.outcome === "completed")}
    label={proactive ? "由 agent-friend 主动发出" : <RunDurationLabel key={run?.started_at ?? message.runStartedAt} message={message} run={run} />} />;
}
