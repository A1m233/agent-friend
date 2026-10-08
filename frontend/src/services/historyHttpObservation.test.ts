import { describe, expect, it, vi } from "vitest";
const { invoke } = vi.hoisted(() => ({ invoke: vi.fn().mockResolvedValue(undefined) }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("@/utils/tauri", () => ({ isTauri: () => true }));
vi.mock("@/constants", () => ({ BRIDGE_BASE_URL: "http://127.0.0.1:18800" }));
vi.mock("@/services/runtime", () => ({ ensureLocalServiceReady: vi.fn().mockResolvedValue(undefined) }));
import { createHttp } from "./http";
import { startHistoryObservation } from "./historyObservation";

describe("history HTTP timing", () => {
  it("preserves Axios JSON transforms and correlates server request before decode", async () => {
    const read = startHistoryObservation("session-a", "latest");
    const client = createHttp();
    const result = await client.get("/sessions/a/history", {
      headers: { "x-agent-friend-navigation-id": read.navigationId },
      adapter: async (config) => ({
        data: '{"events":[]}', status: 200, statusText: "OK", config,
        headers: {
          "content-type": "application/json", "content-length": "13",
          "x-agent-friend-request-id": "server-123",
        },
      }),
    });
    expect(result).toEqual({ events: [] });
    const rows = invoke.mock.calls.map(([, payload]) => payload.observation);
    expect(rows.map((row) => row.phase)).toEqual([
      "started", "service-wait", "service-ready", "response-received", "decode-finished",
    ]);
    expect(rows.find((row) => row.phase === "response-received")).toMatchObject({
      requestId: "server-123", responseBytes: 13,
    });
    read.finish("success");
  });
});
