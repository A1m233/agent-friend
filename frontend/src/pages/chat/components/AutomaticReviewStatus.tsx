import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui";
import { http } from "@/services/http";
import { useCommandRuntimeStore, useConversationStore, useFilesystemApprovalsStore } from "@/stores";
import { sessionActiveRun } from "@/stores/runPresentation";

const LABELS: Record<string, string> = {
  denied: "这项操作未获放行", timed_out: "审批超时，操作未执行",
  failed: "审批出错，操作未执行", aborted: "审批已中止，操作未执行",
};

export function AutomaticReviewStatus({ toolCallId }: { toolCallId: string }) {
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
      && request.tool_call_id === toolCallId);
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
    <div className="mb-2 space-y-1 text-xs text-muted">
      <div className="flex flex-wrap items-center gap-2">
        <span role="status">{LABELS[request.status]}</span>
        {canRetry && (
          <Button size="xs" variant="ghost" loading={submitting} onClick={() => void retry()}>
            重新审批
          </Button>
        )}
      </div>
      {request.review?.rationale && <p className="break-words">{request.review.rationale}</p>}
    </div>
  );
}
