import { describe, expect, it, vi } from "vitest";
import { syncPixiRendererDpr } from "./pixiRendererDpr";

describe("syncPixiRendererDpr", () => {
  it("updates renderer resolution and resizes the framebuffer", () => {
    const resize = vi.fn();
    const app = {
      renderer: {
        resolution: 1,
        resize,
      },
      screen: {
        width: 800,
        height: 600,
      },
    };

    expect(syncPixiRendererDpr(app, 2)).toBe(true);

    expect(app.renderer.resolution).toBe(2);
    expect(resize).toHaveBeenCalledWith(800, 600);
  });

  it("skips work when the renderer already matches the requested DPR", () => {
    const resize = vi.fn();
    const app = {
      renderer: {
        resolution: 2,
        resize,
      },
      screen: {
        width: 800,
        height: 600,
      },
    };

    expect(syncPixiRendererDpr(app, 2)).toBe(false);
    expect(resize).not.toHaveBeenCalled();
  });

  it("falls back to app resize hooks when renderer size is unavailable", () => {
    const resize = vi.fn();
    const app = {
      renderer: {
        resolution: 1,
      },
      resize,
    };

    expect(syncPixiRendererDpr(app, 1.5)).toBe(true);
    expect(app.renderer.resolution).toBe(1.5);
    expect(resize).toHaveBeenCalledTimes(1);
  });
});
