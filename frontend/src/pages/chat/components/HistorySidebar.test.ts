import { describe, expect, it } from "vitest";
import { shouldResetMissingCurrentSession } from "./HistorySidebar";

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
      }),
    ).toBe(false);
  });
});
