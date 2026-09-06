import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ invoke: vi.fn(), isTauri: vi.fn(() => true) }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@/utils/tauri", () => ({ isTauri: mocks.isTauri }));
import { acquireBubbleWindowVisibility, type BubbleWindowVisibility } from "./bubbleWindowVisibility";

let owners: BubbleWindowVisibility[];
function acquire() {
  const owner = acquireBubbleWindowVisibility();
  owners.push(owner);
  return owner;
}

async function flush() {
  for (let step = 0; step < 12; step++) await Promise.resolve();
}

function controlledNative() {
  let visible = false;
  const calls: Array<{ command: string; complete(): void; fail(): void }> = [];
  mocks.invoke.mockImplementation((command: string) => new Promise<void>((resolve, reject) => {
    calls.push({ command,
      complete: () => { visible = command === "show_bubble"; resolve(); },
      fail: () => reject(new Error("native unavailable")),
    });
  }));
  return { calls, visible: () => visible };
}

beforeEach(() => {
  owners = [];
  mocks.invoke.mockReset().mockResolvedValue(undefined);
  mocks.isTauri.mockReturnValue(true);
});

afterEach(async () => {
  owners.forEach((owner) => owner.dispose());
  await flush();
});

describe("DOM-owned bubble window visibility", () => {
  it("waits for an explicit DOM measurement, then keeps the window visible until DOM becomes empty", async () => {
    const owner = acquire();
    expect(mocks.invoke).not.toHaveBeenCalled();
    await owner.setVisible(true);
    await owner.setVisible(true); // A removed business record can still have an exiting DOM node.
    expect(mocks.invoke).toHaveBeenCalledExactlyOnceWith("show_bubble");
    await owner.setVisible(false);
    expect(mocks.invoke.mock.calls.map(([command]) => command)).toEqual(["show_bubble", "hide_bubble"]);
    owner.dispose();
    expect(mocks.invoke).toHaveBeenCalledTimes(2);
  });

  it("corrects a residual native window when the first measured DOM is empty", async () => {
    const owner = acquire();
    await owner.setVisible(false);
    await owner.setVisible(false);
    expect(mocks.invoke).toHaveBeenCalledExactlyOnceWith("hide_bubble");
  });

  it("coalesces changes behind a slow show and settles at the latest requested visibility", async () => {
    const native = controlledNative();
    const owner = acquire();
    const pending = [owner.setVisible(true)];
    await flush();
    pending.push(owner.setVisible(false), owner.setVisible(true), owner.setVisible(false));
    expect(native.calls.map(({ command }) => command)).toEqual(["show_bubble"]);
    native.calls[0].complete();
    await flush();
    expect(native.calls.map(({ command }) => command)).toEqual(["show_bubble", "hide_bubble"]);
    native.calls[1].complete();
    await Promise.all(pending);
    expect(native.visible()).toBe(false);
  });

  it("does not hide when content returns before the pending show has finished", async () => {
    const native = controlledNative();
    const owner = acquire();
    const pending = [owner.setVisible(true)];
    await flush();
    pending.push(owner.setVisible(false), owner.setVisible(true));
    native.calls[0].complete();
    await Promise.all(pending);
    expect(native.calls.map(({ command }) => command)).toEqual(["show_bubble"]);
    expect(native.visible()).toBe(true);
  });

  it("lets the replacement owner's hide finish after an old in-flight show", async () => {
    const native = controlledNative();
    const oldOwner = acquire();
    const show = oldOwner.setVisible(true);
    await flush();
    const owner = acquire();
    const hide = owner.setVisible(false);
    oldOwner.dispose();
    await oldOwner.setVisible(true);
    expect(native.calls.map(({ command }) => command)).toEqual(["show_bubble"]);
    native.calls[0].complete();
    await flush();
    expect(native.calls.map(({ command }) => command)).toEqual(["show_bubble", "hide_bubble"]);
    native.calls[1].complete();
    await Promise.all([show, hide]);
    expect(native.visible()).toBe(false);
  });

  it("drops a disposed owner's unissued hide instead of applying it to a StrictMode replacement", async () => {
    const native = controlledNative();
    const oldOwner = acquire();
    const pending = [oldOwner.setVisible(true)];
    await flush();
    pending.push(oldOwner.setVisible(false));
    oldOwner.dispose();
    const owner = acquire();
    pending.push(owner.setVisible(true));
    native.calls[0].complete();
    await Promise.all(pending);
    expect(native.calls.map(({ command }) => command)).toEqual(["show_bubble"]);
  });

  it("does not issue queued commands after disposal without a replacement owner", async () => {
    const owner = acquire();
    const pending = owner.setVisible(true);
    owner.dispose();
    await pending;
    expect(mocks.invoke).not.toHaveBeenCalled();
  });

  it("rejects one failed attempt without spinning and permits a later same-value retry", async () => {
    mocks.invoke.mockRejectedValueOnce(new Error("native unavailable"));
    const owner = acquire();
    await expect(owner.setVisible(true)).rejects.toThrow("native unavailable");
    await flush();
    expect(mocks.invoke).toHaveBeenCalledTimes(1);
    await owner.setVisible(true);
    expect(mocks.invoke).toHaveBeenCalledTimes(2);
  });

  it("allows a new owner to converge after its predecessor's command fails", async () => {
    const native = controlledNative();
    const oldOwner = acquire();
    const failed = expect(oldOwner.setVisible(true)).rejects.toThrow("native unavailable");
    await flush();
    oldOwner.dispose();
    const owner = acquire();
    const hide = owner.setVisible(false);
    native.calls[0].fail();
    await failed;
    await flush();
    expect(native.calls.map(({ command }) => command)).toEqual(["show_bubble", "hide_bubble"]);
    native.calls[1].complete();
    await hide;
    expect(native.visible()).toBe(false);
  });

  it("performs no native work outside Tauri", async () => {
    mocks.isTauri.mockReturnValue(false);
    const owner = acquire();
    await owner.setVisible(true);
    await owner.setVisible(false);
    owner.dispose();
    expect(mocks.invoke).not.toHaveBeenCalled();
  });
});
