import { describe, expect, it, vi, afterEach } from "vitest";
import type { TextCadenceMouthDriver } from "./MouthDriver";
import { AudioRmsMouthDriver } from "./MouthDriver";

interface MockSprite {
  setParameterValueById: ReturnType<typeof vi.fn>;
}

function makeMockSprite(): MockSprite {
  return {
    setParameterValueById: vi.fn(),
  };
}

/**
 * 把 raf 替换为可由 vi.advanceTimersByTime 驱动的 stub：
 *   raf(cb) → setTimeout(cb, 16)
 *   caf(id) → clearTimeout(id)
 *
 * performance.now 也用 fake timer 时间驱动，让 sin 波时序可断言。
 *
 * 024 改造：driver 不再自己 hook onRender，而是实现 ParamSource.apply；测试用
 * applyRef.current 让 raf 每帧计算完 currentMouthValue 后触发一次 apply，
 * 模拟 composer 的 onRender 调用。
 */
function setupRafMock(
  applyRef: { current: () => void },
): { now: () => number } {
  let virtualNow = 0;
  vi.spyOn(performance, "now").mockImplementation(() => virtualNow);
  vi.useFakeTimers({ now: 0 });
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback): number => {
    return setTimeout(() => {
      virtualNow += 16;
      cb(virtualNow);
      applyRef.current();
    }, 16) as unknown as number;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number): void => {
    clearTimeout(id as unknown as ReturnType<typeof setTimeout>);
  });
  return {
    now: () => virtualNow,
  };
}

function teardownRafMock(): void {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
}

async function makeTextCadenceMouthDriver(): Promise<TextCadenceMouthDriver> {
  const { TextCadenceMouthDriver: Ctor } = await import("./MouthDriver");
  return new Ctor();
}

describe("TextCadenceMouthDriver", () => {
  afterEach(() => {
    teardownRafMock();
  });

  it("attach 之前 onTextDelta 是 no-op", async () => {
    const driver = await makeTextCadenceMouthDriver();
    const applyRef = { current: () => driver.apply({} as never) };
    setupRafMock(applyRef);
    driver.onTextDelta?.("hello");
    vi.advanceTimersByTime(100);
    const sprite = makeMockSprite();
    applyRef.current = () => driver.apply(sprite as never);
    driver.attach(sprite as never);
    vi.advanceTimersByTime(100);
    expect(sprite.setParameterValueById).not.toHaveBeenCalled();
  });

  it("空 text_delta 不入队（不开启 raf 循环）", async () => {
    const driver = await makeTextCadenceMouthDriver();
    const sprite = makeMockSprite();
    const applyRef = { current: () => driver.apply(sprite as never) };
    setupRafMock(applyRef);
    driver.attach(sprite as never);
    driver.onTextDelta?.("");
    vi.advanceTimersByTime(200);
    expect(sprite.setParameterValueById).not.toHaveBeenCalled();
  });

  it("attach + onTextDelta 1 段 → 走完 sin 波后回 0", async () => {
    const driver = await makeTextCadenceMouthDriver();
    const sprite = makeMockSprite();
    const applyRef = { current: () => driver.apply(sprite as never) };
    setupRafMock(applyRef);
    driver.attach(sprite as never);

    // 5 字 × 80ms = 400ms 驱嘴
    driver.onTextDelta?.("你好啊吗");

    // 推 200ms（一半时长，应该在峰值附近 ≈ 0.8）
    vi.advanceTimersByTime(200);
    expect(sprite.setParameterValueById).toHaveBeenCalled();
    const calls = sprite.setParameterValueById.mock.calls;
    const peakish = calls.find(([id, v]) => id === "ParamMouthOpenY" && v >= 0.5);
    expect(peakish).toBeDefined();

    // 推到时长结束，最终落 0
    vi.advanceTimersByTime(300);
    const lastCall = calls[calls.length - 1];
    expect(lastCall[0]).toBe("ParamMouthOpenY");
    expect(lastCall[1]).toBe(0);
  });

  it("多段 text_delta 串行（不并发驱嘴）", async () => {
    const driver = await makeTextCadenceMouthDriver();
    const sprite = makeMockSprite();
    const applyRef = { current: () => driver.apply(sprite as never) };
    setupRafMock(applyRef);
    driver.attach(sprite as never);

    driver.onTextDelta?.("hi");   // 2×80=160ms
    driver.onTextDelta?.("there"); // 5×80=400ms

    vi.advanceTimersByTime(50);
    const callsAfterMid1 = sprite.setParameterValueById.mock.calls.length;
    expect(callsAfterMid1).toBeGreaterThan(0);

    vi.advanceTimersByTime(120);
    vi.advanceTimersByTime(100);
    const allCalls = sprite.setParameterValueById.mock.calls;
    const nonZeroAfterFirstSegment = allCalls.slice(15).filter(([, v]) => v > 0);
    expect(nonZeroAfterFirstSegment.length).toBeGreaterThan(0);
  });

  it("detach 立即归零 + 清 queue + 后续 onTextDelta no-op", async () => {
    const driver = await makeTextCadenceMouthDriver();
    const sprite = makeMockSprite();
    const applyRef = { current: () => driver.apply(sprite as never) };
    setupRafMock(applyRef);
    driver.attach(sprite as never);
    driver.onTextDelta?.("running");

    vi.advanceTimersByTime(100);
    const callsBeforeDetach = sprite.setParameterValueById.mock.calls.length;
    expect(callsBeforeDetach).toBeGreaterThan(0);

    driver.detach();

    const lastCallAtDetach = sprite.setParameterValueById.mock.calls[
      sprite.setParameterValueById.mock.calls.length - 1
    ];
    expect(lastCallAtDetach).toEqual(["ParamMouthOpenY", 0]);

    const callsAfterDetach = sprite.setParameterValueById.mock.calls.length;
    vi.advanceTimersByTime(500);
    expect(sprite.setParameterValueById.mock.calls.length).toBe(callsAfterDetach);

    driver.onTextDelta?.("after");
    vi.advanceTimersByTime(200);
    expect(sprite.setParameterValueById.mock.calls.length).toBe(callsAfterDetach);
  });

  it("re-attach 后能继续工作", async () => {
    const driver = await makeTextCadenceMouthDriver();
    const sprite1 = makeMockSprite();
    const applyRef = { current: () => driver.apply(sprite1 as never) };
    setupRafMock(applyRef);
    driver.attach(sprite1 as never);
    driver.onTextDelta?.("first");
    vi.advanceTimersByTime(50);
    driver.detach();

    const sprite2 = makeMockSprite();
    applyRef.current = () => driver.apply(sprite2 as never);
    driver.attach(sprite2 as never);
    driver.onTextDelta?.("second");
    vi.advanceTimersByTime(100);
    expect(sprite2.setParameterValueById).toHaveBeenCalled();
  });
});

describe("AudioRmsMouthDriver (stub)", () => {
  it("stub 实现可实例化 + 接口三个方法可调（17c 占位）", () => {
    const driver = new AudioRmsMouthDriver();
    const sprite = makeMockSprite();
    driver.attach(sprite as never);
    driver.onAudioFrame?.(new Float32Array([0, 0.1, 0.2]), 16000);
    driver.detach();
    expect(sprite.setParameterValueById).not.toHaveBeenCalled();
  });
});
