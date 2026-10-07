import { useEffect, useState } from "react";
import { Button } from "@/components/ui";
import { http, friendlyErrorMessage } from "@/services/http";
import { runsApi } from "@/services/api/runs";
import { subscribePushInvalidation } from "@/services/pushInvalidation";
import { openScheduledTask } from "@/pages/scheduled-tasks/navigation";

interface Receipt {
  run_id: string;
  status: string;
  input_accepted: boolean;
  input: {
    text: string;
    source?: { task_id: string; task_name: string } | null;
    options?: { attachments?: { display_name: string }[] };
  };
}

export function PendingRequests({ sessionId }: { sessionId: string | null }) {
  const [snapshot, setSnapshot] = useState<{
    sessionId: string;
    items: Receipt[];
  } | null>(null);
  const items = snapshot?.sessionId === sessionId ? snapshot.items : [];
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState<string | null>(null);
  useEffect(() => {
    if (!sessionId) return;
    let active = true;
    let loading = false;
    async function load() {
      if (loading) return;
      loading = true;
      try {
        const result = await http.get<unknown, { requests: Receipt[] }>(
          `/v1/sessions/${encodeURIComponent(sessionId!)}/requests`,
        );
        if (active)
          setSnapshot({
            sessionId: sessionId!,
            items: result.requests.filter(
              (item) => item.status === "queued" && !item.input_accepted,
            ),
          });
      } catch {
        /* Connection status is displayed by the chat surface. */
      } finally {
        loading = false;
      }
    }
    void load();
    const unsubscribe = subscribePushInvalidation(
      [
        "session://run-changed",
        "session://changed",
        "session://push-reconnected",
      ],
      () => void load(),
    );
    const timer = window.setInterval(() => void load(), 1000);
    return () => {
      active = false;
      unsubscribe();
      window.clearInterval(timer);
    };
  }, [sessionId]);
  if (!sessionId || !items.length) return null;
  return (
    <section
      aria-label="待发送请求"
      className="pointer-events-auto mx-auto mb-2 max-h-44 w-full max-w-3xl overflow-y-auto rounded-lg border border-border bg-surface p-3 text-sm"
    >
      <h2 className="mb-2 font-medium">待发送 · {items.length}</h2>
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
      {items.map((item, index) => (
        <div key={item.run_id} className="flex items-center gap-2 py-1">
          <span className="text-muted">{index + 1}.</span>
          <div className="min-w-0 flex-1">
            <p className="truncate">
              {item.input.text ||
                item.input.options?.attachments
                  ?.map((a) => a.display_name)
                  .join("、")}
            </p>
            {item.input.source && (
              <Button
                className="h-auto p-0 text-xs"
                variant="link"
                onClick={() => openScheduledTask(item.input.source!.task_id)}
              >
                {item.input.source.task_name}
              </Button>
            )}
          </div>
          <Button
            variant="ghost"
            size="sm"
            disabled={cancelling === item.run_id}
            onClick={() => {
              setCancelling(item.run_id);
              setError("");
              void runsApi
                .cancel(sessionId, item.run_id)
                .then(() =>
                  setSnapshot((current) =>
                    current?.sessionId === sessionId
                      ? {
                          ...current,
                          items: current.items.filter(
                            (row) => row.run_id !== item.run_id,
                          ),
                        }
                      : current,
                  ),
                )
                .catch((e) => setError(friendlyErrorMessage(e)))
                .finally(() => setCancelling(null));
            }}
          >
            取消
          </Button>
        </div>
      ))}
    </section>
  );
}
