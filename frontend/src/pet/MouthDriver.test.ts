import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { TextCadenceMouthDriver, AudioRmsMouthDriver } from "./MouthDriver";

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
 */
function setupRafMock(): { now: () => number } {
  let virtualNow = 0;
  vi.spyOn(performance, "now").mockImplementation(() => virtualNow);
  vi.useFakeTimers({ now: 0 });
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback): number => {
    return setTimeout(() => {
      virtualNow += 16;
      cb(virtualNow);
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

describe("TextCadenceMouthDriver", () => {
  beforeEach(() => {
    setupRafMock();
  });

  afterEach(() => {
    teardownRafMock();
  });

  it("attach 之前 onTextDelta 是 no-op", () => {
    const driver = new TextCadenceMouthDriver();
    driver.onTextDelta?.("hello");
    // 推时间 100ms，确保没有 raf 回调被排队
    vi.advanceTimersByTime(100);
    // 没 sprite 也没 timer 在跑，advanceTimers 不会触发 setParameterValueById
    // —— 通过"没 throw"+"用上 sprite 也是 0 调用"验证
    const sprite = makeMockSprite();
    driver.attach(sprite as never);
    vi.advanceTimersByTime(100);
    expect(sprite.setParameterValueById).not.toHaveBeenCalled();
  });

  it("空 text_delta 不入队（不开启 raf 循环）", () => {
    const driver = new TextCadenceMouthDriver();
    const sprite = makeMockSprite();
    driver.attach(sprite as never);
    driver.onTextDelta?.("");
    vi.advanceTimersByTime(200);
    expect(sprite.setParameterValueById).not.toHaveBeenCalled();
  });

  it("attach + onTextDelta 1 段 → 走完 sin 波后回 0", () => {
    const driver = new TextCadenceMouthDriver();
    const sprite = makeMockSprite();
    driver.attach(sprite as never);

    // 5 字 × 80ms = 400ms 驱嘴
    driver.onTextDelta?.("你好啊吗");

    // 推 200ms（一半时长，应该在峰值附近 ≈ MOUTH_OPEN_PEAK * sin(π/2) ≈ 0.8）
    // 4 字（不是 5）—— 改成 5 字 "你好啊吗?"，让计算更直观
    // 实际上 raf step 是 16ms 一帧，200ms 内约 12 帧
    vi.advanceTimersByTime(200);
    expect(sprite.setParameterValueById).toHaveBeenCalled();
    const calls = sprite.setParameterValueById.mock.calls;
    // 至少一次调用的值在峰值区间（[0.5, 0.8]，对应 t in [0.25, 0.75]）
    const peakish = calls.find(([id, v]) => id === "ParamMouthOpenY" && v >= 0.5);
    expect(peakish).toBeDefined();

    // 推到时长结束（5 字 × 80ms = 400ms，加余量 200ms 总共 400ms），最终落 0
    vi.advanceTimersByTime(300);
    const lastCall = calls[calls.length - 1];
    expect(lastCall[0]).toBe("ParamMouthOpenY");
    expect(lastCall[1]).toBe(0);
  });

  it("多段 text_delta 串行（不并发驱嘴）", () => {
    const driver = new TextCadenceMouthDriver();
    const sprite = makeMockSprite();
    driver.attach(sprite as never);

    driver.onTextDelta?.("hi");   // 2×80=160ms → 抬到 min 100ms? 不,是 max(100, 2*80) = 160ms
    driver.onTextDelta?.("there"); // 5×80=400ms

    // 推 50ms：第一段在跑（中点附近）
    vi.advanceTimersByTime(50);
    const callsAfterMid1 = sprite.setParameterValueById.mock.calls.length;
    expect(callsAfterMid1).toBeGreaterThan(0);

    // 推到 160ms：第一段刚结束（落 0），第二段还没开始
    // raf 步进可能让落 0 + 启动下一段在同一帧附近
    vi.advanceTimersByTime(120);
    // 此时第二段已开始（或刚开始），检查后续帧值不全为 0（驱嘴在动）
    vi.advanceTimersByTime(100);  // 第二段中段
    const allCalls = sprite.setParameterValueById.mock.calls;
    const nonZeroAfterFirstSegment = allCalls.slice(15).filter(([, v]) => v > 0);
    expect(nonZeroAfterFirstSegment.length).toBeGreaterThan(0);
  });

  it("detach 立即归零 + 清 queue + 后续 onTextDelta no-op", () => {
    const driver = new TextCadenceMouthDriver();
    const sprite = makeMockSprite();
    driver.attach(sprite as never);
    driver.onTextDelta?.("running");

    // 推 100ms，驱嘴中
    vi.advanceTimersByTime(100);
    const callsBeforeDetach = sprite.setParameterValueById.mock.calls.length;
    expect(callsBeforeDetach).toBeGreaterThan(0);

    driver.detach();

    // detach 立即调一次 setParameterValueById("ParamMouthOpenY", 0)
    const lastCallAtDetach = sprite.setParameterValueById.mock.calls[
      sprite.setParameterValueById.mock.calls.length - 1
    ];
    expect(lastCallAtDetach).toEqual(["ParamMouthOpenY", 0]);

    // 推时间，不再有新调用
    const callsAfterDetach = sprite.setParameterValueById.mock.calls.length;
    vi.advanceTimersByTime(500);
    expect(sprite.setParameterValueById.mock.calls.length).toBe(callsAfterDetach);

    // 后续 onTextDelta 也是 no-op（sprite 已 null）
    driver.onTextDelta?.("after");
    vi.advanceTimersByTime(200);
    expect(sprite.setParameterValueById.mock.calls.length).toBe(callsAfterDetach);
  });

  it("re-attach 后能继续工作", () => {
    const driver = new TextCadenceMouthDriver();
    const sprite1 = makeMockSprite();
    driver.attach(sprite1 as never);
    driver.onTextDelta?.("first");
    vi.advanceTimersByTime(50);
    driver.detach();

    // re-attach 到新 sprite
    const sprite2 = makeMockSprite();
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
    // 不应 throw
    driver.attach(sprite as never);
    driver.onAudioFrame?.(new Float32Array([0, 0.1, 0.2]), 16000);
    driver.detach();
    // 本期 stub 不调 setParameterValueById
    expect(sprite.setParameterValueById).not.toHaveBeenCalled();
  });
});
