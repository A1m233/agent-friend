import { afterEach, describe, expect, it, vi } from "vitest";

const ready = vi.hoisted(() => vi.fn(() => Promise.resolve()));
vi.mock("@/services/runtime", () => ({ ensureLocalServiceReady: ready }));
import { retainSessionUsage } from "./sessionUsage";

afterEach(() => { vi.unstubAllGlobals(); ready.mockReset(); });

describe("session usage connection", () => {
  it("closes only its own connection when another window still uses the session", async () => {
    const closes: ReturnType<typeof vi.fn>[] = [];
    vi.stubGlobal("EventSource", class {
      close = vi.fn();
      constructor() { closes.push(this.close); }
    });
    ready.mockResolvedValue(undefined);
    const closeOld = retainSessionUsage("a");
    const closeNew = retainSessionUsage("a");
    await Promise.resolve();
    closeOld();
    expect(closes[0]).toHaveBeenCalledOnce();
    expect(closes[1]).not.toHaveBeenCalled();
    closeNew();
    expect(closes[1]).toHaveBeenCalledOnce();
  });

  it("does not resurrect a closed view after readiness finishes", async () => {
    let resolve!: () => void;
    ready.mockReturnValue(new Promise<void>((done) => { resolve = done; }));
    const create = vi.fn();
    vi.stubGlobal("EventSource", create);
    const close = retainSessionUsage("loading");
    close(); resolve();
    await Promise.resolve();
    expect(create).not.toHaveBeenCalled();
  });
});
