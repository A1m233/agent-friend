import { describe, expect, it } from "vitest";
import {
  resolveOpenSessionActionMenu,
  sessionRunIndicator,
  shouldClearSessionActionFocusOnOpen,
  shouldResetMissingCurrentSession,
} from "./HistorySidebar";

const queuedRun = {
  run_id: "run-1",
  session_id: "session-1",
  status: "queued" as const,
  outcome: null,
  reason_code: "",
  channel: "text" as const,
  origin: "ag_ui",
  control_scope: "desktop_text",
  created_at: "2026-07-25T00:00:00Z",
  started_at: null,
  finished_at: null,
  version: 1,
};

describe("HistorySidebar missing current session guard", () => {
  it("keeps a locally-created conversation while the backend list is catching up", () => {
    expect(
      shouldResetMissingCurrentSession({
        sessionsLoaded: true,
        currentSessionId: "local-new-session",
        sessionIds: ["older-session"],
        streaming: true,
        historyLoading: false,
        messageCount: 2,
        detachedSessionId: null,
      }),
    ).toBe(false);

    expect(
      shouldResetMissingCurrentSession({
        sessionsLoaded: true,
        currentSessionId: "local-new-session",
        sessionIds: ["older-session"],
        streaming: false,
        historyLoading: false,
        messageCount: 2,
        detachedSessionId: null,
      }),
    ).toBe(false);
  });

  it("resets only an empty idle current session that is absent from the loaded list", () => {
    expect(
      shouldResetMissingCurrentSession({
        sessionsLoaded: true,
        currentSessionId: "missing-session",
        sessionIds: ["older-session"],
        streaming: false,
        historyLoading: false,
        messageCount: 0,
        detachedSessionId: null,
      }),
    ).toBe(true);
  });

  it("does not reset before the list is loaded or while history is loading", () => {
    expect(
      shouldResetMissingCurrentSession({
        sessionsLoaded: false,
        currentSessionId: "missing-session",
        sessionIds: [],
        streaming: false,
        historyLoading: false,
        messageCount: 0,
        detachedSessionId: null,
      }),
    ).toBe(false);

    expect(
      shouldResetMissingCurrentSession({
        sessionsLoaded: true,
        currentSessionId: "missing-session",
        sessionIds: [],
        streaming: false,
        historyLoading: true,
        messageCount: 0,
        detachedSessionId: null,
      }),
    ).toBe(false);
  });

  it("keeps an explicitly detached archived search result open", () => {
    expect(
      shouldResetMissingCurrentSession({
        sessionsLoaded: true,
        currentSessionId: "archived-session",
        sessionIds: ["normal-session"],
        streaming: false,
        historyLoading: false,
        messageCount: 0,
        detachedSessionId: "archived-session",
      }),
    ).toBe(false);
  });
});

describe("HistorySidebar run indicator", () => {
  it("active queued/running 优先于旧的 unread 终态", () => {
    expect(sessionRunIndicator(queuedRun, "failed")).toBe("queued");
    expect(
      sessionRunIndicator({ ...queuedRun, status: "running", started_at: queuedRun.created_at }, "completed"),
    ).toBe("running");
  });

  it("没有 active run 时显示后台完成或失败反馈", () => {
    expect(sessionRunIndicator(undefined, "completed")).toBe("completed");
    expect(sessionRunIndicator(undefined, "failed")).toBe("failed");
    expect(sessionRunIndicator(undefined, undefined)).toBeNull();
  });
});

describe("HistorySidebar session action menu", () => {
  it("keeps only the most recently opened menu surface", () => {
    const dropdown = resolveOpenSessionActionMenu(null, "session-1", "dropdown", true);
    const context = resolveOpenSessionActionMenu(dropdown, "session-2", "context", true);

    expect(context).toEqual({ sessionId: "session-2", surface: "context" });
  });

  it("ignores a stale close event from another surface", () => {
    const context = { sessionId: "session-1", surface: "context" } as const;

    expect(resolveOpenSessionActionMenu(context, "session-1", "dropdown", false)).toBe(context);
    expect(resolveOpenSessionActionMenu(context, "session-1", "context", false)).toBeNull();
  });
});

describe("HistorySidebar session action focus", () => {
  it("clears persistent focus visibility after a pointer click", () => {
    expect(shouldClearSessionActionFocusOnOpen(1)).toBe(true);
  });

  it("keeps keyboard focus visibility after Enter or Space activation", () => {
    expect(shouldClearSessionActionFocusOnOpen(0)).toBe(false);
  });
});
