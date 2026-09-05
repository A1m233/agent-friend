import { useEffect, useState } from "react";
import { StatusDot } from "@/components/ui";
import type { ModelResponseProgress } from "@/types/chat";

const SLOW_MODEL_RESPONSE_MS = 10_000;
const STALE_MODEL_ACTIVITY_MS = 12_000;

export function modelResponseStatusLabel(
  status: ModelResponseProgress | undefined,
  {
    slow = false,
    staleActivity = false,
    override,
  }: { slow?: boolean; staleActivity?: boolean; override?: string } = {},
): string {
  if (override) return override;
  if (!status) {
    return slow
      ? "准备回答所需内容花了较长时间，仍在继续…"
      : "正在准备回答…";
  }
  if (status.phase === "retrying") {
    return `连接不稳定，正在重试 ${Math.max(1, status.attempt - 1)}/${Math.max(1, status.maxAttempts - 1)}`;
  }
  if (status.phase === "reasoning") {
    return staleActivity
      ? "暂时没有收到新的推理活动，模型可能仍在处理，也可能已经停滞…"
      : "模型仍在推理，连接正常…";
  }
  if (status.phase === "processing") {
    return staleActivity
      ? "暂时没有收到新的模型活动，可能仍在处理，也可能已经停滞…"
      : "模型仍在处理，连接正常…";
  }
  if (slow) {
    return "暂未收到新的模型活动，可能仍在等待服务，也可能已经停滞…";
  }
  return status.afterTool ? "正在整理工具结果…" : "正在等待模型响应…";
}

export function useModelResponseStatusLabel(status?: ModelResponseProgress, label?: string) {
  const [slow, setSlow] = useState(false);
  const [staleActivity, setStaleActivity] = useState(false);

  useEffect(() => {
    setSlow(false);
    if (status && status.phase !== "waiting") return;
    const timer = window.setTimeout(() => setSlow(true), SLOW_MODEL_RESPONSE_MS);
    return () => window.clearTimeout(timer);
  }, [status?.phase, status?.afterTool]);

  useEffect(() => {
    setStaleActivity(false);
    if (status?.phase !== "reasoning" && status?.phase !== "processing") return;
    const timer = window.setTimeout(() => setStaleActivity(true), STALE_MODEL_ACTIVITY_MS);
    return () => window.clearTimeout(timer);
  }, [status?.phase, status?.activityVersion]);

  return modelResponseStatusLabel(status, { slow, staleActivity, override: label });
}

export function ModelResponseStatus({ status, label, primaryActivityKind }: {
  status?: ModelResponseProgress;
  label?: string;
  primaryActivityKind?: "model" | "run";
}) {
  const text = useModelResponseStatusLabel(status, label);
  return (
    <p
      className="flex items-center gap-2 px-1 text-xs text-muted"
      role="status"
      data-primary-activity={primaryActivityKind}
    >
      <StatusDot tone="muted" pulse />
      {text}
    </p>
  );
}
