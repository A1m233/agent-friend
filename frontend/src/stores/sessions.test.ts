import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services", () => ({
  sessionsApi: { list: vi.fn() },
}));

import { sessionsApi } from "@/services";
import type { SessionSummary } from "@/types/meta";
import { useSessionsStore } from "./sessions";

const mockList = vi.mocked(sessionsApi.list);

function summary(sessionId: string): SessionSummary {
  return {
    session_id: sessionId,
    title: sessionId,
    created_at: "2026-06-26T00:00:00Z",
    updated_at: "2026-06-26T00:00:00Z",
    persona: "default",
    model: "test",
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useSessionsStore.setState({ list: [], loading: false, loaded: false });
});

describe("sessions store", () => {
  it("marks the list as loaded only after a successful refresh", async () => {
    mockList.mockResolvedValue([summary("sid-1")]);

    await useSessionsStore.getState().refresh();

    expect(useSessionsStore.getState().list.map((s) => s.session_id)).toEqual(["sid-1"]);
    expect(useSessionsStore.getState().loaded).toBe(true);
    expect(useSessionsStore.getState().loading).toBe(false);
  });

  it("keeps the previous loaded state when refresh fails", async () => {
    useSessionsStore.setState({ list: [summary("sid-old")], loading: false, loaded: true });
    mockList.mockRejectedValue(new Error("offline"));

    await useSessionsStore.getState().refresh();

    expect(useSessionsStore.getState().list.map((s) => s.session_id)).toEqual(["sid-old"]);
    expect(useSessionsStore.getState().loaded).toBe(true);
    expect(useSessionsStore.getState().loading).toBe(false);
  });
});
