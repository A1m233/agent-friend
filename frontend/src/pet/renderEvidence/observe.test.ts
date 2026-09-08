// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { observePetRenderer } from "./observe";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@/utils/tauri", () => ({ isTauri: () => true }));
function fixture() {
  const callbacks = new Set<() => void>();
  const pre = new Set<{ prerender(): void }>();
  const post = new Set<{ postrender(): void }>();
  const renderer = { renderingToScreen: true, runners: {
    prerender: { add: (v: { prerender(): void }) => pre.add(v), remove: (v: { prerender(): void }) => pre.delete(v) },
    postrender: { add: (v: { postrender(): void }) => post.add(v), remove: (v: { postrender(): void }) => post.delete(v) },
  } };
  const ticker = { started: true, add: (v: () => void) => callbacks.add(v), remove: (v: () => void) => callbacks.delete(v) };
  return { renderer, ticker, callbacks, pre, post, canvas: document.createElement("canvas") };
}
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });
describe("renderer observer", () => {
  it("preserves context transitions while IPC is in flight and detaches on cleanup", async () => {
    vi.useFakeTimers();
    let accept!: (value: boolean) => void;
    vi.mocked(invoke).mockImplementationOnce(() => new Promise((resolve) => { accept = resolve; })).mockResolvedValue(true);
    const f = fixture(); const stop = observePetRenderer(f.renderer, f.ticker, f.canvas, "pixi7");
    f.canvas.dispatchEvent(new Event("webglcontextlost"));
    f.canvas.dispatchEvent(new Event("webglcontextrestored"));
    expect(invoke).toHaveBeenCalledTimes(1);
    accept(true); await vi.advanceTimersByTimeAsync(1);
    const samples = vi.mocked(invoke).mock.calls.map((c) => (c[1] as { sample: { kind: string; contextLost: boolean } }).sample);
    expect(samples.map((s) => s.kind)).toEqual(["started", "context-lost", "context-restored"]);
    expect(samples[1].contextLost).toBe(true);
    stop(); await vi.advanceTimersByTimeAsync(1);
    expect(f.callbacks.size + f.pre.size + f.post.size).toBe(0);
    const n = vi.mocked(invoke).mock.calls.length;
    await vi.advanceTimersByTimeAsync(20_000);
    expect(invoke).toHaveBeenCalledTimes(n);
  });
  it("reports independent timer, ticker and renderer counts", async () => {
    vi.useFakeTimers(); vi.mocked(invoke).mockResolvedValue(true);
    const f = fixture(); const stop = observePetRenderer(f.renderer, f.ticker, f.canvas, "pixi8");
    for (const tick of f.callbacks) tick();
    await vi.advanceTimersByTimeAsync(5_000);
    const last = vi.mocked(invoke).mock.calls.at(-1)?.[1] as { sample: { ticks: number; renders: number; timerTicks: number } };
    expect(last.sample).toMatchObject({ ticks: 1, renders: 0, timerTicks: 5 }); stop();
  });
});
