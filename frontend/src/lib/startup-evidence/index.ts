import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";

type Milestone = "module-loaded" | "settings-ready" | "root-committed" |
  "models-ready" | "restore-ready" | "composer-ready" | "frontend-ready";
const observed = new Set<Milestone>();
let composerUsable = false;
const pageId = crypto.randomUUID();
const required: Milestone[] = ["settings-ready", "root-committed", "models-ready", "restore-ready", "composer-ready"];

/** Observational only: never blocks startup, sends text, or changes focus. */
export function recordStartupMilestone(milestone: Milestone): void {
  if (!isTauri()) return;
  if (!observed.has(milestone)) {
    observed.add(milestone);
    void invoke("record_startup_milestone", {
      milestone, pageId, pageElapsedMs: performance.now(),
    }).catch(() => { /* Missing telemetry remains a gap, never a startup failure. */ });
  }
  if (milestone !== "frontend-ready" && composerUsable && required.every((item) => observed.has(item))) {
    recordStartupMilestone("frontend-ready");
  }
}

/** React commit alone does not prove that the custom element's editor is usable. */
export function observeStartupComposer(findEditor: () => HTMLTextAreaElement | null): () => void {
  if (!isTauri() || observed.has("frontend-ready")) return () => {};
  const deadline = performance.now() + 120_000;
  const check = () => {
    const editor = findEditor();
    if (editor?.isConnected && !editor.disabled && !editor.readOnly) {
      composerUsable = true;
      recordStartupMilestone("composer-ready");
      clearInterval(timer);
    } else if (performance.now() >= deadline) {
      clearInterval(timer);
    }
  };
  const timer = setInterval(check, 100);
  check();
  return () => { composerUsable = false; clearInterval(timer); };
}
