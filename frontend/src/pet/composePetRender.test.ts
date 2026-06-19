import { describe, it, expect, vi } from "vitest";
import { composePetRender } from "./composePetRender";
import type { ParamSource } from "./sources/types";

function makeMockSprite(): {
  onRender: ((renderer: unknown) => void) | null;
  setParameterValueById: ReturnType<typeof vi.fn>;
} {
  return {
    onRender: null,
    setParameterValueById: vi.fn(),
  } as unknown as {
    onRender: ((renderer: unknown) => void) | null;
    setParameterValueById: ReturnType<typeof vi.fn>;
  };
}

function makeMockSource(name: string, active = true): ParamSource & { log: string[] } {
  const log: string[] = [];
  return {
    get active() {
      log.push(`${name}.active`);
      return active;
    },
    apply(sprite) {
      log.push(`${name}.apply`);
      sprite.setParameterValueById(name, 1);
    },
    log,
  };
}

describe("composePetRender", () => {
  it("按数组顺序调用 source.apply 且 origOnRender 先执行", () => {
    const sprite = makeMockSprite();
    const orig = vi.fn();
    sprite.onRender = orig;

    const a = makeMockSource("A");
    const b = makeMockSource("B");
    composePetRender(sprite as unknown as import("easy-live2d").Live2DSprite, [a, b]);

    sprite.onRender?.("renderer");

    expect(orig).toHaveBeenCalledWith("renderer");
    expect(a.log).toEqual(["A.active", "A.apply"]);
    expect(b.log).toEqual(["B.active", "B.apply"]);
    expect(sprite.setParameterValueById.mock.calls).toEqual([
      ["A", 1],
      ["B", 1],
    ]);
  });

  it("origOnRender 在 source.apply 之前执行", () => {
    const sprite = makeMockSprite();
    const callOrder: string[] = [];
    sprite.onRender = () => {
      callOrder.push("orig");
    };

    const a = makeMockSource("A");
    composePetRender(sprite as unknown as import("easy-live2d").Live2DSprite, [a]);
    sprite.onRender?.("renderer");

    expect(callOrder[0]).toBe("orig");
  });

  it("跳过 active === false 的 source", () => {
    const sprite = makeMockSprite();
    const a = makeMockSource("A", true);
    const b = makeMockSource("B", false);
    composePetRender(sprite as unknown as import("easy-live2d").Live2DSprite, [a, b]);

    sprite.onRender?.("renderer");

    expect(a.log).toContain("A.apply");
    expect(b.log).toEqual(["B.active"]);
    expect(sprite.setParameterValueById).toHaveBeenCalledTimes(1);
  });

  it("detach 恢复原始 onRender", () => {
    const sprite = makeMockSprite();
    const orig = vi.fn();
    sprite.onRender = orig;

    const detach = composePetRender(sprite as unknown as import("easy-live2d").Live2DSprite, [
      makeMockSource("A"),
    ]);

    detach();

    expect(sprite.onRender).toBe(orig);
    expect(orig).not.toHaveBeenCalled();
  });

  it("onRender 为空时也能安全装载（orig 不传参）", () => {
    const sprite = makeMockSprite();
    sprite.onRender = null;

    const a = makeMockSource("A");
    composePetRender(sprite as unknown as import("easy-live2d").Live2DSprite, [a]);

    expect(() => sprite.onRender?.("renderer")).not.toThrow();
    expect(a.log).toContain("A.apply");
  });
});
