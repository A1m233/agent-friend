import { afterEach, describe, expect, it, vi } from "vitest";
import {
  __resetPermissionSyncForTests,
  acceptPermissionSnapshot,
  getPermissionSnapshot,
  hasPermissions,
  loadInitialPermissionSnapshot,
  subscribePermissionSnapshot,
  type PermissionSnapshot,
} from "./index";

function snapshot(revision: number, state: "granted" | "denied"): PermissionSnapshot {
  return {
    snapshotRevision: revision,
    refreshing: false,
    activeFlow: null,
    items: [
      {
        id: "microphone",
        definitionRevision: 1,
        systemState: state,
        presentationState: state,
        recoveryAction: state === "granted" ? "none" : "retry_request",
        handledForCurrentRevision: false,
        deferredForCurrentRevision: false,
        changedAtMs: 1,
      },
    ],
  };
}

afterEach(__resetPermissionSyncForTests);

describe("permission snapshot facade", () => {
  it("ignores stale snapshots after listener-first initialization races", () => {
    expect(acceptPermissionSnapshot(snapshot(3, "granted"))).toBe(true);
    expect(acceptPermissionSnapshot(snapshot(2, "denied"))).toBe(false);
    expect(getPermissionSnapshot().snapshotRevision).toBe(3);
    expect(hasPermissions(["microphone"])).toBe(true);
  });

  it("notifies subscribers only for newer revisions", () => {
    const subscriber = vi.fn();
    const unsubscribe = subscribePermissionSnapshot(subscriber);
    acceptPermissionSnapshot(snapshot(1, "denied"));
    acceptPermissionSnapshot(snapshot(1, "granted"));
    expect(subscriber).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("retries the initial snapshot while Tauri managed state is starting", async () => {
    const loader = vi
      .fn<() => Promise<PermissionSnapshot>>()
      .mockRejectedValueOnce(new Error("state not managed for field `state`"))
      .mockResolvedValueOnce(snapshot(1, "granted"));
    const wait = vi.fn(() => Promise.resolve());

    await expect(loadInitialPermissionSnapshot(loader, wait)).resolves.toEqual(
      snapshot(1, "granted"),
    );
    expect(loader).toHaveBeenCalledTimes(2);
    expect(wait).toHaveBeenCalledWith(50);
  });

  it("does not hide unrelated initialization failures", async () => {
    const failure = new Error("invoke transport failed");
    const loader = vi.fn<() => Promise<PermissionSnapshot>>().mockRejectedValue(failure);

    await expect(loadInitialPermissionSnapshot(loader)).rejects.toBe(failure);
    expect(loader).toHaveBeenCalledTimes(1);
  });
});
