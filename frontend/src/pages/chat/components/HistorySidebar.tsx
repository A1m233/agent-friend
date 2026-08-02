import { useCallback, useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { Circle, CircleAlert, Clock3, MoreVertical, SquarePen } from "lucide-react";
import {
  Badge,
  ConfirmDialog,
  ContextMenu,
  ContextMenuContent,
  ContextMenuTrigger,
  DropdownMenu,
  DropdownMenuContent,
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
  Spinner,
  TextEllipsis,
} from "@/components/ui";
import type { IMType } from "@/services";
import {
  getChatUiPersistence,
  resolveRestorableChatSessionId,
  setLastChatSessionId,
} from "@/lib/persistence/chatUi";
import {
  useConnectionStore,
  useConversationStore,
  useDraftStore,
  useSessionsStore,
} from "@/stores";
import type { RunSnapshot } from "@/types/run";
import { isTauri } from "@/utils/tauri";
import {
  createSessionMenuActions,
  SessionContextMenuItems,
  SessionDropdownMenuItems,
} from "./SessionActionMenuItems";

const WINDOW_SHOWN_EVENT = "window://shown";
const MINUTE_MS = 60 * 1000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

interface MissingCurrentSessionResetInput {
  sessionsLoaded: boolean;
  currentSessionId: string | null;
  sessionIds: string[];
  streaming: boolean;
  historyLoading: boolean;
  messageCount: number;
}

export type SessionRunIndicator = "queued" | "running" | "completed" | "failed" | null;

type SessionActionMenuSurface = "context" | "dropdown";

export interface OpenSessionActionMenu {
  sessionId: string;
  surface: SessionActionMenuSurface;
}

export function resolveOpenSessionActionMenu(
  current: OpenSessionActionMenu | null,
  sessionId: string,
  surface: SessionActionMenuSurface,
  open: boolean,
): OpenSessionActionMenu | null {
  if (open) return { sessionId, surface };
  return current?.sessionId === sessionId && current.surface === surface ? null : current;
}

export function sessionRunIndicator(
  activeRun: RunSnapshot | undefined,
  unreadOutcome: "completed" | "failed" | undefined,
): SessionRunIndicator {
  if (activeRun?.status === "queued") return "queued";
  if (activeRun?.status === "running") return "running";
  return unreadOutcome ?? null;
}

export function shouldClearSessionActionFocusOnOpen(clickDetail: number): boolean {
  return clickDetail > 0;
}

export function shouldResetMissingCurrentSession({
  sessionsLoaded,
  currentSessionId,
  sessionIds,
  streaming,
  historyLoading,
  messageCount,
}: MissingCurrentSessionResetInput): boolean {
  if (!sessionsLoaded || !currentSessionId) return false;
  if (sessionIds.includes(currentSessionId)) return false;
  if (streaming || historyLoading) return false;
  return messageCount === 0;
}

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
  const imTypesBySession = useSessionsStore((s) => s.imTypesBySession);
  const refresh = useSessionsStore((s) => s.refresh);
  const renameSession = useSessionsStore((s) => s.rename);
  const archiveSession = useSessionsStore((s) => s.archive);
  const sessionsLoaded = useSessionsStore((s) => s.loaded);
  const status = useConnectionStore((s) => s.status);
  const currentSessionId = useConversationStore((s) => s.currentSessionId);
  const streaming = useConversationStore((s) => s.streaming);
  const historyLoading = useConversationStore((s) => s.historyLoading);
  const messageCount = useConversationStore((s) => s.messages.length);
  const openSession = useConversationStore((s) => s.openSession);
  const newSession = useConversationStore((s) => s.newSession);
  const activeRunIdBySession = useConversationStore((s) => s.activeRunIdBySession);
  const runSnapshots = useConversationStore((s) => s.runSnapshots);
  const unreadRunOutcomeBySession = useConversationStore((s) => s.unreadRunOutcomeBySession);
  const draftWorkspaces = useDraftStore((s) => s.workspaces);
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
  const [openActionMenu, setOpenActionMenu] = useState<OpenSessionActionMenu | null>(null);
  const suppressNextActionFocusRef = useRef(false);
  const pointerContextMenuSessionRef = useRef<string | null>(null);
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

  const handleSessionActionMenuOpenChange = useCallback(
    (sessionId: string, surface: SessionActionMenuSurface, open: boolean) => {
      setOpenActionMenu((current) => {
        return resolveOpenSessionActionMenu(current, sessionId, surface, open);
      });
    },
    [],
  );

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
    const shouldReset = shouldResetMissingCurrentSession({
      sessionsLoaded,
      currentSessionId,
      sessionIds: list.map((s) => s.session_id),
      streaming,
      historyLoading,
      messageCount,
    });
    if (!shouldReset) return;
    blockAutoRestore();
    newSession();
  }, [
    blockAutoRestore,
    currentSessionId,
    historyLoading,
    list,
    messageCount,
    newSession,
    sessionsLoaded,
    streaming,
  ]);

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

  const openActionSessionId = openActionMenu?.sessionId ?? null;

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
                  const boundIMTypes = imTypesBySession[s.session_id] ?? [];
                  const activeRunId = activeRunIdBySession[s.session_id];
                  const activeRun = activeRunId ? runSnapshots[activeRunId] : undefined;
                  const unreadOutcome = unreadRunOutcomeBySession[s.session_id];
                  const runIndicator = sessionRunIndicator(activeRun, unreadOutcome);
                  const draft = draftWorkspaces[s.session_id];
                  const hasDraft = Boolean(draft?.text || draft?.attachments.length);
                  const sessionActionContext = {
                    sessionId: s.session_id,
                    title,
                    hasActiveRun: Boolean(activeRun),
                    hasDraft,
                  };
                  const sessionActions = createSessionMenuActions(sessionActionContext, {
                    rename: ({ sessionId, title: sessionTitle }) =>
                      handleOpenRename(sessionId, sessionTitle),
                    archive: ({ sessionId, title: sessionTitle }) =>
                      setArchivingSession({ sessionId, title: sessionTitle }),
                  });
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
                      <ContextMenu
                        open={
                          openActionMenu?.sessionId === s.session_id &&
                          openActionMenu.surface === "context"
                        }
                        onOpenChange={(open) =>
                          handleSessionActionMenuOpenChange(s.session_id, "context", open)
                        }
                      >
                        <ContextMenuTrigger asChild>
                          <SidebarMenuButton
                            isActive={s.session_id === currentSessionId}
                            className="px-2 text-sm font-normal data-[active=true]:bg-bg transition-none"
                            onContextMenu={(event) => {
                              pointerContextMenuSessionRef.current =
                                event.button === 2 ? s.session_id : null;
                            }}
                            onClick={(event) => {
                              if (shouldClearSessionActionFocusOnOpen(event.detail)) {
                                setFocusedActionSessionId((current) =>
                                  current === s.session_id ? null : current,
                                );
                              }
                              handleOpenSession(s.session_id);
                            }}
                          >
                            <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                              <span className="flex min-w-0 flex-1 items-center gap-2">
                                <TextEllipsis
                                  className="min-w-0 flex-1"
                                  tooltipContentClassName="translate-x-2"
                                  tooltipDelayMs={500}
                                  tooltipSide="right"
                                >
                                  {title}
                                </TextEllipsis>
                                {boundIMTypes.map((imType) => (
                                  <Badge
                                    key={imType}
                                    variant="secondary"
                                    className="px-1.5 py-0 font-normal text-muted"
                                  >
                                    IM · {labelOfIMType(imType)}
                                  </Badge>
                                ))}
                              </span>
                              {!actionVisible && runIndicator === "running" ? (
                                <span title="正在处理" aria-label="正在处理">
                                  <Spinner className="size-3 text-accent" />
                                </span>
                              ) : null}
                              {!actionVisible && runIndicator === "queued" ? (
                                <Clock3
                                  className="size-3 shrink-0 text-muted"
                                  aria-label="等待处理"
                                />
                              ) : null}
                              {!actionVisible && runIndicator === "failed" ? (
                                <CircleAlert
                                  className="size-3 shrink-0 text-danger"
                                  aria-label="处理失败"
                                />
                              ) : null}
                              {!actionVisible && runIndicator === "completed" ? (
                                <Circle
                                  className="size-2 shrink-0 fill-accent text-accent"
                                  aria-label="有新回复"
                                />
                              ) : null}
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
                        </ContextMenuTrigger>
                        <ContextMenuContent
                          onCloseAutoFocus={(event) => {
                            if (pointerContextMenuSessionRef.current !== s.session_id) return;
                            pointerContextMenuSessionRef.current = null;
                            event.preventDefault();
                            setFocusedActionSessionId((current) =>
                              current === s.session_id ? null : current,
                            );
                          }}
                          onClick={(event) => event.stopPropagation()}
                        >
                          <SessionContextMenuItems actions={sessionActions} />
                        </ContextMenuContent>
                      </ContextMenu>
                      {actionVisible ? (
                        <DropdownMenu
                          open={
                            openActionMenu?.sessionId === s.session_id &&
                            openActionMenu.surface === "dropdown"
                          }
                          onOpenChange={(open) => {
                            handleSessionActionMenuOpenChange(
                              s.session_id,
                              "dropdown",
                              open,
                            );
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
                            <SessionDropdownMenuItems actions={sessionActions} />
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

function labelOfIMType(imType: IMType): string {
  return { qq: "QQ Bot" }[imType] ?? imType;
}
