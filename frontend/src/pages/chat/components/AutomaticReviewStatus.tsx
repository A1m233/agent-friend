import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui";
import { http } from "@/services/http";
import { useCommandRuntimeStore, useConversationStore, useFilesystemApprovalsStore } from "@/stores";
import { sessionActiveRun } from "@/stores/runPresentation";
import { CHAT_CONTENT_CONTAINER_CLASS } from "../layout";

const LABELS: Record<string, string> = {
  reviewing: "正在帮你审批", denied: "这项操作未获放行", timed_out: "审批超时，操作未执行",
  failed: "审批出错，操作未执行", aborted: "审批已中止，操作未执行",
};

export function AutomaticReviewStatus({ toolCallId }: { toolCallId?: string }) {
  const sessionId = useConversationStore((state) => state.currentSessionId);
  const mode = useConversationStore((state) => state.approvalMode);
  const readOnly = useConversationStore((state) => Boolean(
    state.currentSessionId && state.readOnlySessionIds[state.currentSessionId],
  ));
  const activeRunId = useConversationStore((state) => sessionActiveRun(state, state.currentSessionId)?.run_id);
  const commands = useCommandRuntimeStore((state) => state.approvals);
  const files = useFilesystemApprovalsStore((state) => state.entries);
  const [submitting, setSubmitting] = useState(false);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const requests = [...Object.values(commands), ...Object.values(files)
    .filter((entry) => entry.status !== "interrupted")
    .map((entry) => entry.request)]
    .filter((request) => request.session_id === sessionId && request.decision_source === "auto_review"
      && LABELS[request.status] && !dismissed.includes(request.request_id)
      && (toolCallId
        ? request.tool_call_id === toolCallId && request.status !== "reviewing"
        : request.run_id === activeRunId && request.status === "reviewing"));
  const request = requests.at(-1);
  if (!request) return null;
  const canRetry = !readOnly && mode === "auto_review"
    && ["denied", "timed_out", "failed"].includes(request.status) && !activeRunId;
  const retry = async () => {
    setSubmitting(true);
    try {
      const result = await http.post<unknown, { action: unknown }>(
        `/v1/approvals/${encodeURIComponent(request.request_id)}/reauthorize-review`,
        { session_id: sessionId, expected_version: request.version },
      );
      if (useConversationStore.getState().currentSessionId !== sessionId) return;
      useConversationStore.getState().send(`我已查看这项操作的审批结果，授权对下面这个具体操作重新审批并尝试一次；仍然遵守安全限制。\n${JSON.stringify(result.action)}`);
      setDismissed((values) => [...values, request.request_id]);
    } catch (error) {
      toast.error("无法重新审批", { description: String(error) });
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <div className={toolCallId ? "w-full" : CHAT_CONTENT_CONTAINER_CLASS}>
      <div role="status" className="pointer-events-auto mb-2 w-full space-y-2 rounded-lg border border-border bg-surface p-3 text-sm">
        <div className="flex items-center justify-between gap-2">
          <span>{LABELS[request.status]}</span>
          {request.status !== "reviewing" && (
            <Button size="xs" variant="ghost"
              onClick={() => setDismissed((values) => [...values, request.request_id])}>
              收起
            </Button>
          )}
        </div>
        <p className="break-words text-xs text-muted">{request.review?.rationale ?? request.reason}</p>
        {request.status !== "reviewing" && (
          <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words text-xs text-muted">
            {JSON.stringify("details" in request ? request.details : { items: request.items, actions: request.actions }, null, 2)}
          </pre>
        )}
        {canRetry && (
          <Button size="xs" variant="outline" loading={submitting} onClick={() => void retry()}>
            为这项操作补充授权并重试
          </Button>
        )}
      </div>
    </div>
  );
}
