import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { X } from "lucide-react";
import { Button } from "@/components/ui";
import { usePetBubbleStore } from "@/stores/petBubble";
import {
  dismissNotification, prepareNotification, selectBubbleNotifications, useNotificationStore,
} from "@/stores/notifications";
import { BUBBLE_VISIBLE_TOASTS } from "./bubbleWindowSize";
import { useBubblePresentation } from "./useBubblePresentation";
import "./PetBubble.css";

const TRUNCATE_LEN = 120;

export function notificationDisplayText(text: string, expanded: boolean) {
  const characters = Array.from(text);
  const truncated = characters.length > TRUNCATE_LEN && !expanded;
  return { text: truncated ? characters.slice(0, TRUNCATE_LEN).join("") + "…" : text, truncated };
}

interface VisualRow {
  id: string;
  source: "notification" | "legacy";
  sourceTitle: string | null;
  text: string;
  expanded: boolean;
  sessionId: string | null;
  state: "present" | "exiting";
}

function reconcileRows(previous: VisualRow[], desired: VisualRow[]): VisualRow[] {
  const wanted = new Set(desired.map((row) => row.id));
  return [
    ...desired,
    ...previous.filter((row) => !wanted.has(row.id)).map((row): VisualRow =>
      row.state === "exiting" ? row : { ...row, state: "exiting" }),
  ];
}

/** The ledger selects rows; this owner retains their DOM until the shared exit finishes. */
export function PetBubble() {
  const items = usePetBubbleStore((state) => state.items);
  const expandLegacy = usePetBubbleStore((state) => state.expand);
  const dismissLegacy = usePetBubbleStore((state) => state.dismiss);
  const records = useNotificationStore((state) => state.records);
  const expanded = useNotificationStore((state) => state.expanded);
  const ready = useNotificationStore((state) => state.ready);
  const ownedAttempts = useNotificationStore((state) => state.ownedAttempts);
  const [closing, setClosing] = useState<ReadonlySet<string>>(() => new Set());
  const closingIds = useRef(new Set<string>());
  const viewport = useRef<HTMLElement>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const notifications = useMemo(() => {
    if (!ready) return [];
    const available = Object.fromEntries(Object.entries(records).filter(([id]) => !closing.has(id)));
    return selectBubbleNotifications(available);
  }, [records, ready, closing]);

  const desired = useMemo<VisualRow[]>(() => {
    const prepared = notifications.filter((item) => item.delivery.status === "presented"
      || (item.delivery.status === "prepared" && ownedAttempts[item.notificationId] === item.delivery.attempt));
    const legacy = items.filter((item) => !closing.has(item.id))
      .slice(0, Math.max(0, BUBBLE_VISIBLE_TOASTS - notifications.length));
    return [
      ...prepared.map((item): VisualRow => ({
        id: item.notificationId, source: "notification", text: item.text,
        sourceTitle: item.title
          ? item.kind === "todo_due" ? `待办 · ${item.title}` : item.sessionId ? item.title : null
          : null,
        expanded: expanded[item.notificationId] ?? false,
        sessionId: item.navigation?.sessionId ?? null, state: "present",
      })),
      ...legacy.map((item): VisualRow => ({
        id: item.id, source: "legacy", sourceTitle: null, text: item.text, expanded: item.expanded,
        sessionId: null, state: "present",
      })),
    ];
  }, [notifications, ownedAttempts, items, expanded, closing]);
  const [visualRows, setVisualRows] = useState<VisualRow[]>(() => desired);

  useLayoutEffect(() => {
    setVisualRows((previous) => reconcileRows(previous, desired));
  }, [desired]);

  const onExitComplete = useCallback((ids: string[]) => {
    const finished = new Set(ids);
    setVisualRows((rows) => rows.filter((row) => row.state !== "exiting" || !finished.has(row.id)));
  }, []);
  useBubblePresentation({ onExitComplete });

  useEffect(() => {
    for (const item of notifications) void prepareNotification(item.notificationId);
  }, [notifications, ownedAttempts]);

  const close = (row: VisualRow) => {
    if (row.state === "exiting" || closingIds.current.has(row.id)) return;
    const current = [...(viewport.current?.querySelectorAll<HTMLElement>(
      '[data-bubble-item][data-state="present"]',
    ) ?? [])];
    const index = current.findIndex((element) => element.dataset.bubbleId === row.id);
    const remaining = [...current.slice(index + 1), ...current.slice(0, index).reverse()]
      .filter((element) => !closingIds.current.has(element.dataset.bubbleId!));
    const nextFocus = remaining[0]?.querySelector<HTMLButtonElement>("button:not(:disabled)");
    (nextFocus ?? viewport.current)?.focus({ preventScroll: true });

    closingIds.current.add(row.id);
    setClosing(new Set(closingIds.current));
    void (async () => {
      try {
        if (row.source === "legacy") dismissLegacy(row.id);
        else await dismissNotification(row.id);
      } catch (error) {
        console.warn("notification close failed", error);
      } finally {
        // A successful request leaves a terminal ledger row. If it failed, the
        // active row becomes eligible again even if its exit has already ended.
        closingIds.current.delete(row.id);
        if (alive.current) setClosing(new Set(closingIds.current));
      }
    })();
  };

  return <section
    ref={viewport} data-bubble-viewport tabIndex={-1}
    aria-label="桌宠提醒" aria-live="polite" aria-relevant="additions text" aria-atomic={false}
  >
    <ol data-bubble-stack>
      {visualRows.map((row) => {
        const display = notificationDisplayText(row.text, row.expanded);
        const disabled = row.state === "exiting" || closing.has(row.id);
        return <li
          key={row.id} data-bubble-item data-bubble-id={row.id} data-state={row.state}
          aria-hidden={row.state === "exiting" ? true : undefined}
          className="pet-notification-row rounded-lg border border-border bg-surface text-fg shadow-md"
        >
          <Button
            data-close-button aria-label="关闭提醒" size="icon-xs" variant="ghost" disabled={disabled}
            className="pet-notification-close size-5 rounded-full border border-border bg-surface"
            onClick={() => close(row)}
          ><X className="size-3" /></Button>
          <div data-notification-content className="flex min-w-0 flex-1 flex-col gap-1">
            {row.sourceTitle && <span
              data-notification-source className="text-xs font-normal break-words text-muted"
            >{row.sourceTitle}</span>}
            <span
              data-notification-id={row.source === "notification" ? row.id : undefined}
              data-truncated={String(display.truncated)}
              className="min-w-0 whitespace-pre-wrap break-words"
            >{display.text}</span>
          </div>
          {(row.sessionId || display.truncated) && <div className="flex shrink-0 flex-col gap-1 self-center">
            {row.sessionId && <Button size="xs" variant="secondary" disabled={disabled} onClick={() => {
              void invoke("open_chat_session", { sessionId: row.sessionId })
                .then(() => { if (alive.current) close(row); })
                .catch((error: unknown) => console.warn("notification navigation unavailable", error));
            }}>查看</Button>}
            {display.truncated && <Button size="xs" variant="secondary" disabled={disabled} onClick={() => {
              if (row.source === "legacy") expandLegacy(row.id);
              else useNotificationStore.getState().expand(row.id);
            }}>展开</Button>}
          </div>}
        </li>;
      })}
    </ol>
  </section>;
}
