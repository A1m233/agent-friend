import { describe, expect, it, vi } from "vitest";
import { createAvatarScalePreviewScheduler } from "./avatarScalePreview";

describe("avatar scale preview scheduler", () => {
  it("coalesces changes in one frame and publishes the latest values", () => {
    const publish = vi.fn();
    const scheduled: FrameRequestCallback[] = [];
    const scheduler = createAvatarScalePreviewScheduler(
      publish,
      (callback) => {
        scheduled.push(callback);
        return 1;
      },
      vi.fn(),
    );

    scheduler.queue({ modelId: "model-a", modelScalePercent: 101 });
    scheduler.queue({ modelId: "model-a", modelScalePercent: 102 });
    scheduler.queue({ miniScalePercent: 49 });

    expect(publish).not.toHaveBeenCalled();
    expect(scheduled).toHaveLength(1);
    scheduled[0]?.(0);
    expect(publish).toHaveBeenCalledOnce();
    expect(publish).toHaveBeenCalledWith({
      modelId: "model-a",
      modelScalePercent: 102,
      miniScalePercent: 49,
    });
  });

  it("flushes the pending final value immediately", () => {
    const publish = vi.fn();
    const cancelFrame = vi.fn();
    const scheduler = createAvatarScalePreviewScheduler(
      publish,
      () => 7,
      cancelFrame,
    );

    scheduler.queue({ modelId: "model-a", modelScalePercent: 137 });
    scheduler.flush();

    expect(cancelFrame).toHaveBeenCalledWith(7);
    expect(publish).toHaveBeenCalledWith({
      modelId: "model-a",
      modelScalePercent: 137,
    });
  });
});
