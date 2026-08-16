import type { PixelRegion } from "@/pet/petAlphaHitTest";

export const SPINE_VISIBLE_BOUNDS_SETTLE_MS = 1_500;
export const SPINE_VISIBLE_BOUNDS_REQUIRED_SAMPLES = 3;

const DEFAULT_SIZE_TOLERANCE = 0.08;

export interface SpineVisibleBoundsStabilityState {
  accepted: PixelRegion | null;
  candidate: PixelRegion | null;
  candidateCount: number;
  scanAfterMs: number;
}

export function createSpineVisibleBoundsStabilityState(
  nowMs: number,
): SpineVisibleBoundsStabilityState {
  return {
    accepted: null,
    candidate: null,
    candidateCount: 0,
    scanAfterMs: nowMs + SPINE_VISIBLE_BOUNDS_SETTLE_MS,
  };
}

/**
 * Display recovery can emit several transient resize/DPR snapshots. Before the first trusted
 * scan, restart the quiet period. Once model-local bounds are accepted, preserve them: changing
 * output density does not change the model's local geometry.
 */
export function resetPendingSpineVisibleBoundsForDisplayChange(
  state: SpineVisibleBoundsStabilityState,
  nowMs: number,
): SpineVisibleBoundsStabilityState {
  if (state.accepted) return state;
  return createSpineVisibleBoundsStabilityState(nowMs);
}

export function canSampleSpineVisibleBounds(
  state: SpineVisibleBoundsStabilityState,
  nowMs: number,
): boolean {
  return state.accepted === null && nowMs >= state.scanAfterMs;
}

function relativeDelta(left: number, right: number): number {
  return Math.abs(left - right) / Math.max(Math.abs(left), Math.abs(right), 1);
}

function hasStableSize(
  previous: PixelRegion,
  next: PixelRegion,
  tolerance: number,
): boolean {
  return (
    relativeDelta(previous.w, next.w) <= tolerance &&
    relativeDelta(previous.h, next.h) <= tolerance
  );
}

export function observeSpineVisibleBoundsSample(
  state: SpineVisibleBoundsStabilityState,
  sample: PixelRegion,
  nowMs: number,
  options: {
    requiredSamples?: number;
    sizeTolerance?: number;
  } = {},
): SpineVisibleBoundsStabilityState {
  if (state.accepted || nowMs < state.scanAfterMs) return state;

  const requiredSamples = Math.max(
    1,
    Math.floor(options.requiredSamples ?? SPINE_VISIBLE_BOUNDS_REQUIRED_SAMPLES),
  );
  const sizeTolerance = Math.max(0, options.sizeTolerance ?? DEFAULT_SIZE_TOLERANCE);
  const candidateCount =
    state.candidate && hasStableSize(state.candidate, sample, sizeTolerance)
      ? state.candidateCount + 1
      : 1;

  if (candidateCount >= requiredSamples) {
    return {
      accepted: sample,
      candidate: sample,
      candidateCount,
      scanAfterMs: state.scanAfterMs,
    };
  }

  return {
    ...state,
    candidate: sample,
    candidateCount,
  };
}

export function discardPendingSpineVisibleBoundsSample(
  state: SpineVisibleBoundsStabilityState,
): SpineVisibleBoundsStabilityState {
  if (state.accepted || state.candidate === null) return state;
  return {
    ...state,
    candidate: null,
    candidateCount: 0,
  };
}
