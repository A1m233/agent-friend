import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import {
  usePetStateStore,
  setPetStatePolicy,
  resetPetStatePolicy,
} from "./petState";
import type { PetStatePolicy } from "./petStatePolicy";
import type { PushEnvelope } from "@/types/push";

function emptyEnv(): PushEnvelope {
  return {
    kind: "agent_turn",
    session_id: "s1",
    seq: 1,
    source_kind: null,
    events: [],
  };
}

describe("usePetStateStore", () => {
  beforeEach(() => {
    resetPetStatePolicy();
    usePetStateStore.setState({ phase: "idle" });
  });

  it("初始态 = idle", () => {
    expect(usePetStateStore.getState().phase).toBe("idle");
  });

  it("ingest 时 policy 决策的 next 与当前态相同则不动 store", () => {
    const noop: PetStatePolicy = (_, current) => ({ from: current, next: current });
    setPetStatePolicy(noop);
    const before = usePetStateStore.getState().phase;
    usePetStateStore.getState().ingest(emptyEnv());
    expect(usePetStateStore.getState().phase).toBe(before);
  });

  it("ingest 时 policy 返立即 transition → phase 同步切", () => {
    const toSpeaking: PetStatePolicy = (_, current) => ({
      from: current,
      next: "speaking",
    });
    setPetStatePolicy(toSpeaking);
    usePetStateStore.getState().ingest(emptyEnv());
    expect(usePetStateStore.getState().phase).toBe("speaking");
  });

  it("ingest 时 policy 返延迟 transition → 延迟到期后才切", async () => {
    vi.useFakeTimers();
    try {
      const delayedToIdle: PetStatePolicy = (_, current) => ({
        from: current,
        next: "idle",
        delayMs: 300,
      });
      // 先把 store 切到 speaking
      usePetStateStore.setState({ phase: "speaking" });
      setPetStatePolicy(delayedToIdle);
      usePetStateStore.getState().ingest(emptyEnv());
      // 延迟前 phase 仍 speaking
      expect(usePetStateStore.getState().phase).toBe("speaking");
      vi.advanceTimersByTime(299);
      expect(usePetStateStore.getState().phase).toBe("speaking");
      vi.advanceTimersByTime(1);
      expect(usePetStateStore.getState().phase).toBe("idle");
    } finally {
      vi.useRealTimers();
    }
  });

  it("延迟切到期前被新 envelope 抢断态：延迟回调 noop", async () => {
    vi.useFakeTimers();
    try {
      const nextOut: "idle" | "speaking" = "idle";
      const dynamicPolicy: PetStatePolicy = (_, current) => ({
        from: current,
        next: nextOut,
        delayMs: nextOut === "idle" ? 300 : undefined,
      });
      usePetStateStore.setState({ phase: "speaking" });
      setPetStatePolicy(dynamicPolicy);

      // 首次 ingest 安排 speaking → idle 延迟切
      usePetStateStore.getState().ingest(emptyEnv());
      expect(usePetStateStore.getState().phase).toBe("speaking");

      // 100ms 时来一帧把 phase 改回 speaking（这里直接 setState 模拟抢断）
      vi.advanceTimersByTime(100);
      usePetStateStore.setState({ phase: "thinking" });

      // 推到 300ms 触发延迟回调 —— 但 from 是 speaking，phase 已 thinking，noop
      vi.advanceTimersByTime(200);
      expect(usePetStateStore.getState().phase).toBe("thinking");
    } finally {
      vi.useRealTimers();
    }
  });

  it("raiseError 直接进 error 态", () => {
    usePetStateStore.setState({ phase: "speaking" });
    usePetStateStore.getState().raiseError();
    expect(usePetStateStore.getState().phase).toBe("error");
  });

  it("reset 回 idle 态", () => {
    usePetStateStore.setState({ phase: "error" });
    usePetStateStore.getState().reset();
    expect(usePetStateStore.getState().phase).toBe("idle");
  });

  it("setPetStatePolicy 注入测试 policy + resetPetStatePolicy 恢复", () => {
    const alwaysError: PetStatePolicy = (_, current) => ({
      from: current,
      next: "error",
    });
    setPetStatePolicy(alwaysError);
    usePetStateStore.getState().ingest(emptyEnv());
    expect(usePetStateStore.getState().phase).toBe("error");

    resetPetStatePolicy();
    // default policy 对空 events 不切态
    usePetStateStore.setState({ phase: "idle" });
    usePetStateStore.getState().ingest(emptyEnv());
    expect(usePetStateStore.getState().phase).toBe("idle");
  });
});

afterEach(() => {
  resetPetStatePolicy();
  usePetStateStore.setState({ phase: "idle" });
});
