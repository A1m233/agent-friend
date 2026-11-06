// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { collectMessageExposure, displayExtent, registerMessageExposure, resetMessageExposure } from "./messageExposure";

afterEach(() => { resetMessageExposure(); vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.replaceChildren(); });

it("requires stable focused display and resets on background, layout change, suspension and disposal", async () => {
  vi.stubGlobal("crypto", webcrypto);
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.stubGlobal("getComputedStyle", () => ({ visibility: "visible", display: "block", opacity: "1", overflowX: "visible", overflowY: "visible" }));
  const wrapper = document.createElement("div");
  const child = document.createElement("div");
  wrapper.append(child); document.body.append(wrapper);
  let rect = new DOMRect(20, 20, 200, 100);
  vi.spyOn(child, "getBoundingClientRect").mockImplementation(() => rect);
  vi.spyOn(child, "getClientRects").mockImplementation(() => [rect] as unknown as DOMRectList);
  Object.defineProperty(document, "elementFromPoint", { configurable: true, value: () => child });
  const dispose = registerMessageExposure(wrapper, "msg", "正文");
  try {
    expect(await collectMessageExposure(true, 0)).toEqual([]);
    expect(await collectMessageExposure(true, 1999)).toEqual([]);
    expect((await collectMessageExposure(true, 2000))[0]).toMatchObject({ messageId: "msg", extent: "full" });
    expect(await collectMessageExposure(false, 2500)).toEqual([]);
    expect(await collectMessageExposure(true, 3000)).toEqual([]);
    rect = new DOMRect(20, 30, 200, 100);
    expect(await collectMessageExposure(true, 5000)).toEqual([]);
    expect(await collectMessageExposure(true, 16000)).toEqual([]);
    expect((await collectMessageExposure(true, 18000)).length).toBe(1);
    dispose();
    expect(await collectMessageExposure(true, 20000)).toEqual([]);
  } finally { dispose(); }
});

describe("message display extent is not a read receipt", () => {
  const clip = { top: 20, left: 0, bottom: 400, right: 600 };
  it("distinguishes fully shown, long clipped and offscreen text", () => {
    expect(displayExtent(new DOMRect(10, 30, 200, 100), clip)).toBe("full");
    expect(displayExtent(new DOMRect(10, 30, 200, 800), clip)).toBe("partial");
    expect(displayExtent(new DOMRect(10, -80, 200, 100), clip)).toBeNull();
    expect(displayExtent(new DOMRect(10, 30, 0, 100), clip)).toBeNull();
  });
  it("accounts for header and horizontal clipping", () => {
    expect(displayExtent(new DOMRect(10, 10, 200, 100), clip)).toBe("partial");
    expect(displayExtent(new DOMRect(-20, 30, 200, 100), clip)).toBe("partial");
  });
});
