// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { RunSnapshot } from "@/types/run";
import type { ChatMessage } from "@/types/chat";
import { projectSessionEvents } from "@/stores/sessionProjection";
import type { RunCalibrationPhase } from "@/stores/runPresentation";
import { RunExecutionDisclosure } from "./RunExecutionDisclosure";

function DurationFixture({ message }: { message: ChatMessage }) {
  return createElement(RunExecutionDisclosure, { message, enabled: false, collapse: false, children: () => null });
}

const state = vi.hoisted(() => ({
  runSnapshots: {} as Record<string, RunSnapshot>,
  runCalibrationPhase: "ready" as RunCalibrationPhase,
  stopRequestsByRun: {} as Record<string, unknown>,
}));
vi.mock("@/stores", () => ({ useConversationStore: (select: (value: typeof state) => unknown) => select(state) }));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it.each([false, true])("hides the whole header until admission/start without counting queue time (foldable=%s)", async (enabled) => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-05T00:00:00Z"));
  const createdAt = new Date().toISOString();
  const message: ChatMessage = { id: "m", runId: "r", role: "assistant", blocks: [], status: "streaming", createdAt };
  const container = document.createElement("div");
  const root = createRoot(container);
  const render = () => act(async () => root.render(createElement(RunExecutionDisclosure, {
    message, enabled, collapse: false, children: () => createElement("div", { "data-body": true }, "content"),
  })));
  const expectPending = () => {
    expect(container.querySelector("[data-run-duration]")).toBeNull();
    expect(container.querySelector("[data-run-status-line]")).toBeNull();
    expect(container.querySelector("[data-run-separator]")).toBeNull();
    expect(container.querySelector("button")).toBeNull();
    expect(container.textContent).not.toContain("用时未知");
    expect(vi.getTimerCount()).toBe(0);
  };
  try {
    state.runCalibrationPhase = "calibrating";
    await render();
    expectPending();
    const body = container.querySelector("[data-body]");
    state.runSnapshots.r = { run_id: "r", status: "queued", outcome: null,
      created_at: createdAt, started_at: null, finished_at: null, version: 0 } as RunSnapshot;
    await render();
    expectPending();
    state.runCalibrationPhase = "ready";
    await render();
    expectPending();
    state.runSnapshots.r = { ...state.runSnapshots.r, version: 1 };
    await act(async () => vi.advanceTimersByTime(15_000));
    await render();
    expectPending();
    state.runSnapshots.r = { ...state.runSnapshots.r, status: "running", started_at: new Date().toISOString(), version: 2 };
    await render();
    expect(container.querySelector("[data-run-duration]")?.textContent).toBe("已进行 0秒");
    expect(container.querySelectorAll("[data-run-separator]")).toHaveLength(1);
    expect(container.querySelectorAll("button")).toHaveLength(enabled ? 1 : 0);
    expect(container.querySelector("[data-body]")).toBe(body);
    await act(async () => vi.advanceTimersByTime(1000));
    expect(container.querySelector("[data-run-duration]")?.textContent).toBe("已进行 1秒");
  } finally {
    await act(async () => root.unmount());
    state.runCalibrationPhase = "ready";
    state.runSnapshots = {};
    vi.useRealTimers();
  }
});

it.each(["cancelled", "rejected", "failed"] as const)("shows a %s terminal without inventing queue duration", async (outcome) => {
  const container = document.createElement("div");
  const root = createRoot(container);
  const message: ChatMessage = { id: "m", runId: "r", role: "assistant", blocks: [], status: "streaming", createdAt: "t" };
  const render = () => act(async () => root.render(createElement(DurationFixture, { message })));
  state.runSnapshots.r = { run_id: "r", status: "queued", started_at: null, finished_at: null } as RunSnapshot;
  try {
    await render();
    expect(container.querySelector("[data-run-separator]")).toBeNull();
    state.runSnapshots.r = { ...state.runSnapshots.r, status: "terminal", outcome, finished_at: new Date().toISOString() };
    state.runCalibrationPhase = "calibrating";
    await render();
    expect(container.querySelector("[data-run-duration]")?.textContent).toBe(outcome === "cancelled" ? "已停止，用时未知" : "用时未知");
    expect(container.querySelectorAll("[data-run-separator]")).toHaveLength(1);
  } finally {
    await act(async () => root.unmount());
    state.runCalibrationPhase = "ready";
    state.runSnapshots = {};
  }
});

it.each([null, "invalid"])("keeps a running snapshot with malformed start %s explicitly unknown", async (started_at) => {
  const container = document.createElement("div");
  const root = createRoot(container);
  state.runSnapshots.r = { run_id: "r", status: "running", started_at, finished_at: null } as RunSnapshot;
  try {
    await act(async () => root.render(createElement(DurationFixture, { message: {
      id: "m", runId: "r", role: "assistant", blocks: [], status: "streaming", createdAt: "t",
    } })));
    expect(container.querySelector("[data-run-duration]")?.textContent).toBe("进行中，用时未知");
    expect(container.querySelectorAll("[data-run-separator]")).toHaveLength(1);
  } finally { await act(async () => root.unmount()); state.runSnapshots = {}; }
});

it("does not hide an existing partial response just because its run timing is missing", async () => {
  const container = document.createElement("div");
  const root = createRoot(container);
  try {
    await act(async () => root.render(createElement(DurationFixture, { message: {
      id: "m", runId: "missing", role: "assistant", status: "streaming", createdAt: "t",
      blocks: [{ kind: "text", mid: "text", text: "partial response", phase: "unknown" }],
    } })));
    expect(container.querySelector("[data-run-duration]")?.textContent).toBe("用时未知");
    expect(container.querySelectorAll("[data-run-separator]")).toHaveLength(1);
  } finally { await act(async () => root.unmount()); }
});

it("ticks through stopping, freezes on terminal and reloads anchors without a model capability", async () => {
  vi.useFakeTimers();
  const started = "2026-09-05T00:00:00Z";
  vi.setSystemTime(new Date(started));
  state.runSnapshots.r = { run_id: "r", status: "running", started_at: started, finished_at: null } as RunSnapshot;
  const container = document.createElement("div");
  const root = createRoot(container);
  let message: ChatMessage = { id: "m", runId: "r", role: "assistant", blocks: [], status: "streaming", createdAt: started };
  const render = () => act(async () => root.render(createElement(DurationFixture, { message })));
  try {
    await render();
    expect(container.textContent).toBe("已进行 0秒");
    expect(vi.getTimerCount()).toBe(1);
    await act(async () => vi.advanceTimersByTime(2000));
    expect(container.textContent).toBe("已进行 2秒");
    state.stopRequestsByRun.r = true;
    await render();
    await act(async () => vi.advanceTimersByTime(2000));
    expect(container.textContent).toBe("已进行 4秒");
    state.runSnapshots.r = { ...state.runSnapshots.r, status: "terminal", outcome: "cancelled", finished_at: new Date().toISOString() };
    await render();
    await act(async () => vi.advanceTimersByTime(9000));
    expect(container.textContent).toBe("4秒 后被停止");
    expect(vi.getTimerCount()).toBe(0);
    expect(container.querySelector("button")).toBeNull();
    delete state.runSnapshots.r;
    message = { ...message, status: "complete" };
    await render();
    expect(container.textContent).toContain("用时未知");
    const events = [
      { type: "run_started", payload: { started_at: started } },
      { type: "user_message", payload: { content: "fixture" } },
      { type: "assistant_message", payload: { content: "partial", partial: true } },
      { type: "run_terminal", payload: { status: "interrupted", started_at: started, finished_at: "2026-09-05T00:00:04Z" } },
    ].map((event, index) => ({ ...event, uuid: `event-${index}`, ts: started, meta: { run_id: "r" } }));
    message = projectSessionEvents(events)[1];
    await render();
    expect(container.textContent).toBe("4秒 后被停止");
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(container.textContent).toBe("4秒 后被停止");
    message = projectSessionEvents(events.map((event) => event.type === "run_terminal"
      ? { ...event, payload: { ...event.payload, recovered: true } } : event))[1];
    await render();
    expect(container.textContent).toBe("已停止，用时未知");
  } finally {
    await act(async () => root.unmount());
    state.stopRequestsByRun = {};
    state.runSnapshots = {};
    vi.useRealTimers();
  }
});

it.each(["explicit_request", "runtime_stopping", "computer_or_owner_runtime_lost", ""])(
  "does not attribute a stopped run to its reason: %s", async (reason_code) => {
    const container = document.createElement("div");
    const root = createRoot(container);
    state.runSnapshots.r = { run_id: "r", status: "terminal", outcome: "cancelled", reason_code,
      started_at: "2026-09-05T00:00:00Z", finished_at: "2026-09-05T00:00:32Z" } as RunSnapshot;
    try {
      await act(async () => root.render(createElement(DurationFixture, { message: {
        id: "m", runId: "r", role: "assistant", blocks: [], status: "interrupted", createdAt: "t",
      } })));
      expect(container.textContent).toBe("32秒 后被停止");
    } finally { await act(async () => root.unmount()); state.runSnapshots = {}; }
  },
);

it.each(["failed", "expired", "skipped", "rejected", "completed"] as const)(
  "does not label an explicit %s history outcome as stopped", async (runOutcome) => {
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      await act(async () => root.render(createElement(DurationFixture, { message: {
        id: "m", role: "assistant", blocks: [], status: "interrupted", runOutcome, createdAt: "t",
        runStartedAt: "2026-09-05T00:00:00Z", runFinishedAt: "2026-09-05T00:00:32Z",
      } })));
      expect(container.textContent).toBe("用时 32秒");
    } finally { await act(async () => root.unmount()); }
  },
);

it.each(["running", "queued"] as const)("prefers a live %s snapshot over stale interrupted history", async (status) => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-05T00:00:04Z"));
  const container = document.createElement("div");
  const root = createRoot(container);
  state.runSnapshots.r = { run_id: "r", status, outcome: null,
    started_at: status === "running" ? "2026-09-05T00:00:00Z" : null, finished_at: null } as RunSnapshot;
  try {
    await act(async () => root.render(createElement(DurationFixture, { message: {
      id: "m", runId: "r", role: "assistant", blocks: [], status: "interrupted", runOutcome: "cancelled", createdAt: "t",
    } })));
    expect(container.textContent).toBe(status === "running" ? "已进行 4秒" : "");
  } finally { await act(async () => root.unmount()); state.runSnapshots = {}; vi.useRealTimers(); }
});
