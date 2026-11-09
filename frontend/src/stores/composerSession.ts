import { create } from "zustand";
import { sessionsApi, type SessionComposerState } from "@/services/api/sessions";
import type { FriendlyError } from "@/services/http";

export interface ComposerSessionState {
  sessionId: string | null;
  phase: "idle" | "loading" | "preparing" | "ready" | "failed";
  configuration: SessionComposerState | null;
  error: string | null;
  showPreparing: boolean;
  canRetry: boolean;
}

export const useComposerSessionStore = create<ComposerSessionState>(() => ({
  sessionId: null, phase: "idle", configuration: null, error: null, showPreparing: false, canRetry: false,
}));

let controller: AbortController | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
let noticeTimer: ReturnType<typeof setTimeout> | undefined;
let retry: (() => void) | null = null;
const RETRY_DELAYS_MS = [300, 1200, 4000];

export function resetComposerSession(): Promise<void> {
  const previous = useComposerSessionStore.getState().sessionId;
  controller?.abort(); controller = null;
  clearTimeout(timer); timer = undefined;
  clearTimeout(noticeTimer); noticeTimer = undefined;
  retry = null;
  useComposerSessionStore.setState(useComposerSessionStore.getInitialState());
  if (previous && typeof sessionsApi.cancelPreparation === "function") {
    return sessionsApi.cancelPreparation(previous).then(() => undefined, () => undefined);
  }
  return Promise.resolve();
}

export function retryComposerPreparation(): void { retry?.(); }

/** Metadata and preparation never wait for a history response. */
export function openComposerSession(
  sessionId: string, apply: (config: SessionComposerState) => void,
): void {
  const previousCancelled = resetComposerSession();
  const current = new AbortController();
  controller = current;
  const live = () => controller === current && !current.signal.aborted;
  let attempts = 0;
  useComposerSessionStore.setState({ sessionId, phase: "loading" });
  const showNoticeIfSlow = () => {
    if (noticeTimer !== undefined || useComposerSessionStore.getState().showPreparing) return;
    noticeTimer = setTimeout(() => {
      noticeTimer = undefined;
      if (live()) useComposerSessionStore.setState({ showPreparing: true });
    }, 1200);
  };
  const settle = (phase: "ready" | "failed", error: string | null = null, canRetry = false) => {
    clearTimeout(noticeTimer); noticeTimer = undefined;
    useComposerSessionStore.setState({ phase, error, showPreparing: false, canRetry });
  };
  type Step = "configure" | "prepare" | "poll";
  const fail = (error: unknown, step: Step) => {
    if (!live()) return;
    const { code, status, friendly } = (error ?? {}) as Partial<FriendlyError>;
    if (status === 404 || status === 403) {
      settle("failed", status === 404 ? "会话已不存在" : "当前会话不允许发送消息");
      return;
    }
    const transient = error === null || code === "history_changed" || code === "network"
      || status === 408 || status === 429 || (status !== undefined && status >= 500);
    if (transient && attempts < RETRY_DELAYS_MS.length) {
      useComposerSessionStore.setState({ phase: step === "configure" ? "loading" : "preparing" });
      showNoticeIfSlow();
      timer = setTimeout(() => void run(step), RETRY_DELAYS_MS[attempts++]);
      return;
    }
    settle("failed", code === "history_changed" ? "会话仍在更新，暂时无法发送" : friendly || "会话准备失败", true);
    retry = () => {
      retry = null;
      attempts = 0;
      useComposerSessionStore.setState({ phase: step === "configure" ? "loading" : "preparing", error: null, canRetry: false });
      showNoticeIfSlow();
      void run(step);
    };
  };
  const run = async (step: Step) => {
    if (!live()) return;
    try {
      if (step === "configure") {
        const config = await sessionsApi.composer(sessionId, current.signal);
        if (!live()) return;
        if (config.session_id !== sessionId) throw new Error("Session composer identity mismatch");
        useComposerSessionStore.setState({ configuration: config });
        apply(config);
        if (config.read_only) { settle("ready"); return; }
        await previousCancelled;
        if (live()) await run("prepare");
        return;
      }
      if (step === "prepare") {
        useComposerSessionStore.setState({ phase: "preparing" });
        showNoticeIfSlow();
      }
      const result = step === "prepare"
        ? await sessionsApi.prepare(sessionId, current.signal)
        : await sessionsApi.preparation(sessionId, current.signal);
      if (!live()) return;
      if (result.session_id !== sessionId) throw new Error("Session preparation identity mismatch");
      if (result.state === "failed") { fail(null, "prepare"); return; }
      if (result.state === "idle" && step === "poll") { await run("prepare"); return; }
      if (result.state === "ready") {
        attempts = 0;
        settle("ready");
      } else {
        useComposerSessionStore.setState({ phase: "preparing" });
        showNoticeIfSlow();
      }
      timer = setTimeout(() => void run("poll"), result.state === "ready" ? 10000 : 200);
    } catch (error) { fail(error, step); }
  };
  showNoticeIfSlow();
  void run("configure");
}
