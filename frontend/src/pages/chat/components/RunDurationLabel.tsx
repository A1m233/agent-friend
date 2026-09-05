import { useEffect, useState } from "react";
import type { ChatMessage } from "@/types/chat";
import type { RunSnapshot } from "@/types/run";
import { elapsedRunSeconds, formatRunDuration } from "./runTiming";

export function RunDurationLabel({ message, run }: { message: ChatMessage; run?: RunSnapshot }) {
  const live = run?.status === "running";
  const outcome = run?.outcome ?? message.runOutcome;
  const stopped = run?.status === "running" || run?.status === "queued"
    ? false : outcome ? outcome === "cancelled" : message.status === "interrupted";
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    setNow(Date.now());
    if (!live) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [live, message.runId]);
  return <span data-run-duration>{formatRunDuration(
    elapsedRunSeconds(message, run, now), live ? "running" : stopped ? "stopped" : "finished",
  )}</span>;
}
