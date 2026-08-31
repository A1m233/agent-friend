import { Vector2 } from "pixi-spine";
import type { Spine as Spine38 } from "pixi-spine";
import type {
  AvatarModelAction,
  SpineAvatarModelEntry,
} from "@/avatar-models";
import {
  resolveAnimationName,
  resolveSpineActionMapping,
} from "@/pet/avatarRuntime/actionMapping";
import type { PixelRegion } from "@/pet/petAlphaHitTest";

const DEFAULT_SAMPLE_STEP_SECONDS = 0.05;
const CANONICAL_LAYOUT_ACTIONS: AvatarModelAction[] = ["idle"];

interface Spine38AnimationLike {
  name: string;
  duration: number;
}

function finitePositive(value: number): boolean {
  return Number.isFinite(value) && value > 0;
}

function unionBounds(current: PixelRegion | null, next: PixelRegion): PixelRegion {
  if (!current) return next;
  const minX = Math.min(current.x, next.x);
  const minY = Math.min(current.y, next.y);
  const maxX = Math.max(current.x + current.w, next.x + next.w);
  const maxY = Math.max(current.y + current.h, next.y + next.h);
  return {
    x: minX,
    y: minY,
    w: maxX - minX,
    h: maxY - minY,
  };
}

function readSkeletonBounds(spine: Spine38): PixelRegion | null {
  const offset = new Vector2();
  const size = new Vector2();
  spine.skeleton.updateWorldTransform();
  spine.skeleton.getBounds(offset, size, []);
  if (!finitePositive(size.x) || !finitePositive(size.y)) return null;
  return { x: offset.x, y: offset.y, w: size.x, h: size.y };
}

function resolveSampleTimes(duration: number, stepSeconds: number): number[] {
  if (!finitePositive(duration)) return [0];
  const safeStep = finitePositive(stepSeconds) ? stepSeconds : DEFAULT_SAMPLE_STEP_SECONDS;
  const count = Math.max(2, Math.ceil(duration / safeStep) + 1);
  return Array.from({ length: count }, (_, index) =>
    index === count - 1 ? duration : (duration * index) / (count - 1),
  );
}

export function resolveSpine38CanonicalLayoutAnimationNames(
  entry: SpineAvatarModelEntry,
  animations: readonly string[],
): string[] {
  const names = new Set<string>();

  for (const action of CANONICAL_LAYOUT_ACTIONS) {
    const mapping = resolveSpineActionMapping(entry, action, animations);
    const resolved = resolveAnimationName(
      animations,
      mapping.animation,
      entry.spine.defaultAnimation,
    );
    if (resolved) names.add(resolved.animation);
  }

  const fallback = resolveAnimationName(
    animations,
    entry.spine.defaultAnimation,
    entry.spine.defaultAnimation,
  );
  if (fallback) names.add(fallback.animation);

  return [...names];
}

export function sampleSpine38AnimationBounds(
  spine: Spine38,
  animationNames: readonly string[],
  sampleStepSeconds = DEFAULT_SAMPLE_STEP_SECONDS,
): PixelRegion | null {
  const animations = spine.skeleton.data.animations as Spine38AnimationLike[];
  const targets = animationNames
    .map((name) => animations.find((animation) => animation.name === name))
    .filter((animation): animation is Spine38AnimationLike => Boolean(animation));
  if (targets.length === 0) return readSkeletonBounds(spine);

  let sampled: PixelRegion | null = null;

  for (const animation of targets) {
    for (const time of resolveSampleTimes(animation.duration, sampleStepSeconds)) {
      const track = spine.state.setAnimation(0, animation.name, false);
      track.mixDuration = 0;
      track.trackTime = time;
      track.trackLast = time;
      spine.skeleton.setToSetupPose();
      spine.state.apply(spine.skeleton);
      const bounds = readSkeletonBounds(spine);
      if (bounds) sampled = unionBounds(sampled, bounds);
      spine.state.clearTracks();
    }
  }

  spine.skeleton.setToSetupPose();
  spine.skeleton.updateWorldTransform();
  return sampled;
}

export function sampleSpine38CanonicalLayoutBounds(
  spine: Spine38,
  entry: SpineAvatarModelEntry,
  animations: readonly string[],
): PixelRegion | null {
  return sampleSpine38AnimationBounds(
    spine,
    resolveSpine38CanonicalLayoutAnimationNames(entry, animations),
  );
}
