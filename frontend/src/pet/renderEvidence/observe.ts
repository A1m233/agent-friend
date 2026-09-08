import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";
import { RenderStatistics } from "./statistics";

interface Runner { add(listener: object): unknown; remove(listener: object): unknown }
interface ObservableRenderer {
  runners: { prerender: Runner; postrender: Runner };
  readonly renderingToScreen: boolean;
}
interface ObservableTicker {
  readonly started: boolean;
  add(callback: () => void): unknown;
  remove(callback: () => void): unknown;
}
let pageId: string | undefined;

/** Observes CPU-side render lifecycle only. Never reads pixels or requests a render. */
export function observePetRenderer(
  rendererInput: unknown, tickerInput: unknown, canvas: HTMLCanvasElement,
  backend: "pixi7" | "pixi8",
): () => void {
  if (!isTauri()) return () => {};
  // The repository Pixi7 shim exposes renderer as unknown; failures remain observation-only.
  const renderer = rendererInput as ObservableRenderer;
  const ticker = tickerInput as ObservableTicker;
  pageId ??= crypto.randomUUID();
  const page = pageId;
  const rendererId = crypto.randomUUID();
  const stats = new RenderStatistics();
  const started = performance.now();
  let lastReport = started;
  let sequence = 0;
  let contextLost = false;
  let closed = false;
  let pending = false;
  let dropped = 0;
  const queue: Array<Record<string, unknown>> = [];
  const pump = () => {
    if (pending || queue.length === 0) return;
    const sample = queue.shift();
    pending = true;
    void invoke<boolean>("record_pet_render_sample", { sample })
      .then((accepted) => { if (!accepted) dropped++; })
      .catch(() => { dropped++; })
      .finally(() => { pending = false; pump(); });
  };
  const send = (kind: "started" | "summary" | "context-lost" | "context-restored" | "visibility" | "stopped" | "observer-failed") => {
    if (queue.length >= 8) { dropped++; return; }
    const now = performance.now();
    const sample = { pageId: page, rendererId, backend, kind, sequence: ++sequence,
      elapsedMs: now - started, intervalMs: now - lastReport, ...stats.take(),
      visible: document.visibilityState === "visible", tickerStarted: ticker?.started === true,
      contextLost, width: canvas.width, height: canvas.height, dpr: window.devicePixelRatio || 1, dropped };
    dropped = 0; lastReport = now; queue.push(sample); pump();
  };
  const tick = () => stats.tick(performance.now());
  const listener = {
    prerender: () => stats.begin(performance.now()),
    postrender: () => {
      try { stats.end(performance.now(), renderer.renderingToScreen); }
      catch { dropped++; }
    },
  };
  const lost = () => { contextLost = true; send("context-lost"); };
  const restored = () => { contextLost = false; send("context-restored"); };
  const visible = () => send("visibility");
  const cleanup: Array<() => void> = [];
  try {
    renderer.runners.prerender.add(listener);
    cleanup.push(() => renderer.runners.prerender.remove(listener));
    renderer.runners.postrender.add(listener);
    cleanup.push(() => renderer.runners.postrender.remove(listener));
    ticker.add(tick); cleanup.push(() => ticker.remove(tick));
    canvas.addEventListener("webglcontextlost", lost);
    canvas.addEventListener("webglcontextrestored", restored);
    document.addEventListener("visibilitychange", visible);
    cleanup.push(() => {
      canvas.removeEventListener("webglcontextlost", lost);
      canvas.removeEventListener("webglcontextrestored", restored);
      document.removeEventListener("visibilitychange", visible);
    });
    const timer = window.setInterval(() => {
      stats.timerTicks++;
      if (performance.now() - lastReport >= 5_000) send("summary");
    }, 1_000);
    cleanup.push(() => window.clearInterval(timer));
    send("started");
  } catch { send("observer-failed"); }
  return () => {
    if (closed) return;
    closed = true;
    send("stopped");
    for (const dispose of cleanup.reverse()) { try { dispose(); } catch { /* stale renderer */ } }
  };
}
