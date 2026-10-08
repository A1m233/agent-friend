import type { ComponentProps } from "react";
import { useConversationStore } from "@/stores";
import type { ChatMessage } from "@/types/chat";
import { ExecutionDisclosure } from "./ExecutionDisclosure";
import { RunDurationLabel } from "./RunDurationLabel";
import { isRunAwaitingStart } from "./runTiming";

/** Run facts control header visibility; ticking stays inside the small duration label. */
export function RunExecutionDisclosure({ message, hideHeader = false, ...props }: {
  message: ChatMessage;
  hideHeader?: boolean;
} & Omit<ComponentProps<typeof ExecutionDisclosure>, "label" | "showHeader">) {
  const snapshot = useConversationStore((state) => message.runId ? state.runSnapshots[message.runId] : undefined);
  const calibration = useConversationStore((state) => state.runCalibrationPhase);
  const run = snapshot?.status === "terminal" || calibration === "ready" ? snapshot : undefined;
  const interactive = run ? run.status === "terminal" : message.status !== "streaming";
  return <ExecutionDisclosure {...props} interactive={interactive}
    showHeader={!hideHeader && message.history?.first !== false && !isRunAwaitingStart(message, run)}
    collapse={props.collapse && (!run || run.outcome === "completed")}
    label={<RunDurationLabel key={run?.started_at ?? message.runStartedAt} message={message} run={run} />} />;
}
