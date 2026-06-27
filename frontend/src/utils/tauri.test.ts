import { afterEach, describe, expect, it } from "vitest";

import { isTauri } from "./tauri";

type TauriTestGlobal = typeof globalThis & {
  isTauri?: boolean;
  __TAURI__?: unknown;
  __TAURI_INTERNALS__?: unknown;
};

const tauriGlobal = globalThis as TauriTestGlobal;

afterEach(() => {
  delete tauriGlobal.isTauri;
  delete tauriGlobal.__TAURI__;
  delete tauriGlobal.__TAURI_INTERNALS__;
});

describe("isTauri", () => {
  it("returns false without Tauri globals", () => {
    expect(isTauri()).toBe(false);
  });

  it("recognizes the official isTauri marker", () => {
    tauriGlobal.isTauri = true;

    expect(isTauri()).toBe(true);
  });

  it("recognizes injected Tauri runtime globals", () => {
    tauriGlobal.__TAURI_INTERNALS__ = {};

    expect(isTauri()).toBe(true);
  });
});
