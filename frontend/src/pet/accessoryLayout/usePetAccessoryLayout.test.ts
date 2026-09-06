// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccessoryContext, BubbleMeasurement } from "./protocol";

const { invokeMock, listeners } = vi.hoisted(() => ({
  invokeMock: vi.fn(),
  listeners: new Map<string, (event: { payload: BubbleMeasurement }) => void>(),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async (name: string, listener: (event: { payload: BubbleMeasurement }) => void) => {
  listeners.set(name, listener);
  return () => { if (listeners.get(name) === listener) listeners.delete(name); };
}) }));
vi.mock("@/utils/tauri", () => ({ isTauri: () => true }));

import { usePetAccessoryLayout } from "./usePetAccessoryLayout";
import { BUBBLE_MEASUREMENT_EVENT } from "./protocol";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const context: AccessoryContext = { generation: 3, origin: { x: -1600, y: 0 }, monitors: [
  { x: -1600, y: 0, width: 3200, height: 2000, scaleMilli: 1250, workArea: { x: -1600, y: 0, width: 3200, height: 1920 } },
] };
const measurement: BubbleMeasurement = { revision: 1, widthPhysical: 450, heightPhysical: 120, desiredWidthPhysical: 450, desiredHeightPhysical: 120, scaleFactor: 1.25, showRequested: true, retainBounds: false };
const viewport = { x: 0, y: 0, w: 1600, h: 1000 };
const anchor = { x: 700, y: 400, w: 100, h: 100 };
function Probe({ generation = 3, petAnchor = anchor, dpr = 2, buttonCount = 6, isDragging = false }: {
  generation?: number;
  petAnchor?: typeof anchor;
  dpr?: number;
  buttonCount?: number;
  isDragging?: boolean;
}) {
  const layout = usePetAccessoryLayout({ anchor: petAnchor, fallbackViewport: viewport, dpr, topologyGeneration: generation, buttonCount, isDragging });
  return createElement("output", { "data-side": layout?.side, "data-orientation": layout?.actionBar.orientation }, layout?.actionBar.top);
}

describe("pet layout coordinator", () => {
  let root: Root;
  beforeEach(() => {
    listeners.clear(); invokeMock.mockReset();
    invokeMock.mockImplementation(async (command: string) => {
      if (command === "begin_pet_accessory_layout") return { owner: 9, context, measurement };
      if (command === "get_pet_accessory_context") return context;
      return {};
    });
    const container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });
  afterEach(async () => { await act(async () => root.unmount()); document.body.replaceChildren(); });

  it("converts bubble and pet dimensions through their respective WebView scales", async () => {
    await act(async () => root.render(createElement(Probe)));
    const requests = invokeMock.mock.calls.filter(([cmd]) => cmd === "apply_pet_accessory_layout");
    const request = requests.at(-1)![1].request;
    expect(request.owner).toBe(9);
    expect(request.generation).toBe(3);
    expect(request.bubble.width).toBe(450);
    expect(request.bubble.x).toBe(-325);
    // Pet CSS y=400: 44px bar, 48px tooltip lane and three 8px gaps precede the bubble.
    expect(request.bubble.y + request.bubble.height).toBe(568);
  });

  it("ignores delayed older content measurements and does not repeatedly apply equal snapshots", async () => {
    await act(async () => root.render(createElement(Probe)));
    const listener = listeners.get(BUBBLE_MEASUREMENT_EVENT)!;
    const latest = { ...measurement, revision: 4, desiredHeightPhysical: 300 };
    await act(async () => listener({ payload: latest }));
    const count = invokeMock.mock.calls.filter(([cmd]) => cmd === "apply_pet_accessory_layout").length;
    await act(async () => { listener({ payload: measurement }); listener({ payload: { ...latest } }); });
    expect(invokeMock.mock.calls.filter(([cmd]) => cmd === "apply_pet_accessory_layout")).toHaveLength(count);
    expect(invokeMock.mock.calls.filter(([cmd]) => cmd === "apply_pet_accessory_layout").at(-1)![1].request.measurementRevision).toBe(4);
  });

  it("retains stationary close bounds but releases them immediately when dragging starts", async () => {
    await act(async () => root.render(createElement(Probe)));
    const lastRequest = () => invokeMock.mock.calls.filter(([cmd]) => cmd === "apply_pet_accessory_layout").at(-1)![1].request;
    expect(lastRequest().retainBounds).toBe(false);
    await act(async () => listeners.get(BUBBLE_MEASUREMENT_EVENT)!({ payload: { ...measurement, revision: 2, retainBounds: true } }));
    expect(lastRequest().retainBounds).toBe(true);
    const position = lastRequest().bubble;

    // The drag signal arrives before the avatar's next position update.
    await act(async () => root.render(createElement(Probe, { isDragging: true })));
    expect(lastRequest().retainBounds).toBe(false);
    expect(lastRequest().bubble).toEqual(position);
    await act(async () => listeners.get(BUBBLE_MEASUREMENT_EVENT)!({ payload: { ...measurement, revision: 3, retainBounds: true, desiredHeightPhysical: 200 } }));
    expect(lastRequest().retainBounds).toBe(false);
  });

  it("discards an old display-context response after a newer topology change", async () => {
    let resolveOld: ((value: AccessoryContext) => void) | undefined;
    invokeMock.mockImplementation(async (command: string) => {
      if (command === "begin_pet_accessory_layout") return { owner: 9, context, measurement };
      if (command === "get_pet_accessory_context") return new Promise<AccessoryContext>((resolve) => { resolveOld = resolve; });
      return {};
    });
    await act(async () => root.render(createElement(Probe)));
    const old = resolveOld!;
    invokeMock.mockImplementation(async (command: string) => command === "get_pet_accessory_context" ? { ...context, generation: 5 } : {});
    await act(async () => root.render(createElement(Probe, { generation: 5 })));
    await act(async () => old(context));
    expect(invokeMock.mock.calls.filter(([cmd]) => cmd === "apply_pet_accessory_layout").at(-1)![1].request.generation).toBe(5);
  });

  it("keeps the top-edge bar below the pet through hidden, short, tall and hidden measurements", async () => {
    invokeMock.mockImplementation(async (command: string) => {
      if (command === "begin_pet_accessory_layout") return { owner: 9, context, measurement: { ...measurement, showRequested: false } };
      if (command === "get_pet_accessory_context") return context;
      return {};
    });
    await act(async () => root.render(createElement(Probe, { petAnchor: { ...anchor, y: 0 } })));
    const lastRequest = () => invokeMock.mock.calls.filter(([cmd]) => cmd === "apply_pet_accessory_layout").at(-1)![1].request;
    expect(document.querySelector("output")?.dataset.side).toBe("bottom");
    expect(lastRequest().bubble).toBeNull();
    const listener = listeners.get(BUBBLE_MEASUREMENT_EVENT)!;
    for (const next of [
      { ...measurement, revision: 2 },
      { ...measurement, revision: 3, desiredHeightPhysical: 3600 },
      { ...measurement, revision: 4 },
      { ...measurement, revision: 5, showRequested: false },
    ]) {
      await act(async () => listener({ payload: next }));
      expect(document.querySelector("output")?.dataset.side).toBe("bottom");
      expect(document.querySelector("output")?.dataset.orientation).toBe("horizontal");
      expect(lastRequest().measurementRevision).toBe(next.revision);
      if (next.showRequested) {
        expect(lastRequest().bubble.y).toBeGreaterThan(200);
        expect(lastRequest().bubble.y + lastRequest().bubble.height).toBeLessThanOrEqual(1920);
      } else expect(lastRequest().bubble).toBeNull();
    }
  });

  it("retains edge hysteresis only on the same monitor and display generation", async () => {
    const makeMonitor = (x: number) => ({ x, y: 0, width: 1920, height: 1040, scaleMilli: 1000, workArea: { x, y: 0, width: 1920, height: 1040 } });
    let activeContext: AccessoryContext = { generation: 3, origin: { x: 0, y: 0 }, monitors: [makeMonitor(0), makeMonitor(1920)] };
    invokeMock.mockImplementation(async (command: string) => {
      if (command === "begin_pet_accessory_layout") return { owner: 9, context: activeContext, measurement: { ...measurement, showRequested: false } };
      if (command === "get_pet_accessory_context") return activeContext;
      return {};
    });
    const renderAt = async (x: number, generation = activeContext.generation) => {
      await act(async () => root.render(createElement(Probe, { petAnchor: { x, y: 500, w: 40, h: 40 }, dpr: 1, buttonCount: 12, generation })));
      return document.querySelector("output")?.dataset.side;
    };
    expect(await renderAt(40)).toBe("right");
    expect(await renderAt(140)).toBe("right"); // center 160: inside the 158 + 8px exit band
    expect(await renderAt(1920 + 140)).toBe("top");
    expect(await renderAt(1920 + 40)).toBe("right");
    expect(await renderAt(1920 + 140)).toBe("right");
    activeContext = { ...activeContext, generation: 4 };
    expect(await renderAt(1920 + 140, 4)).toBe("top");
  });

  it("recalculates top and bottom placement with work-area offsets and each overlay DPR", async () => {
    for (const dpr of [1, 1.25, 1.75, 2]) {
      const cssX = 700 / dpr;
      await act(async () => root.render(createElement(Probe, { petAnchor: { x: cssX, y: 0, w: 80 / dpr, h: 100 / dpr }, dpr })));
      expect(document.querySelector("output")?.dataset.side).toBe("bottom");
      const request = invokeMock.mock.calls.filter(([cmd]) => cmd === "apply_pet_accessory_layout").at(-1)![1].request;
      expect(request.bubble.width).toBe(450);
      expect(request.bubble.y).toBeGreaterThan(100);
      await act(async () => root.render(createElement(Probe, { petAnchor: { x: cssX, y: 1600 / dpr, w: 80 / dpr, h: 100 / dpr }, dpr })));
      expect(document.querySelector("output")?.dataset.side).toBe("top");
    }
  });
});
