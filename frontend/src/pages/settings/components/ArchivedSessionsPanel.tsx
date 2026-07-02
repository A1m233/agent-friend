import { useEffect, useMemo, useState } from "react";
import { ArchiveRestore, Search, Trash2 } from "lucide-react";
import {
  Button,
  ConfirmDialog,
  Input,
  Spinner,
  TextEllipsis,
} from "@/components/ui";
import { useArchivedSessionsStore } from "@/stores";
import type { SessionSummary } from "@/types/meta";
import { cn } from "@/utils/cn";
import { SettingsPageLayout } from "./SettingsPageLayout";

const QUERY_DEBOUNCE_MS = 250;

function formatArchivedAt(session: SessionSummary): string {
  const input = session.archived_at ?? session.updated_at ?? session.created_at;
  const date = new Date(input);
  if (!Number.isFinite(date.getTime())) return "";

  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function ArchivedSessionsPanel() {
  const list = useArchivedSessionsStore((s) => s.list);
  const query = useArchivedSessionsStore((s) => s.query);
  const loading = useArchivedSessionsStore((s) => s.loading);
  const loaded = useArchivedSessionsStore((s) => s.loaded);
  const pendingIds = useArchivedSessionsStore((s) => s.pendingIds);
  const deletingAll = useArchivedSessionsStore((s) => s.deletingAll);
  const setQuery = useArchivedSessionsStore((s) => s.setQuery);
  const refresh = useArchivedSessionsStore((s) => s.refresh);
  const unarchive = useArchivedSessionsStore((s) => s.unarchive);
  const remove = useArchivedSessionsStore((s) => s.remove);
  const removeAll = useArchivedSessionsStore((s) => s.removeAll);
  const [deleteTarget, setDeleteTarget] = useState<SessionSummary | null>(null);
  const [deleteAllOpen, setDeleteAllOpen] = useState(false);
  const [hoveredActionSessionId, setHoveredActionSessionId] = useState<string | null>(
    null,
  );
  const [focusedActionSessionId, setFocusedActionSessionId] = useState<string | null>(
    null,
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh();
    }, QUERY_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query, refresh]);

  const pendingSet = useMemo(() => new Set(pendingIds), [pendingIds]);
  const canDeleteAll = list.length > 0 || query.trim().length > 0;

  return (
    <SettingsPageLayout
      title="已归档对话"
      actions={
        <Button
          variant="destructive"
          size="sm"
          loading={deletingAll}
          disabled={!canDeleteAll || loading}
          onClick={() => setDeleteAllOpen(true)}
        >
          <Trash2 />
          全部删除
        </Button>
      }
    >
      <div className="rounded-lg border border-border bg-surface/50">
        <div className="border-b border-border p-4">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
            <Input
              value={query}
              placeholder="搜索已归档聊天"
              className="pl-9"
              aria-label="搜索已归档聊天"
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </div>

        {loading && !loaded ? (
          <div className="flex h-32 items-center justify-center text-muted">
            <Spinner />
          </div>
        ) : list.length === 0 ? (
          <div className="flex h-32 items-center justify-center text-sm text-muted">
            {query.trim() ? "没有匹配的已归档对话" : "还没有已归档对话"}
          </div>
        ) : (
          <div className={cn("divide-y divide-border", loading && "opacity-70")}>
            {list.map((session) => {
              const title = session.title || "新对话";
              const pending = pendingSet.has(session.session_id);
              const actionsVisible =
                pending ||
                deleteTarget?.session_id === session.session_id ||
                hoveredActionSessionId === session.session_id ||
                focusedActionSessionId === session.session_id;
              return (
                <div
                  key={session.session_id}
                  className="flex min-h-16 items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-hover/60"
                  onPointerEnter={() => setHoveredActionSessionId(session.session_id)}
                  onPointerLeave={() =>
                    setHoveredActionSessionId((current) =>
                      current === session.session_id ? null : current,
                    )
                  }
                  onFocus={() => setFocusedActionSessionId(session.session_id)}
                  onBlur={(event) => {
                    const nextTarget = event.relatedTarget;
                    if (
                      nextTarget instanceof Node &&
                      event.currentTarget.contains(nextTarget)
                    ) {
                      return;
                    }
                    setFocusedActionSessionId((current) =>
                      current === session.session_id ? null : current,
                    );
                  }}
                >
                  <div className="min-w-0 flex-1">
                    <TextEllipsis className="text-sm font-medium">{title}</TextEllipsis>
                    <div className="mt-1 text-xs tabular-nums text-muted">
                      {formatArchivedAt(session)}
                    </div>
                  </div>
                  <div
                    data-archived-actions
                    className={cn(
                      "flex shrink-0 items-center gap-1 transition-opacity",
                      actionsVisible ? "opacity-100" : "pointer-events-none opacity-0",
                    )}
                  >
                    <Button
                      variant="destructive"
                      size="icon-sm"
                      disabled={pending}
                      aria-label={`删除 ${title}`}
                      onClick={() => setDeleteTarget(session)}
                    >
                      <Trash2 />
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      loading={pending}
                      disabled={pending}
                      onClick={() => void unarchive(session.session_id)}
                    >
                      <ArchiveRestore />
                      取消归档
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除已归档聊天？"
        description="这将永久删除已归档聊天。"
        confirmLabel="删除"
        cancelLabel="取消"
        variant="destructive"
        loading={deleteTarget ? pendingSet.has(deleteTarget.session_id) : false}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          const target = deleteTarget;
          if (!target) return;
          void remove(target.session_id).then((ok) => {
            if (ok) setDeleteTarget(null);
          });
        }}
      />
      <ConfirmDialog
        open={deleteAllOpen}
        title="删除全部已归档聊天？"
        description="这将永久删除所有已归档聊天。"
        confirmLabel="全部删除"
        cancelLabel="取消"
        variant="destructive"
        loading={deletingAll}
        onCancel={() => setDeleteAllOpen(false)}
        onConfirm={() => {
          void removeAll().then((ok) => {
            if (ok) setDeleteAllOpen(false);
          });
        }}
      />
    </SettingsPageLayout>
  );
}
