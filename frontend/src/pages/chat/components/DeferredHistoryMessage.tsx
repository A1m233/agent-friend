import { useEffect, useState, type ReactNode } from "react";
import type { ChatMessage } from "@/types/chat";
import { Button, Spinner } from "@/components/ui";
import { hasDeferredBody, projectHistoryEvent } from "@/stores/historyProjection";
import { useConversationStore } from "@/stores";
import { historySignal } from "@/stores/historyLifecycle";
import { historyFragmentPage, readHistoryEvent } from "@/services/historyContent";
import { SocialAuthCard } from "./SocialAuthCard";
import { ExecutionDisclosure } from "./ExecutionDisclosure";

/** Missing bodies are visible states, never empty text or a tool reported as still running. */
export function DeferredHistoryMessage({ message, children, reveal = false, footer }: {
  message: ChatMessage; children: (message: ChatMessage) => ReactNode; reveal?: boolean; footer?: ReactNode;
}) {
  const [loaded, setLoaded] = useState<ChatMessage | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const fragment = message.history;
  useEffect(() => { setLoaded(null); setFailed(false); }, [fragment?.version, fragment?.event]);
  const deferred = !!fragment && hasDeferredBody(fragment.event);
  const load = async () => {
    if (!fragment || loading) return;
    setLoading(true); setFailed(false);
    try {
      const signal = historySignal();
      const event = await readHistoryEvent(fragment.sessionId, fragment.version, fragment.event, signal);
      signal.throwIfAborted();
      const result = projectHistoryEvent(historyFragmentPage(fragment, event), event);
      if (!result) throw new Error("Message content missing");
      const projected = { ...result, history: { ...result.history!, first: fragment.first, last: fragment.last } };
      useConversationStore.setState((state) => {
        if (state.currentSessionId !== fragment.sessionId) return state;
        const messages = state.messages.map((current) => current.id === message.id
          && current.history?.version === fragment.version && current.history.event === fragment.event ? projected : current);
        const view = state.sessionViews[fragment.sessionId];
        return { messages, ...(view ? { sessionViews: { ...state.sessionViews,
          [fragment.sessionId]: { ...view, messages } } } : {}) };
      });
      setLoaded(projected);
    } catch { setFailed(true); }
    finally { setLoading(false); }
  };
  useEffect(() => { if (reveal && deferred && !loaded && !loading && !failed) void load(); });
  if (!deferred || loaded) return children(loaded ?? message);
  const event = fragment.event;
  const request = event.summary ?? event.payload ?? {};
  const result = event.result_event?.summary ?? event.result_event?.payload ?? {};
  const name = typeof event.payload?.tool_name === "string" ? event.payload.tool_name : "";
  const status = typeof result.status === "string" ? result.status
    : event.result_event ? result.is_error === true ? "执行失败" : "已完成" : "尚无结果";
  const process = (event.type === "tool_call_request" && name !== "request_social_auth")
    || (event.type === "assistant_message" && event.payload?.phase === "commentary");
  return <ExecutionDisclosure enabled={process} label={null} showHeader={false}
    collapse={message.runOutcome === "completed"} disclosureId={message.runId ?? message.id}
    revealTarget={reveal ? event.uuid : undefined}>
    {(open) => <>{open && <div className="flex flex-col items-start gap-2 rounded-md border border-border p-3 text-sm">
    <div className="text-muted">{name ? `${name} · ${status}` : "这段内容较长，按需加载完整正文。"}</div>
    {typeof result.exit_code === "number" && <div className="text-muted">退出码：{result.exit_code}</div>}
    {name === "request_social_auth" && <SocialAuthCard historySummary={{ request, result }} />}
    <Button variant="outline" size="sm" disabled={loading} onClick={() => void load()}>
      {loading && <Spinner />}{loading ? "正在加载完整内容…" : failed ? "重新加载内容" : "加载完整内容"}
    </Button>
    {failed && <div role="status" className="text-muted">内容尚未加载，现有消息保留。</div>}
  </div>}{footer}</>}
  </ExecutionDisclosure>;
}
