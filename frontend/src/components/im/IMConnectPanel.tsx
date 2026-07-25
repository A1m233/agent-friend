/**
 * IM 接入面板(022 design §3.9)。
 *
 * 面板只承载 IM 状态与交互，不绑定 dialog/window 形态；桌宠 ActionBar 通过
 * `open_im_connect` 打开独立 Tauri 窗口，由窗口负责拖动、关闭即隐藏。
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Check, MessageSquarePlus, Plug, RefreshCcw, Trash2 } from "lucide-react";
import QRCode from "qrcode";

import {
  Badge,
  Button,
  Spinner,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  TextEllipsis,
} from "@/components/ui";
import {
  imApi,
  sessionsApi,
  type IMType,
  type OnboardTaskState,
  type ProviderInfo,
} from "@/services";
import type { SessionSummary } from "@/types/meta";
import { subscribeActiveChatSession } from "@/lib/active-chat-session";
import { emitSessionsChanged } from "@/stores/sessions";
import { cn } from "@/utils/cn";

interface Props {
  active?: boolean;
}

const POLL_INTERVAL_PENDING_MS = 250;
const POLL_INTERVAL_QR_READY_MS = 1000;

export function IMConnectPanel({ active = true }: Props) {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(false);
  const [unbindingKey, setUnbindingKey] = useState<string | null>(null);
  const [startingOnboard, setStartingOnboard] = useState(false);
  const [onboardTask, setOnboardTask] = useState<OnboardTaskState | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [activeChatSessionId, setActiveChatSessionId] = useState<string | null>(null);
  const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
  const [bindingTarget, setBindingTarget] = useState<{
    imType: IMType;
    bindId: string;
  } | null>(null);
  const [bindingMode, setBindingMode] = useState<"new" | "recent">("recent");
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [bindingSession, setBindingSession] = useState(false);
  const mainRef = useRef<HTMLElement | null>(null);
  const onboardProgressRef = useRef<HTMLDivElement | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const qrScrolledTaskRef = useRef<string | null>(null);

  const clearPollTimer = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const resetTransientState = useCallback(() => {
    clearPollTimer();
    qrScrolledTaskRef.current = null;
    setStartingOnboard(false);
    setOnboardTask(null);
    setQrDataUrl(null);
    setErrorMsg(null);
  }, [clearPollTimer]);

  const refreshProviders = useCallback(async () => {
    if (!active) return;
    setLoadingProviders(true);
    try {
      const list = await imApi.listProviders();
      setProviders(list);
    } catch {
      setErrorMsg("拉取已绑定列表失败，稍后重试。");
    } finally {
      setLoadingProviders(false);
    }
  }, [active]);

  useEffect(() => {
    if (active) {
      setErrorMsg(null);
      void refreshProviders();
    } else {
      resetTransientState();
    }
    return clearPollTimer;
  }, [active, clearPollTimer, refreshProviders, resetTransientState]);

  useEffect(() => subscribeActiveChatSession(setActiveChatSessionId), []);

  useEffect(() => {
    const hasSessionBinding = providers.some((provider) => Boolean(provider.session_id));
    if (!active || (!bindingTarget && !hasSessionBinding)) return;
    let cancelled = false;
    void sessionsApi
      .list()
      .then((sessions) => {
        if (cancelled) return;
        setRecentSessions(sessions);
        if (!bindingTarget) return;
        setSelectedSessionId((current) => {
          if (current && sessions.some((session) => session.session_id === current)) {
            return current;
          }
          if (
            activeChatSessionId &&
            sessions.some((session) => session.session_id === activeChatSessionId)
          ) {
            return activeChatSessionId;
          }
          return sessions[0]?.session_id ?? null;
        });
      })
      .catch(() => setErrorMsg("拉取最近会话失败，稍后重试。"));
    return () => {
      cancelled = true;
    };
  }, [active, activeChatSessionId, bindingTarget, providers]);

  useEffect(() => {
    if (onboardTask?.qr_url) {
      QRCode.toDataURL(onboardTask.qr_url, { margin: 1, width: 240 })
        .then(setQrDataUrl)
        .catch(() => setErrorMsg("二维码渲染失败，请重试。"));
    } else {
      setQrDataUrl(null);
    }
  }, [onboardTask?.qr_url]);

  useEffect(() => {
    if (!active || onboardTask?.status !== "qr_ready" || !qrDataUrl) return;
    if (qrScrolledTaskRef.current === onboardTask.task_id) return;

    const frame = window.requestAnimationFrame(() => {
      const scrollContainer = mainRef.current;
      const target = onboardProgressRef.current;
      if (!scrollContainer || !target) return;

      const containerRect = scrollContainer.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const fullyVisible =
        targetRect.top >= containerRect.top && targetRect.bottom <= containerRect.bottom;
      if (!fullyVisible) {
        const nextScrollTop =
          targetRect.bottom > containerRect.bottom
            ? scrollContainer.scrollTop + targetRect.bottom - containerRect.bottom
            : scrollContainer.scrollTop + targetRect.top - containerRect.top;
        scrollContainer.scrollTo({ top: nextScrollTop, behavior: "smooth" });
      }
      qrScrolledTaskRef.current = onboardTask.task_id;
    });

    return () => window.cancelAnimationFrame(frame);
  }, [active, onboardTask?.status, onboardTask?.task_id, qrDataUrl]);

  useEffect(() => {
    if (!active || !onboardTask) return;
    if (onboardTask.status === "success" || onboardTask.status === "failed") {
      clearPollTimer();
      if (onboardTask.status === "success") {
        void refreshProviders();
        if (onboardTask.bind_id) {
          setBindingMode("recent");
          setSelectedSessionId(null);
          setRecentSessions([]);
          setBindingTarget({
            imType: onboardTask.im_type,
            bindId: onboardTask.bind_id,
          });
          setOnboardTask(null);
        }
      }
      return;
    }
    const interval =
      onboardTask.status === "qr_ready"
        ? POLL_INTERVAL_QR_READY_MS
        : POLL_INTERVAL_PENDING_MS;
    pollTimerRef.current = setTimeout(async () => {
      try {
        const next = await imApi.getOnboardState(onboardTask.task_id);
        setOnboardTask(next);
      } catch {
        setErrorMsg("拉取扫码状态失败，稍后重试。");
        clearPollTimer();
      }
    }, interval);
    return clearPollTimer;
  }, [active, onboardTask, clearPollTimer, refreshProviders]);

  const startQQOnboard = useCallback(async () => {
    if (!active || startingOnboard) return;
    setErrorMsg(null);
    setStartingOnboard(true);
    try {
      const { task_id } = await imApi.startOnboard("qq");
      setOnboardTask({
        task_id,
        im_type: "qq",
        status: "pending",
        qr_url: null,
        bind_id: null,
        bind_id_masked: null,
        error: null,
      });
    } catch {
      setErrorMsg("启动扫码失败，稍后重试。");
    } finally {
      setStartingOnboard(false);
    }
  }, [active, startingOnboard]);

  const unbind = useCallback(
    async (imType: IMType, bindId: string) => {
      if (!active || unbindingKey) return;
      const key = `${imType}:${bindId}`;
      setUnbindingKey(key);
      try {
        await imApi.unbindProvider(imType, bindId);
        await emitSessionsChanged({ action: "im-unbind" });
        await refreshProviders();
      } catch {
        setErrorMsg("解绑失败，稍后重试。");
      } finally {
        setUnbindingKey(null);
      }
    },
    [active, refreshProviders, unbindingKey],
  );

  const confirmBinding = useCallback(async () => {
    if (!bindingTarget || bindingSession) return;
    setBindingSession(true);
    setErrorMsg(null);
    try {
      const sessionId =
        bindingMode === "new"
          ? (await sessionsApi.create()).session_id
          : selectedSessionId;
      if (!sessionId) {
        setErrorMsg("还没有可选择的最近会话，请新建一个独立会话。 ");
        return;
      }
      await imApi.bindSession(bindingTarget.imType, bindingTarget.bindId, sessionId);
      await emitSessionsChanged({ action: "im-bind", sessionId });
      setBindingTarget(null);
      setOnboardTask(null);
      setRecentSessions([]);
      setSelectedSessionId(null);
      await refreshProviders();
    } catch {
      setErrorMsg("会话绑定失败，稍后重试。 ");
    } finally {
      setBindingSession(false);
    }
  }, [bindingMode, bindingSession, bindingTarget, refreshProviders, selectedSessionId]);

  return (
    <div className="flex h-full flex-col bg-bg text-fg">
      <header className="border-b border-border px-4 py-3">
        <h1 className="text-lg font-semibold text-fg">接入 IM</h1>
        <p className="mt-1 text-sm text-fg/70">
          在 IM 里也能跟我聊。同人格，共享记忆。
        </p>
      </header>

      <main ref={mainRef} className="flex min-h-0 flex-1 flex-col gap-6 overflow-auto p-4">
        <section>
          <header className="mb-2 flex items-center justify-between">
            <span className="text-xs uppercase tracking-wide text-fg/60">
              已绑定
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => void refreshProviders()}
              loading={loadingProviders}
              disabled={loadingProviders}
              aria-label="刷新"
            >
              {!loadingProviders && <RefreshCcw className="size-4" />}
            </Button>
          </header>
          {providers.length === 0 ? (
            <p className="text-sm text-fg/50">
              {loadingProviders ? "加载中..." : "暂无已绑定的 IM。"}
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {providers.map((p) => (
                <li
                  key={`${p.im_type}-${p.bind_id}`}
                  className="overflow-hidden rounded-lg border border-border bg-surface"
                >
                  <div className="flex items-center justify-between gap-3 px-3 py-2">
                    <div className="min-w-0 text-sm text-fg">
                      <div>
                        {labelOfIMType(p.im_type)} · {p.bind_id_masked}
                        <span
                          className={cn(
                            "ml-2 text-xs",
                            p.status === "active" && "text-success",
                            p.status === "degraded" && "text-warning",
                            p.status === "error" && "text-danger",
                            p.status === "stopped" && "text-fg/50",
                          )}
                        >
                          {labelOfStatus(p.status)}
                        </span>
                      </div>
                      <TextEllipsis className="mt-0.5 text-xs text-fg/60">
                        {`会话：${bindingLabel(p, recentSessions)}`}
                      </TextEllipsis>
                      {p.controls_capability === "unsupported" && (
                        <p className="mt-1 text-xs text-warning">
                          QQ 仍可正常聊天；会话和模型请在桌面端管理。
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="hover:bg-bg"
                        aria-expanded={isBindingTarget(bindingTarget, p)}
                        onClick={() => {
                          if (isBindingTarget(bindingTarget, p)) {
                            setBindingTarget(null);
                            return;
                          }
                          setBindingMode("recent");
                          setSelectedSessionId(p.session_id ?? null);
                          setRecentSessions([]);
                          setBindingTarget({
                            imType: p.im_type,
                            bindId: p.bind_id,
                          });
                        }}
                      >
                        {isBindingTarget(bindingTarget, p)
                          ? "收起"
                          : p.binding_status === "bound"
                            ? "更换会话"
                            : "选择会话"}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="text-muted hover:bg-danger/10 hover:text-danger"
                        onClick={() => void unbind(p.im_type, p.bind_id)}
                        loading={unbindingKey === `${p.im_type}:${p.bind_id}`}
                        disabled={Boolean(unbindingKey)}
                        aria-label="解绑"
                      >
                        {unbindingKey !== `${p.im_type}:${p.bind_id}` && (
                          <Trash2 className="size-4" />
                        )}
                      </Button>
                    </div>
                  </div>
                  {isBindingTarget(bindingTarget, p) && (
                    <SessionBindingStep
                      imLabel={labelOfIMType(p.im_type)}
                      mode={bindingMode}
                      onModeChange={setBindingMode}
                      sessions={recentSessions}
                      activeChatSessionId={activeChatSessionId}
                      selectedSessionId={selectedSessionId}
                      onSelectSession={setSelectedSessionId}
                      onConfirm={() => void confirmBinding()}
                      onCancel={() => setBindingTarget(null)}
                      loading={bindingSession}
                    />
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section>
          <header className="mb-2 text-xs uppercase tracking-wide text-fg/60">
            接入新 IM
          </header>
          {onboardTask ? (
            <div ref={onboardProgressRef}>
              <OnboardProgress
                task={onboardTask}
                qrDataUrl={qrDataUrl}
                onCancel={() => setOnboardTask(null)}
              />
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                loading={startingOnboard}
                onClick={() => void startQQOnboard()}
              >
                {!startingOnboard && <Plug className="size-4" />}
                QQ
              </Button>
              {/* <Button size="sm" variant="ghost" disabled>
                飞书 (敬请期待)
              </Button>
              <Button size="sm" variant="ghost" disabled>
                Telegram (敬请期待)
              </Button> */}
            </div>
          )}
        </section>

        {errorMsg && (
          <p className="text-sm text-danger" role="alert">
            {errorMsg}
          </p>
        )}
      </main>
    </div>
  );
}

function SessionBindingStep({
  imLabel,
  mode,
  onModeChange,
  sessions,
  activeChatSessionId,
  selectedSessionId,
  onSelectSession,
  onConfirm,
  onCancel,
  loading,
}: {
  imLabel: string;
  mode: "new" | "recent";
  onModeChange: (mode: "new" | "recent") => void;
  sessions: SessionSummary[];
  activeChatSessionId: string | null;
  selectedSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
  loading: boolean;
}) {
  const sessionListRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    if (mode !== "recent" || !selectedSessionId) return;
    const container = sessionListRef.current;
    const selected = container?.querySelector<HTMLElement>("[data-selected='true']");
    if (!container || !selected) return;

    const containerRect = container.getBoundingClientRect();
    const selectedRect = selected.getBoundingClientRect();
    if (selectedRect.top < containerRect.top) {
      container.scrollTop += selectedRect.top - containerRect.top;
    } else if (selectedRect.bottom > containerRect.bottom) {
      container.scrollTop += selectedRect.bottom - containerRect.bottom;
    }
  }, [mode, selectedSessionId, sessions]);

  return (
    <div className="border-t border-border bg-bg p-3">
      <h2 className="text-sm font-medium text-fg">选择 {imLabel} 要使用的会话</h2>
      <Tabs
        value={mode}
        onValueChange={(value) => onModeChange(value as "new" | "recent")}
        className="mt-3 gap-0 overflow-hidden rounded-lg border border-border bg-bg"
      >
        <TabsList className="grid h-auto w-full grid-cols-2 rounded-none border-b border-border bg-surface p-1">
          <TabsTrigger value="recent">选择最近会话</TabsTrigger>
          <TabsTrigger value="new">新建独立会话</TabsTrigger>
        </TabsList>
        <TabsContent value="recent" className="m-0 p-2">
          {sessions.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-sm text-fg/60">
              暂无最近会话
            </p>
          ) : (
            <div
              ref={sessionListRef}
              className="flex max-h-56 flex-col gap-1 overflow-y-auto"
            >
              {sessions.map((session) => {
                const selected = session.session_id === selectedSessionId;
                const current = session.session_id === activeChatSessionId;
                return (
                  <Button
                    key={session.session_id}
                    type="button"
                    variant={selected ? "secondary" : "ghost"}
                    data-selected={selected || undefined}
                    className="h-auto w-full justify-start px-3 py-2 text-left"
                    onClick={() => onSelectSession(session.session_id)}
                  >
                    <span className="flex min-w-0 flex-1 items-center gap-2">
                      <span className="min-w-0 flex-1 truncate">{session.title || "未命名会话"}</span>
                      {current && <Badge variant="secondary">上次活跃会话</Badge>}
                      {selected && <Check className="size-4 shrink-0 text-accent" />}
                    </span>
                  </Button>
                );
              })}
            </div>
          )}
        </TabsContent>
        <TabsContent value="new" className="m-0 p-2">
          <div className="rounded-md bg-surface px-3 py-3">
            <p className="flex items-center gap-2 text-sm font-medium text-fg">
              <MessageSquarePlus className="size-4" />
              为 QQ 新建独立会话
            </p>
            <p className="mt-1 text-xs leading-5 text-fg/60">
              QQ 之后的消息会进入这段新会话，不影响你当前正在聊的内容。
            </p>
          </div>
        </TabsContent>
      </Tabs>
      <div className="mt-3 flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={loading}>
          取消
        </Button>
        <Button
          size="sm"
          onClick={onConfirm}
          loading={loading}
          disabled={mode === "recent" && !selectedSessionId}
        >
          {mode === "new" ? "新建并绑定" : "绑定此会话"}
        </Button>
      </div>
    </div>
  );
}

function OnboardProgress({
  task,
  qrDataUrl,
  onCancel,
}: {
  task: OnboardTaskState;
  qrDataUrl: string | null;
  onCancel: () => void;
}) {
  if (task.status === "pending") {
    return (
      <div className="flex flex-col items-center gap-2 py-4">
        <p className="flex items-center gap-2 text-sm text-fg/70">
          <Spinner data-icon="inline-start" />
          正在向腾讯请求扫码链接...
        </p>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
      </div>
    );
  }

  if (task.status === "qr_ready") {
    return (
      <div className="flex flex-col items-center gap-3 py-2">
        {qrDataUrl ? (
          <img
            src={qrDataUrl}
            alt="QQ Bot 绑定二维码"
            className="size-60 rounded-md border border-border bg-bg p-2"
          />
        ) : (
          <div className="size-60 rounded-md border border-border bg-surface" />
        )}
        <p className="text-sm text-fg/70">
          用手机 QQ 扫码，完成创建者专属 Bot 绑定。
        </p>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
      </div>
    );
  }

  if (task.status === "success") {
    return (
      <div className="flex flex-col items-center gap-2 py-3">
        <p className="text-sm text-success">绑定成功 · {task.bind_id_masked}</p>
        <Button size="sm" onClick={onCancel}>
          完成
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-2 py-3">
      <p className="text-sm text-danger">绑定失败 · {task.error ?? "未知错误"}</p>
      <Button size="sm" variant="ghost" onClick={onCancel}>
        重试
      </Button>
    </div>
  );
}

function labelOfIMType(t: IMType): string {
  return { qq: "QQ Bot" }[t] ?? t;
}

function labelOfStatus(s: ProviderInfo["status"]): string {
  return (
    {
      active: "在线",
      degraded: "短暂掉线",
      error: "异常",
      stopped: "已停止",
    } as const
  )[s];
}

function bindingLabel(provider: ProviderInfo, sessions: SessionSummary[]): string {
  if (provider.binding_status === "unbound") return "尚未选择会话";
  if (provider.binding_status === "invalid") return "原会话已不存在，请重新选择";
  return (
    sessions.find((session) => session.session_id === provider.session_id)?.title ??
    "已选择会话"
  );
}

function isBindingTarget(
  target: { imType: IMType; bindId: string } | null,
  provider: ProviderInfo,
): boolean {
  return target?.imType === provider.im_type && target.bindId === provider.bind_id;
}
