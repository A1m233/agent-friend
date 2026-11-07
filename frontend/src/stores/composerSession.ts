import { create } from "zustand";
import { sessionsApi, type SessionComposerState } from "@/services/api/sessions";

export interface ComposerSessionState {
  sessionId: string | null;
  phase: "idle" | "loading" | "preparing" | "ready" | "failed";
  configuration: SessionComposerState | null;
  error: string | null;
}

export const useComposerSessionStore = create<ComposerSessionState>(() => ({
  sessionId: null, phase: "idle", configuration: null, error: null,
}));

let controller: AbortController | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
let retry: (() => void) | null = null;

export function resetComposerSession(): Promise<void> {
  const previous = useComposerSessionStore.getState().sessionId;
  controller?.abort(); controller = null;
  clearTimeout(timer); timer = undefined;
  retry = null;
  useComposerSessionStore.setState({ sessionId: null, phase: "idle", configuration: null, error: null });
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
  useComposerSessionStore.setState({ sessionId, phase: "loading" });
  const fail = (error: unknown) => {
    if (!live()) return;
    const friendly = error && typeof error === "object" && "friendly" in error
      ? String(error.friendly) : "会话准备失败，请重试";
    useComposerSessionStore.setState({ phase: "failed", error: friendly });
  };
  const poll = async () => {
    try {
      const result = await sessionsApi.preparation(sessionId, current.signal);
      if (!live()) return;
      if (result.session_id !== sessionId) throw new Error("Session preparation identity mismatch");
      if (result.state === "idle") { await prepare(); return; }
      if (result.state === "failed") { fail(null); return; }
      useComposerSessionStore.setState({ phase: result.state });
      timer = setTimeout(() => void poll(), result.state === "ready" ? 10000 : 200);
    } catch (error) { fail(error); }
  };
  const prepare = async () => {
    if (!live()) return;
    useComposerSessionStore.setState({ phase: "preparing", error: null });
    try {
      const result = await sessionsApi.prepare(sessionId, current.signal);
      if (!live()) return;
      if (result.session_id !== sessionId) throw new Error("Session preparation identity mismatch");
      if (result.state === "failed") { fail(null); return; }
      useComposerSessionStore.setState({ phase: result.state === "ready" ? "ready" : "preparing" });
      timer = setTimeout(() => void poll(), result.state === "ready" ? 10000 : 200);
    } catch (error) { fail(error); }
  };
  retry = () => openComposerSession(sessionId, apply);
  void (async () => {
    try {
      const config = await sessionsApi.composer(sessionId, current.signal);
      if (!live()) return;
      if (config.session_id !== sessionId) throw new Error("Session composer identity mismatch");
      useComposerSessionStore.setState({ configuration: config });
      apply(config);
      if (config.read_only) {
        useComposerSessionStore.setState({ phase: "ready" });
        return;
      }
      await previousCancelled;
      if (live()) await prepare();
    } catch (error) { fail(error); }
  })();
}
