import { describe, expect, it, vi } from "vitest";

import { runNativeContextMenuPopup } from "./nativeContextMenuLifecycle";

describe("runNativeContextMenuPopup", () => {
  it("keeps the owner open until the native popup completes", async () => {
    let resolvePopup: (() => void) | undefined;
    const popup = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolvePopup = resolve;
        }),
    );
    const onOpenChange = vi.fn();

    const pending = runNativeContextMenuPopup({ popup, onOpenChange });

    expect(onOpenChange).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenLastCalledWith(true);
    resolvePopup?.();
    await pending;

    expect(onOpenChange).toHaveBeenNthCalledWith(2, false);
  });

  it("clears the open state when the native popup fails", async () => {
    const error = new Error("popup failed");
    const onOpenChange = vi.fn();

    await expect(
      runNativeContextMenuPopup({
        popup: () => Promise.reject(error),
        onOpenChange,
      }),
    ).rejects.toBe(error);

    expect(onOpenChange.mock.calls).toEqual([[true], [false]]);
  });
});
