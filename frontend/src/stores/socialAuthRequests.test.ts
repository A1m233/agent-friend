import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { socialAuthApi, type SocialAuthRequestSnapshot } from "@/services";
import { useSocialAuthRequestsStore } from "./socialAuthRequests";

function snapshot(
  requestId: string,
  status: SocialAuthRequestSnapshot["status"],
): SocialAuthRequestSnapshot {
  return {
    requestId,
    sessionId: "session-a",
    platform: "bilibili",
    missingCapabilities: [],
    reason: "需要登录",
    originSurface: "desktop_chat",
    status,
    createdAt: "2026-09-05T00:00:00Z",
    expiresAt: "2026-09-05T00:10:00Z",
    continuationRunId: null,
    errorKind: null,
    challengeKind: null,
  };
}

async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

describe("socialAuthRequests store", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useSocialAuthRequestsStore.getState().clear();
  });

  afterEach(() => {
    useSocialAuthRequestsStore.getState().clear();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("keeps one polling chain per tracked request and stops at terminal", async () => {
    const get = vi.spyOn(socialAuthApi, "get")
      .mockResolvedValueOnce(snapshot("request-a", "pending"))
      .mockResolvedValueOnce(snapshot("request-a", "completed"));

    useSocialAuthRequestsStore.getState().setTrackedRequestIds(["request-a", "request-a"]);
    await settle();
    expect(get).toHaveBeenCalledTimes(1);
    expect(useSocialAuthRequestsStore.getState().trackedRequestIds).toEqual(["request-a"]);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(get).toHaveBeenCalledTimes(2);
    expect(useSocialAuthRequestsStore.getState().entries["request-a"].snapshot?.status)
      .toBe("completed");

    await vi.advanceTimersByTimeAsync(5_000);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("stops the old request when the visible session switches", async () => {
    const get = vi.spyOn(socialAuthApi, "get").mockImplementation(async (requestId) => (
      snapshot(requestId, "pending")
    ));

    useSocialAuthRequestsStore.getState().setTrackedRequestIds(["request-a"]);
    await settle();
    useSocialAuthRequestsStore.getState().setTrackedRequestIds(["request-b"]);
    await settle();
    expect(Object.keys(useSocialAuthRequestsStore.getState().entries)).toEqual(["request-b"]);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(get.mock.calls.filter(([requestId]) => requestId === "request-a")).toHaveLength(1);
    expect(get.mock.calls.filter(([requestId]) => requestId === "request-b")).toHaveLength(2);
  });

  it("writes action responses into the same authoritative entry", async () => {
    vi.spyOn(socialAuthApi, "get").mockResolvedValue(snapshot("request-a", "pending"));
    const approve = vi.spyOn(socialAuthApi, "approve")
      .mockResolvedValue(snapshot("request-a", "login_in_progress"));

    useSocialAuthRequestsStore.getState().setTrackedRequestIds(["request-a"]);
    await settle();
    await useSocialAuthRequestsStore.getState().perform("request-a", "approve");

    expect(approve).toHaveBeenCalledWith("request-a");
    expect(useSocialAuthRequestsStore.getState().entries["request-a"]).toMatchObject({
      submitting: false,
      error: null,
      snapshot: { status: "login_in_progress" },
    });
  });

  it("does not let an older refresh overwrite a newer action response", async () => {
    let resolveRefresh: ((value: SocialAuthRequestSnapshot) => void) | undefined;
    vi.spyOn(socialAuthApi, "get").mockImplementation(() => new Promise((resolve) => {
      resolveRefresh = resolve;
    }));
    vi.spyOn(socialAuthApi, "approve")
      .mockResolvedValue(snapshot("request-a", "authenticated"));

    useSocialAuthRequestsStore.getState().setTrackedRequestIds(["request-a"]);
    await settle();
    await useSocialAuthRequestsStore.getState().perform("request-a", "approve");
    resolveRefresh?.(snapshot("request-a", "pending"));
    await settle();

    expect(useSocialAuthRequestsStore.getState().entries["request-a"].snapshot?.status)
      .toBe("authenticated");
  });

  it.each(["switch", "clear"])("fences old refreshes across a %s and return to the same request", async (mode) => {
    let resolveOld!: (value: SocialAuthRequestSnapshot) => void;
    const get = vi.spyOn(socialAuthApi, "get")
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValue(snapshot("request-a", "completed"));
    const store = useSocialAuthRequestsStore;
    store.getState().setTrackedRequestIds(["request-a"]);
    if (mode === "clear") store.getState().clear();
    else store.getState().setTrackedRequestIds([]);
    store.getState().setTrackedRequestIds(["request-a"]);
    await settle();
    resolveOld(snapshot("request-a", "pending"));
    await settle();
    expect(store.getState().entries["request-a"].snapshot?.status).toBe("completed");
    await vi.advanceTimersByTimeAsync(5_000);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("does not apply an action from an earlier visible-request lifetime", async () => {
    let resolveOld!: (value: SocialAuthRequestSnapshot) => void;
    vi.spyOn(socialAuthApi, "get")
      .mockResolvedValueOnce(snapshot("request-a", "pending"))
      .mockResolvedValue(snapshot("request-a", "completed"));
    vi.spyOn(socialAuthApi, "approve")
      .mockImplementation(() => new Promise((resolve) => { resolveOld = resolve; }));
    const store = useSocialAuthRequestsStore;
    store.getState().setTrackedRequestIds(["request-a"]);
    await settle();
    const action = store.getState().perform("request-a", "approve");
    store.getState().setTrackedRequestIds([]);
    store.getState().setTrackedRequestIds(["request-a"]);
    await settle();
    resolveOld(snapshot("request-a", "login_in_progress"));
    await action;
    expect(store.getState().entries["request-a"].snapshot?.status).toBe("completed");
  });
});
