export interface SnapshotFenceState {
  mutationEpoch: number;
  touchedAt: Record<string, number>;
}
export function emptySnapshotFence(): SnapshotFenceState {
  return { mutationEpoch: 0, touchedAt: {} };
}

export function touchSnapshotKey(
  fence: SnapshotFenceState,
  key: string,
): SnapshotFenceState {
  const mutationEpoch = fence.mutationEpoch + 1;
  return {
    mutationEpoch,
    touchedAt: { ...fence.touchedAt, [key]: mutationEpoch },
  };
}

export function canReconcileSnapshotAbsence(
  fence: SnapshotFenceState,
  key: string,
  requestStartEpoch: number,
): boolean {
  return (fence.touchedAt[key] ?? 0) <= requestStartEpoch;
}

export function pruneSnapshotFence(
  fence: SnapshotFenceState,
  retainedKeys: ReadonlySet<string>,
): SnapshotFenceState {
  const touchedAt = Object.fromEntries(
    Object.entries(fence.touchedAt).filter(([key]) => retainedKeys.has(key)),
  );
  return { ...fence, touchedAt };
}
