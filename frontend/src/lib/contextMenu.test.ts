import { describe, expect, it, vi } from "vitest";

import { installProductContextMenuSuppression } from "./contextMenu";

class FakeContextMenuTarget {
  listener: ((event: Event) => void) | null = null;

  addEventListener(type: "contextmenu", listener: (event: Event) => void): void {
    expect(type).toBe("contextmenu");
    this.listener = listener;
  }

  removeEventListener(type: "contextmenu", listener: (event: Event) => void): void {
    expect(type).toBe("contextmenu");
    if (this.listener === listener) this.listener = null;
  }
}

describe("installProductContextMenuSuppression", () => {
  it("prevents the native menu and removes the listener during cleanup", () => {
    const target = new FakeContextMenuTarget();
    const cleanup = installProductContextMenuSuppression({ target, enabled: true });
    const preventDefault = vi.fn();

    target.listener?.({ preventDefault } as unknown as Event);

    expect(preventDefault).toHaveBeenCalledOnce();
    cleanup();
    expect(target.listener).toBeNull();
  });

  it("does not install a listener outside the enabled runtime", () => {
    const target = new FakeContextMenuTarget();

    installProductContextMenuSuppression({ target, enabled: false });

    expect(target.listener).toBeNull();
  });
});
