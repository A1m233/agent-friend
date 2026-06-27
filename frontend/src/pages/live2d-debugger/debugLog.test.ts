import { describe, expect, it } from "vitest";

import {
  MAX_DEBUG_LOG_ENTRIES,
  debugLogReducer,
  describeCommand,
  type DebugLogEntry,
} from "./debugLog";

function entry(id: string): DebugLogEntry {
  return {
    requestId: id,
    createdAt: 0,
    title: id,
    detail: "",
    status: "pending",
    result: null,
  };
}

describe("debugLogReducer", () => {
  it("prepends pending entries and caps the log", () => {
    let state: DebugLogEntry[] = [];
    for (let i = 0; i < MAX_DEBUG_LOG_ENTRIES + 5; i += 1) {
      state = debugLogReducer(state, {
        type: "append",
        now: i,
        entry: { requestId: `r${i}`, title: `t${i}`, detail: "" },
      });
    }

    expect(state).toHaveLength(MAX_DEBUG_LOG_ENTRIES);
    expect(state[0].requestId).toBe(`r${MAX_DEBUG_LOG_ENTRIES + 4}`);
  });

  it("resolves pending entries with response status", () => {
    const state = debugLogReducer([entry("r1")], {
      type: "resolve",
      response: {
        requestId: "r1",
        ok: false,
        kind: "playMotion",
        code: "motion_failed",
        message: "failed",
      },
    });

    expect(state[0].status).toBe("error");
    expect(state[0].result).toBe("failed");
  });

  it("records local send errors", () => {
    const state = debugLogReducer([entry("r1")], {
      type: "local-error",
      requestId: "r1",
      message: "emit failed",
    });

    expect(state[0].status).toBe("error");
    expect(state[0].result).toBe("emit failed");
  });
});

describe("describeCommand", () => {
  it("keeps motion commands readable for logs", () => {
    expect(
      describeCommand({
        kind: "playMotion",
        requestId: "r1",
        modelId: "m",
        group: "Idle",
        index: 4,
        priority: "force",
      }),
    ).toEqual({
      title: "播放 Idle[4]",
      detail: "priority=force",
    });
  });
});
