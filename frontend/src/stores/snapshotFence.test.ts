import { describe, expect, it } from "vitest";
import {
  canReconcileSnapshotAbsence,
  emptySnapshotFence,
  pruneSnapshotFence,
  touchSnapshotKey,
} from "./snapshotFence";

describe("snapshot request fence", () => {
  it("allows an unchanged missing key to be reconciled", () => {
    const fence = touchSnapshotKey(emptySnapshotFence(), "old");
    const requestStart = fence.mutationEpoch;

    expect(canReconcileSnapshotAbsence(fence, "old", requestStart)).toBe(true);
  });

  it("protects a key touched by push after the request started", () => {
    const initial = touchSnapshotKey(emptySnapshotFence(), "run-a");
    const requestStart = initial.mutationEpoch;
    const afterPush = touchSnapshotKey(initial, "run-a");

    expect(canReconcileSnapshotAbsence(afterPush, "run-a", requestStart)).toBe(false);
  });

  it("does not let an unrelated push protect a stale missing key", () => {
    const initial = touchSnapshotKey(emptySnapshotFence(), "run-a");
    const requestStart = initial.mutationEpoch;
    const afterOtherPush = touchSnapshotKey(initial, "run-b");

    expect(canReconcileSnapshotAbsence(afterOtherPush, "run-a", requestStart)).toBe(true);
  });

  it("drops fence history for evicted objects", () => {
    const first = touchSnapshotKey(emptySnapshotFence(), "keep");
    const second = touchSnapshotKey(first, "drop");

    expect(pruneSnapshotFence(second, new Set(["keep"])).touchedAt).toEqual({ keep: 1 });
  });
});
