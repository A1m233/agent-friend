import { useCallback, useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { Archive, MoreVertical, Pencil, SquarePen } from "lucide-react";
import {
  ConfirmDialog,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  Input,
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  TextEllipsis,
} from "@/components/ui";
import {
  getChatUiPersistence,
  resolveRestorableChatSessionId,
  setLastChatSessionId,
} from "@/lib/persistence/chatUi";
import { useConnectionStore, useConversationStore, useSessionsStore } from "@/stores";
import { isTauri } from "@/utils/tauri";

const WINDOW_SHOWN_EVENT = "window://shown";
const MINUTE_MS = 60 * 1000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

function formatSessionAge(input: string, now = Date.now()): string {
  const timestamp = new Date(input).getTime();
  if (!Number.isFinite(timestamp)) return "";

  const diff = Math.max(0, now - timestamp);
  if (diff < MINUTE_MS) return "刚刚";
  if (diff < HOUR_MS) return `${Math.floor(diff / MINUTE_MS)} 分钟`;
  if (diff < DAY_MS) return `${Math.floor(diff / HOUR_MS)} 小时`;
  if (diff < 30 * DAY_MS) return `${Math.floor(diff / DAY_MS)} 天`;

  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
  }).format(timestamp);
}

/**
 * 历史会话侧栏：列表展示 + 切换（R-M3.4）。bridge 为真相源，列表来自 sessions store。
 * 用 shadcn Sidebar（collapsible="none" 固定面板）；会话项 = SidebarMenuButton（isActive 高亮）。
 * 外层需由 ChatApp 套 SidebarProvider。
 *
 * **18 改连接状态驱动 refresh**：原 mount-only refresh 在 dev cold start 时 bridge 还没
 * ready → fetch 失败 list 留空；之后 bridge 起来也不会重拉。改为 status === "online" 时
 * refresh，让连接 unknown→online / offline→online 转换都自动重拉历史列表。
 */
export function HistorySidebar() {
  const list = useSessionsStore((s) => s.list);
  const refresh = useSessionsStore((s) => s.refresh);
  const renameSession = useSessionsStore((s) => s.rename);
  const archiveSession = useSessionsStore((s) => s.archive);
  const sessionsLoaded = useSessionsStore((s) => s.loaded);
  const status = useConnectionStore((s) => s.status);
  const currentSessionId = useConversationStore((s) => s.currentSessionId);
  const openSession = useConversationStore((s) => s.openSession);
  const newSession = useConversationStore((s) => s.newSession);
  const [renamingSession, setRenamingSession] = useState<{
    sessionId: string;
    title: string;
  } | null>(null);
  const [renameText, setRenameText] = useState("");
  const [renamePending, setRenamePending] = useState(false);
  const [archivingSession, setArchivingSession] = useState<{
    sessionId: string;
    title: string;
  } | null>(null);
  const [archivePending, setArchivePending] = useState(false);
  const [hoveredActionSessionId, setHoveredActionSessionId] = useState<string | null>(
    null,
  );
  const [focusedActionSessionId, setFocusedActionSessionId] = useState<string | null>(
    null,
  );
  const [openActionSessionId, setOpenActionSessionId] = useState<string | null>(null);
  const suppressNextActionFocusRef = useRef(false);
  const [persistedSessionId, setPersistedSessionId] = useState<
    string | null | undefined
  >();
  const [now, setNow] = useState(() => Date.now());
  const restoreAttemptedRef = useRef(false);
  const restoreBlockedRef = useRef(false);

  const blockAutoRestore = useCallback(() => {
    restoreBlockedRef.current = true;
  }, []);

  const handleNewSession = useCallback(() => {
    blockAutoRestore();
    newSession();
  }, [blockAutoRestore, newSession]);

  const handleOpenSession = useCallback(
    (sessionId: string) => {
      blockAutoRestore();
      void openSession(sessionId);
    },
    [blockAutoRestore, openSession],
  );

  const handleOpenRename = useCallback((sessionId: string, title: string) => {
    setRenamingSession({ sessionId, title });
    setRenameText(title);
  }, []);

  const handleConfirmRename = useCallback(() => {
    const target = renamingSession;
    const nextTitle = renameText.trim();
    if (!target || !nextTitle) return;

    setRenamePending(true);
    void renameSession(target.sessionId, nextTitle).then((ok) => {
      setRenamePending(false);
      if (ok) {
        setRenamingSession(null);
        setRenameText("");
      }
    });
  }, [renameSession, renameText, renamingSession]);

  const handleConfirmArchive = useCallback(() => {
    const target = archivingSession;
    if (!target) return;

    setArchivePending(true);
    void archiveSession(target.sessionId).then((ok) => {
      setArchivePending(false);
      if (ok) {
        if (target.sessionId === currentSessionId) {
          blockAutoRestore();
          newSession();
        }
        setArchivingSession(null);
      }
    });
  }, [archiveSession, archivingSession, blockAutoRestore, currentSessionId, newSession]);

  useEffect(() => {
    let alive = true;

    getChatUiPersistence()
      .then((state) => {
        if (alive) setPersistedSessionId(state.lastChatSessionId);
      })
      .catch(() => {
        if (alive) setPersistedSessionId(null);
      });

    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (status === "online") {
      void refresh();
    }
  }, [status, refresh]);

  useEffect(() => {
    if (status !== "online") return;

    const refreshList = () => {
      setNow(Date.now());
      void refresh();
    };
    const refreshListWhenVisible = () => {
      if (document.visibilityState === "visible") refreshList();
    };
    window.addEventListener("focus", refreshList);
    document.addEventListener("visibilitychange", refreshListWhenVisible);

    const unlistenShown = isTauri() ? listen(WINDOW_SHOWN_EVENT, refreshList) : null;

    return () => {
      window.removeEventListener("focus", refreshList);
      document.removeEventListener("visibilitychange", refreshListWhenVisible);
      void unlistenShown?.then((unlisten) => unlisten());
    };
  }, [refresh, status]);

  useEffect(() => {
    if (!sessionsLoaded || !currentSessionId) return;
    if (list.some((s) => s.session_id === currentSessionId)) return;
    blockAutoRestore();
    newSession();
  }, [blockAutoRestore, currentSessionId, list, newSession, sessionsLoaded]);

  useEffect(() => {
    if (list.length === 0) return;

    const updateNow = () => setNow(Date.now());
    const delayToNextMinute = MINUTE_MS - (Date.now() % MINUTE_MS);
    let interval: number | undefined;
    const timeout = window.setTimeout(() => {
      updateNow();
      interval = window.setInterval(updateNow, MINUTE_MS);
    }, delayToNextMinute);

    return () => {
      window.clearTimeout(timeout);
      if (interval !== undefined) window.clearInterval(interval);
    };
  }, [list.length]);

  useEffect(() => {
    if (
      restoreAttemptedRef.current ||
      restoreBlockedRef.current ||
      persistedSessionId === undefined ||
      !sessionsLoaded ||
      currentSessionId
    ) {
      return;
    }

    restoreAttemptedRef.current = true;
    const restorableSessionId = resolveRestorableChatSessionId(persistedSessionId, list);
    if (!restorableSessionId) {
      if (persistedSessionId) void setLastChatSessionId(null);
      return;
    }

    void openSession(restorableSessionId, { failureMode: "home", remember: false }).then(
      (opened) => {
        if (!opened && !restoreBlockedRef.current) {
          void setLastChatSessionId(null);
        }
      },
    );
  }, [currentSessionId, list, openSession, persistedSessionId, sessionsLoaded]);

  return (
    <Sidebar collapsible="none" className="border-r border-border gap-1">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton onClick={handleNewSession}>
              <SquarePen />
              <span>新对话</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="text-sm font-normal">对话</SidebarGroupLabel>
          <SidebarGroupContent>
            {list.length === 0 ? (
              <p className="px-2 py-2 text-xs text-muted">还没有历史会话</p>
            ) : (
              <SidebarMenu className="gap-1">
                {list.map((s) => {
                  const ageText = formatSessionAge(s.updated_at || s.created_at, now);
                  const title = s.title || "新对话";
                  const actionVisible =
                    openActionSessionId === s.session_id ||
                    (!openActionSessionId &&
                      (hoveredActionSessionId === s.session_id ||
                        focusedActionSessionId === s.session_id));
                  return (
                    <SidebarMenuItem
                      key={s.session_id}
                      onPointerEnter={() => setHoveredActionSessionId(s.session_id)}
                      onPointerLeave={() =>
                        setHoveredActionSessionId((current) =>
                          current === s.session_id ? null : current,
                        )
                      }
                      onFocus={() => setFocusedActionSessionId(s.session_id)}
                      onBlur={(event) => {
                        const nextTarget = event.relatedTarget;
                        if (
                          nextTarget instanceof Node &&
                          event.currentTarget.contains(nextTarget)
                        ) {
                          return;
                        }
                        setFocusedActionSessionId((current) =>
                          current === s.session_id ? null : current,
                        );
                      }}
                    >
                      <SidebarMenuButton
                        isActive={s.session_id === currentSessionId}
                        className="px-2 text-sm font-normal data-[active=true]:bg-bg transition-none"
                        onClick={() => handleOpenSession(s.session_id)}
                      >
                        <div className="flex min-w-0 flex-1 items-center gap-2">
                          <TextEllipsis className="min-w-0 flex-1">{title}</TextEllipsis>
                          {!actionVisible && ageText ? (
                            <span
                              className="shrink-0 tabular-nums text-xs text-muted"
                              title={ageText}
                            >
                              {ageText}
                            </span>
                          ) : null}
                        </div>
                      </SidebarMenuButton>
                      {actionVisible ? (
                        <DropdownMenu
                          open={openActionSessionId === s.session_id}
                          onOpenChange={(open) => {
                            setOpenActionSessionId(open ? s.session_id : null);
                            if (!open) {
                              setFocusedActionSessionId((current) =>
                                current === s.session_id ? null : current,
                              );
                            }
                          }}
                        >
                          <DropdownMenuTrigger asChild>
                            <SidebarMenuAction
                              aria-label={`打开 ${title} 的更多操作`}
                              onClick={(event) => {
                                event.stopPropagation();
                              }}
                            >
                              <MoreVertical />
                            </SidebarMenuAction>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent
                            side="right"
                            align="start"
                            onCloseAutoFocus={(event) => {
                              if (!suppressNextActionFocusRef.current) return;
                              suppressNextActionFocusRef.current = false;
                              event.preventDefault();
                            }}
                            onInteractOutside={() => {
                              suppressNextActionFocusRef.current = true;
                              setHoveredActionSessionId((current) =>
                                current === s.session_id ? null : current,
                              );
                              setFocusedActionSessionId((current) =>
                                current === s.session_id ? null : current,
                              );
                            }}
                            onClick={(event) => event.stopPropagation()}
                          >
                            <DropdownMenuItem
                              onSelect={() => handleOpenRename(s.session_id, title)}
                            >
                              <Pencil />
                              重命名
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onSelect={() =>
                                setArchivingSession({ sessionId: s.session_id, title })
                              }
                            >
                              <Archive />
                              归档
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      ) : null}
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            )}
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <ConfirmDialog
        open={renamingSession !== null}
        title="重命名对话"
        confirmLabel="保存"
        cancelLabel="取消"
        loading={renamePending}
        disabled={!renameText.trim()}
        onCancel={() => {
          if (renamePending) return;
          setRenamingSession(null);
          setRenameText("");
        }}
        onConfirm={handleConfirmRename}
      >
        <Input
          autoFocus
          value={renameText}
          maxLength={80}
          aria-label="对话名称"
          onChange={(event) => setRenameText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") handleConfirmRename();
          }}
        />
      </ConfirmDialog>
      <ConfirmDialog
        open={archivingSession !== null}
        title="归档对话？"
        description={
          archivingSession
            ? `“${archivingSession.title}” 会从侧栏移除，可在设置中心的已归档对话中找回。`
            : undefined
        }
        confirmLabel="归档"
        cancelLabel="取消"
        loading={archivePending}
        onCancel={() => {
          if (archivePending) return;
          setArchivingSession(null);
        }}
        onConfirm={handleConfirmArchive}
      />
    </Sidebar>
  );
}
