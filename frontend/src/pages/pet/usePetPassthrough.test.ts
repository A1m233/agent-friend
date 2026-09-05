// @vitest-environment jsdom
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { InputCursor } from "./inputEvidence/types";
import { usePetPassthrough } from "./usePetPassthrough";

const mocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  listeners: [] as Array<(event: { payload: InputCursor }) => void>,
  unlisten: vi.fn(),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn((_name: string, handler: (event: { payload: InputCursor }) => void) => {
    mocks.listeners.push(handler);
    return Promise.resolve(mocks.unlisten);
  }),
}));
vi.mock("@/utils/tauri", () => ({ isTauri: () => true }));

type Options = Parameters<typeof usePetPassthrough>[0];
let root: Root;
let container: HTMLDivElement;
let active = false;
const cursor = (x: number, y: number): InputCursor => ({
  x, y, diagnostic: { sampleId: 4, dprRevision: 2, scale: 1.25, scaleSource: "webview", physicalX: x * 1.25, physicalY: y * 1.25, originX: 0, originY: 0 },
});

function Harness(props: Options) { usePetPassthrough(props); return null; }
function businessCalls() { return mocks.invoke.mock.calls.filter(([name]) => name === "set_pet_cursor_passthrough"); }
async function render(options: Partial<Options> = {}, strict = false) {
  const props = {
    isDragging: false, spriteScreen: { x: 10, y: 20, w: 50, h: 80 },
    alphaHitTest: null, setCursorOnSprite: vi.fn(), ...options,
  };
  await act(() => {
    root.render(strict ? createElement(StrictMode, null, createElement(Harness, props)) : createElement(Harness, props));
  });
  return props;
}
async function move(x: number, y: number) {
  await act(() => { mocks.listeners.at(-1)!({ payload: cursor(x, y) }); });
}

beforeAll(() => { vi.useFakeTimers(); });
afterAll(() => { vi.clearAllTimers(); vi.useRealTimers(); });

beforeEach(async () => {
  mocks.invoke.mockReset().mockResolvedValue(undefined);
  // The observer belongs to this JS page, not a hook mount. Drain prior test cleanup facts.
  await vi.advanceTimersByTimeAsync(2000);
  mocks.invoke.mockClear();
  mocks.listeners.length = 0;
  mocks.unlisten.mockReset();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  Object.defineProperty(document, "elementFromPoint", { configurable: true, value: vi.fn(() => null) });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  active = true;
});

afterEach(async () => {
  if (active) await act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("usePetPassthrough evidence is observation only", () => {
  it("keeps native ignore deduplication, performs exactly the original alpha/DOM checks, and fail-opens on cleanup", async () => {
    const alpha = vi.fn(() => true);
    const props = await render({ alphaHitTest: alpha });
    await move(0, 0);
    await move(20, 30);
    await move(21, 31);
    await move(0, 0);
    expect(businessCalls().map(([, args]) => args.ignore)).toEqual([true, false, true]);
    expect(alpha).toHaveBeenCalledTimes(2);
    expect(document.elementFromPoint).toHaveBeenCalledTimes(4);
    expect(props.setCursorOnSprite).toHaveBeenLastCalledWith(false);
    await act(() => root.unmount());
    active = false;
    expect(businessCalls().map(([, args]) => args.ignore)).toEqual([true, false, true, true]);
    expect(mocks.unlisten).toHaveBeenCalledTimes(1);
  });

  it("does not retry rejected native calls or let a hung diagnostic transport block later input", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    let releaseEvidence: (() => void) | undefined;
    mocks.invoke.mockImplementation((name: string) => name === "record_pet_input_evidence"
      ? new Promise<void>((resolve) => { releaseEvidence = resolve; })
      : Promise.reject(new Error("native failure")));
    try {
      await render();
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(mocks.invoke.mock.calls.some(([name]) => name === "record_pet_input_evidence")).toBe(true);
      await move(20, 30);
      await move(21, 31);
      await move(0, 0);
      expect(businessCalls().map(([, args]) => args.ignore)).toEqual([true, false, true]);
      expect(warn).toHaveBeenCalledTimes(3);
    } finally {
      releaseEvidence?.();
      await act(async () => { await Promise.resolve(); });
    }
  });

  it("keeps StrictMode's setup/cleanup/setup calls and one real page identity", async () => {
    await render({}, true);
    const calls = businessCalls();
    expect(calls.map(([, args]) => args.ignore)).toEqual([true, true, true]);
    const metadata = calls.map(([, args]) => args.diagnostic);
    expect(metadata.every(Boolean)).toBe(true);
    expect(new Set(metadata.map((value) => value.pageInstanceId)).size).toBe(1);
    expect(new Set(metadata.map((value) => value.requestId)).size).toBe(3);
  });

  it("does no alpha or DOM work while dragging and retains the unconditional disabled cleanup", async () => {
    const alpha = vi.fn(() => true);
    const props = await render({ isDragging: true, alphaHitTest: alpha });
    await move(20, 30);
    await move(30, 40);
    expect(alpha).not.toHaveBeenCalled();
    expect(document.elementFromPoint).not.toHaveBeenCalled();
    expect(businessCalls().map(([, args]) => args.ignore)).toEqual([true, false]);
    await render({ ...props, disabled: true });
    expect(businessCalls().map(([, args]) => args.ignore)).toEqual([true, false, true, false]);
    await act(() => root.unmount());
    active = false;
    expect(businessCalls().at(-1)![1].ignore).toBe(true);
  });

  it.each(["slow", "full", "unresponsive"] as const)(
    "keeps all 1000 Hz business decisions and their outcomes independent of a %s diagnostic receiver",
    async (receiver) => {
      const warnings = vi.spyOn(console, "warn").mockImplementation(() => {});
      const alpha = vi.fn(() => true);
      const releases: Array<() => void> = [];
      const businessResults: Array<Promise<{ appliedIgnore: boolean }>> = [];
      const expectedOutcomes: Array<PromiseSettledResult<{ appliedIgnore: boolean }>> = [];
      const expectedIgnores = [true];
      const businessError = new Error("controlled native result");
      let diagnosticsCompleted = 0;
      let nativeCalls = 0;
      mocks.invoke.mockImplementation((name: string, args: { ignore: boolean }) => {
        if (name === "record_pet_input_evidence") {
          if (receiver === "full") return Promise.resolve(false);
          return new Promise<void>((resolve) => {
            const release = () => { diagnosticsCompleted++; resolve(); };
            releases.push(release);
            if (receiver === "slow") setTimeout(release, 10_000);
          });
        }
        const fails = ++nativeCalls % 17 === 0;
        const value = { appliedIgnore: args.ignore };
        const result = fails ? Promise.reject(businessError) : Promise.resolve(value);
        businessResults.push(result);
        expectedOutcomes.push(fails ? { status: "rejected", reason: businessError } : { status: "fulfilled", value });
        return result;
      });
      try {
        const props = await render({ alphaHitTest: alpha });
        await act(async () => {
          for (let sample = 0; sample < 1000; sample++) {
            // Two repeated outside and two repeated inside samples exercise native-command dedup.
            const inside = sample % 4 >= 2;
            const payload = cursor(inside ? 20 : 0, inside ? 30 : 0);
            payload.diagnostic!.sampleId = sample + 1;
            mocks.listeners.at(-1)!({ payload });
            if (!inside !== expectedIgnores.at(-1)) expectedIgnores.push(!inside);
            // The original business invoke happens within this cursor callback, not after a flush/ack.
            expect(businessCalls()).toHaveLength(expectedIgnores.length);
            expect(businessCalls().at(-1)![1].ignore).toBe(expectedIgnores.at(-1));
            await vi.advanceTimersByTimeAsync(1);
          }
        });
        expect(businessCalls().map(([, args]) => args.ignore)).toEqual(expectedIgnores);
        expect(businessCalls()).toHaveLength(500);
        expect(alpha).toHaveBeenCalledTimes(500);
        expect(document.elementFromPoint).toHaveBeenCalledTimes(1000);
        expect(props.setCursorOnSprite).toHaveBeenCalledTimes(1000);
        expect(await Promise.allSettled(businessResults)).toEqual(expectedOutcomes);
        expect(warnings).toHaveBeenCalledTimes(29);
        const batches = mocks.invoke.mock.calls.filter(([name]) => name === "record_pet_input_evidence");
        expect(batches.length).toBeGreaterThan(0);
        expect(batches.length).toBeLessThanOrEqual(4);
        if (receiver !== "full") {
          expect(batches).toHaveLength(1);
          expect(diagnosticsCompleted).toBe(0);
        }
        for (const [, args] of businessCalls()) expect(Object.keys(args).sort()).toEqual(["diagnostic", "ignore"]);
        await act(() => root.unmount());
        active = false;
        expect(businessCalls()).toHaveLength(501);
        expect(businessCalls().at(-1)![1].ignore).toBe(true);
        expect(props.setCursorOnSprite).toHaveBeenLastCalledWith(false);
        expect(await Promise.allSettled(businessResults)).toEqual(expectedOutcomes);
      } finally {
        // Restore only this test's receiver; no producer reset/restart behavior is added to the app.
        mocks.invoke.mockResolvedValue(undefined);
        for (const release of releases) release();
        await act(async () => { await vi.advanceTimersByTimeAsync(12_000); });
      }
    },
  );
});
