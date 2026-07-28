import { invoke } from "@tauri-apps/api/core";

import { http } from "@/services/http";
import { isTauri } from "@/utils/tauri";

interface DesktopPresenceProbe {
  windowVisible: boolean;
  windowFocused: boolean;
  windowMinimized: boolean;
  osIdleMs: number | null;
  sessionState: "active" | "locked" | "sleeping" | "unknown";
  capabilities: string[];
}
const PRESENCE_INTERVAL_MS = 5_000;

export function startDesktopPresenceReporter(currentSessionId: () => string | null): () => void {
  if (!isTauri()) return () => undefined;
  let disposed = false;

  const report = async () => {
    try {
      const probe = await invoke<DesktopPresenceProbe>("desktop_presence_probe");
      if (disposed) return;
      const observedAt = new Date();
      const lastInteraction = probe.osIdleMs === null
        ? null
        : new Date(observedAt.getTime() - probe.osIdleMs).toISOString();
      await http.post("/v1/runtime/presence/desktop", {
        observedAt: observedAt.toISOString(),
        currentSessionId: currentSessionId(),
        windowVisible: probe.windowVisible && document.visibilityState === "visible",
        windowFocused: probe.windowFocused && document.hasFocus(),
        windowMinimized: probe.windowMinimized,
        osIdleMs: probe.osIdleMs,
        sessionState: probe.sessionState,
        lastDesktopInteractionAt: lastInteraction,
        capabilities: probe.capabilities,
      });
    } catch {
      // Missing/failed probe produces no fresh snapshot; Bridge expires the previous one after 15s.
    }
  };

  void report();
  const timer = window.setInterval(() => void report(), PRESENCE_INTERVAL_MS);
  const onVisibility = () => void report();
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("focus", onVisibility);
  window.addEventListener("blur", onVisibility);
  return () => {
    disposed = true;
    window.clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("focus", onVisibility);
    window.removeEventListener("blur", onVisibility);
  };
}
