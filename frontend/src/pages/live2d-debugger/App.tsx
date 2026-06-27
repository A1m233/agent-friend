import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { emitTo, listen } from "@tauri-apps/api/event";
import {
  CircleAlert,
  CircleCheck,
  Play,
  RefreshCw,
  RotateCcw,
  Shuffle,
  SlidersHorizontal,
  Sparkles,
  Trash2,
} from "lucide-react";

import {
  Badge,
  Button,
  ScrollArea,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Separator,
} from "@/components/ui";
import { cn } from "@/utils/cn";
import { isTauri } from "@/utils/tauri";
import {
  LIVE2D_DEBUGGER_COMMAND_EVENT,
  LIVE2D_DEBUGGER_RESPONSE_EVENT,
  PET_WINDOW_LABEL,
  type Live2DDebugCommand,
  type Live2DDebugPriority,
  type Live2DDebugResponse,
  type Live2DMotionCatalog,
} from "@/pet/live2dDebugger/protocol";

import { debugLogReducer, describeCommand } from "./debugLog";

const WINDOW_SHOWN_EVENT = "window://shown";

function safeUnlisten(fn: (() => void) | null | undefined): void {
  if (!fn) return;
  try {
    fn();
  } catch {
    /* stale-cleanup race ignored */
  }
}

function makeRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function Live2DDebuggerApp() {
  const [catalog, setCatalog] = useState<Live2DMotionCatalog | null>(null);
  const [selectedGroup, setSelectedGroup] = useState<string>("");
  const [selectedIndex, setSelectedIndex] = useState<string>("0");
  const [priority, setPriority] = useState<Live2DDebugPriority>("force");
  const [logs, dispatchLog] = useReducer(debugLogReducer, []);

  const selectedMotions = useMemo(() => {
    return catalog?.groups.find((group) => group.name === selectedGroup)?.motions ?? [];
  }, [catalog, selectedGroup]);

  const modelId = catalog?.model.modelId ?? "";

  const applyCatalog = (nextCatalog: Live2DMotionCatalog) => {
    setCatalog(nextCatalog);
    const preferredGroup =
      nextCatalog.defaults.tapGroup ??
      nextCatalog.defaults.idleGroup ??
      nextCatalog.groups[0]?.name ??
      "";
    const preferredMotion =
      nextCatalog.groups
        .find((group) => group.name === preferredGroup)
        ?.motions.find((motion) => motion.index === nextCatalog.defaults.tapIndex) ??
      nextCatalog.groups.find((group) => group.name === preferredGroup)?.motions[0] ??
      nextCatalog.groups[0]?.motions[0] ??
      null;
    setSelectedGroup(preferredMotion?.group ?? preferredGroup);
    setSelectedIndex(String(preferredMotion?.index ?? 0));
  };

  const sendCommand = useCallback(async (command: Live2DDebugCommand) => {
    const { title, detail } = describeCommand(command);
    dispatchLog({
      type: "append",
      now: Date.now(),
      entry: { requestId: command.requestId, title, detail },
    });

    if (!isTauri()) {
      dispatchLog({
        type: "local-error",
        requestId: command.requestId,
        message: "当前不是 Tauri 窗口，无法发送命令",
      });
      return;
    }

    try {
      await emitTo(PET_WINDOW_LABEL, LIVE2D_DEBUGGER_COMMAND_EVENT, command);
    } catch (error) {
      console.warn("[live2d-debugger] emit command failed:", error);
      dispatchLog({
        type: "local-error",
        requestId: command.requestId,
        message: formatError(error),
      });
    }
  }, []);

  const queryCatalog = useCallback(() => {
    void sendCommand({ kind: "queryCatalog", requestId: makeRequestId() });
  }, [sendCommand]);

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | null = null;

    if (!isTauri()) {
      queryCatalog();
      return;
    }

    void listen<Live2DDebugResponse>(LIVE2D_DEBUGGER_RESPONSE_EVENT, (event) => {
      const response = event.payload;
      if (response.ok && response.catalog) {
        applyCatalog(response.catalog);
      }
      dispatchLog({ type: "resolve", response });
    }).then((u) => {
      if (cancelled) safeUnlisten(u);
      else {
        unlisten = u;
        queryCatalog();
      }
    }).catch((error: unknown) => {
      if (cancelled) return;
      const requestId = makeRequestId();
      dispatchLog({
        type: "append",
        now: Date.now(),
        entry: {
          requestId,
          title: "连接 pet 窗",
          detail: "注册调试器响应监听",
        },
      });
      dispatchLog({
        type: "local-error",
        requestId,
        message: formatError(error),
      });
      console.warn("[live2d-debugger] listen response failed:", error);
    });

    return () => {
      cancelled = true;
      safeUnlisten(unlisten);
    };
  }, [queryCatalog]);

  useEffect(() => {
    if (!isTauri()) return;

    let cancelled = false;
    let unlisten: (() => void) | null = null;

    void listen(WINDOW_SHOWN_EVENT, queryCatalog)
      .then((u) => {
        if (cancelled) safeUnlisten(u);
        else unlisten = u;
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          console.warn("[live2d-debugger] listen shown failed:", error);
        }
      });

    return () => {
      cancelled = true;
      safeUnlisten(unlisten);
    };
  }, [queryCatalog]);

  const playSelectedMotion = () => {
    if (!catalog) return;
    void sendCommand({
      kind: "playMotion",
      requestId: makeRequestId(),
      modelId: catalog.model.modelId,
      group: selectedGroup,
      index: Number(selectedIndex),
      priority,
    });
  };

  const sendModelCommand = (kind: Exclude<Live2DDebugCommand["kind"], "queryCatalog" | "playMotion">) => {
    if (!catalog) return;
    void sendCommand({ kind, requestId: makeRequestId(), modelId: catalog.model.modelId });
  };

  return (
    <main className="min-h-screen bg-bg text-fg">
      <div className="mx-auto flex w-full max-w-screen-sm flex-col gap-4 p-4">
        <header className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold">Live2D 调试器</h1>
            <p className="text-sm text-muted">
              当前模型：{catalog?.model.modelName ?? "等待 pet 窗响应"}
            </p>
          </div>
          <Badge variant={catalog ? "secondary" : "outline"}>
            {catalog ? `${catalog.groups.length} groups` : "pending"}
          </Badge>
        </header>

        <section className="rounded-md border border-border bg-surface p-4">
          <div className="mb-3 flex items-center gap-2">
            <Play className="size-4 text-muted" />
            <h2 className="text-sm font-semibold">Motion</h2>
          </div>
          <div className="grid gap-3">
            <label className="grid gap-2 text-sm">
              <span className="text-muted">Group</span>
              <Select value={selectedGroup} onValueChange={setSelectedGroup} disabled={!catalog}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="选择 group" />
                </SelectTrigger>
                <SelectContent>
                  {catalog?.groups.map((group) => (
                    <SelectItem key={group.name} value={group.name}>
                      {group.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>

            <label className="grid gap-2 text-sm">
              <span className="text-muted">Motion</span>
              <Select value={selectedIndex} onValueChange={setSelectedIndex} disabled={!selectedMotions.length}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="选择 motion" />
                </SelectTrigger>
                <SelectContent>
                  {selectedMotions.map((motion) => (
                    <SelectItem key={`${motion.group}-${motion.index}`} value={String(motion.index)}>
                      {motion.index} · {motion.file}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>

            <label className="grid gap-2 text-sm">
              <span className="text-muted">Priority</span>
              <Select value={priority} onValueChange={(value) => setPriority(value as Live2DDebugPriority)}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="force">force</SelectItem>
                  <SelectItem value="normal">normal</SelectItem>
                  <SelectItem value="idle">idle</SelectItem>
                </SelectContent>
              </Select>
            </label>

            <div className="flex gap-2">
              <Button className="flex-1" disabled={!catalog || !selectedGroup} onClick={playSelectedMotion}>
                <Play />
                播放
              </Button>
              <Button variant="outline" onClick={queryCatalog}>
                <RefreshCw />
                刷新
              </Button>
            </div>
          </div>
        </section>

        <section className="rounded-md border border-border bg-surface p-4">
          <div className="mb-3 flex items-center gap-2">
            <SlidersHorizontal className="size-4 text-muted" />
            <h2 className="text-sm font-semibold">快捷</h2>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Button variant="outline" disabled={!modelId} onClick={() => sendModelCommand("triggerTapFeedback")}>
              <Sparkles />
              点击反馈
            </Button>
            <Button variant="outline" disabled={!modelId} onClick={() => sendModelCommand("triggerTapParamsOnly")}>
              <SlidersHorizontal />
              参数 only
            </Button>
            <Button variant="outline" disabled={!modelId} onClick={() => sendModelCommand("playIdle")}>
              <RotateCcw />
              回 idle
            </Button>
            <Button variant="outline" disabled={!modelId} onClick={() => sendModelCommand("playRandomIdle")}>
              <Shuffle />
              随机 idle
            </Button>
          </div>
        </section>

        <section className="rounded-md border border-border bg-surface p-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <CircleCheck className="size-4 text-muted" />
              <h2 className="text-sm font-semibold">日志</h2>
            </div>
            <Button variant="ghost" size="xs" onClick={() => dispatchLog({ type: "clear" })}>
              <Trash2 />
              清空
            </Button>
          </div>
          <Separator className="mb-3" />
          <ScrollArea className="h-64">
            <div className="flex flex-col gap-2 pr-3">
              {logs.length === 0 && (
                <p className="text-sm text-muted">等待命令。</p>
              )}
              {logs.map((entry) => (
                <div
                  key={entry.requestId}
                  className="rounded-md border border-border bg-bg p-3"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{entry.title}</p>
                      <p className="text-xs text-muted">{entry.detail}</p>
                    </div>
                    <Badge
                      variant={
                        entry.status === "ok"
                          ? "default"
                          : entry.status === "error"
                            ? "destructive"
                            : "outline"
                      }
                    >
                      {entry.status}
                    </Badge>
                  </div>
                  <div className="mt-2 flex items-start gap-2 text-xs text-muted">
                    {entry.status === "error" && <CircleAlert className="size-3 text-danger" />}
                    <span>{formatTime(entry.createdAt)}</span>
                    {entry.result && <span className={cn(entry.status === "error" && "text-danger")}>{entry.result}</span>}
                  </div>
                </div>
              ))}
            </div>
          </ScrollArea>
        </section>
      </div>
    </main>
  );
}
