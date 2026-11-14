import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp, X } from "lucide-react";
import { Button, Input } from "@/components/ui";
import { sessionsApi } from "@/services/api/sessions";
import { useConversationStore } from "@/stores/conversation";
import { findOccurrences, prepareFindBlocks, type FindBlock } from "./conversationFindText";

export function ConversationFind({ sessionId }: { sessionId: string | null }) {
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const close = useCallback(() => {
    setOpen(false);
    previousFocus.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.isComposing || event.defaultPrevented) return;
      if (event.key.toLowerCase() === "f" && (event.ctrlKey || event.metaKey) && !event.altKey) {
        if (event.shiftKey) {
          if (open) close();
          return;
        }
        if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
        event.preventDefault();
        if (!open) previousFocus.current = document.activeElement as HTMLElement | null;
        setOpen(true);
        input.current?.focus();
        input.current?.select();
      } else if (open && event.key === "Escape") {
        event.preventDefault();
        close();
      }
    };
    window.addEventListener("keydown", keydown, true);
    return () => window.removeEventListener("keydown", keydown, true);
  }, [open, close]);

  return open ? <FindBar sessionId={sessionId} input={input} close={close} /> : null;
}

function FindBar({ sessionId, input, close }: {
  sessionId: string | null; input: React.RefObject<HTMLInputElement | null>; close: () => void;
}) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const [blocks, setBlocks] = useState<FindBlock[] | null>(null);
  const [failed, setFailed] = useState(false);
  const syncSequence = useConversationStore(state => state.syncRefreshSeq);
  useEffect(() => {
    input.current?.focus();
  }, [input]);
  useEffect(() => {
    if (!sessionId) {
      setBlocks([]);
      return;
    }
    const request = new AbortController();
    void sessionsApi.searchText(sessionId, request.signal)
      .then(rows => prepareFindBlocks(rows, request.signal))
      .then(rows => {
        if (!request.signal.aborted) {
          setBlocks(rows);
          setFailed(false);
        }
      })
      .catch(() => { if (!request.signal.aborted) setFailed(true); });
    return () => request.abort();
  }, [sessionId, syncSequence]);

  const hits = useMemo(() => query && blocks ? findOccurrences(blocks, query) : [], [query, blocks]);
  const selected = Math.min(index, Math.max(hits.length - 1, 0));
  const hit = hits[selected];
  const anchor = hit?.anchor;
  const occurrence = hit?.occurrence;
  useEffect(() => {
    const store = useConversationStore.getState();
    if (anchor && sessionId) void store.openMessage(sessionId, anchor, { query, occurrence });
    return () => store.cancelConversationFind();
  }, [anchor, occurrence, sessionId, query]);

  function move(delta: number) {
    if (hits.length) setIndex((selected + delta + hits.length) % hits.length);
  }
  return <div className="flex shrink-0 justify-end px-2 pt-2 pr-(--chat-corner-clearance)">
    <div role="search" aria-label="会话内查找"
      className="flex min-w-0 max-w-full items-center gap-1 rounded-lg border border-border bg-surface p-1 shadow-md">
    <Input ref={input} aria-label="查找当前会话" placeholder="查找当前会话" value={query}
      className="h-8 w-44 min-w-0" onChange={e => { setQuery(e.target.value); setIndex(0); }}
      onKeyDown={e => {
        if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); move(e.shiftKey ? -1 : 1); }
      }} />
    <span role="status" className="whitespace-nowrap px-1 text-xs tabular-nums text-muted">
      {failed ? "读取失败" : !blocks ? "读取中" : query ? (hit ? `${selected + 1}/${hits.length}` : "0/0") : ""}
    </span>
    <Button size="icon-sm" variant="ghost" aria-label="上一处" disabled={!hit} onClick={() => move(-1)}><ChevronUp /></Button>
    <Button size="icon-sm" variant="ghost" aria-label="下一处" disabled={!hit} onClick={() => move(1)}><ChevronDown /></Button>
    <Button size="icon-sm" variant="ghost" aria-label="关闭查找" onClick={close}><X /></Button>
    </div>
  </div>;
}
