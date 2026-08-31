import {
  ARK_SPINE_ACTION_MAPPING,
  type AvatarActionMapping,
  type AvatarModelAction,
  type AvatarRuntimePhase,
  type SpineAvatarModelEntry,
} from "@/avatar-models";

export interface ResolvedAvatarAnimation {
  animation: string;
  fallbackReason?: string;
}

const PHASE_ACTIONS = new Set<AvatarModelAction>([
  "idle",
  "thinking",
  "speaking",
  "error",
  "sleep",
]);

export function isAvatarPhaseAction(
  action: AvatarModelAction,
): action is AvatarRuntimePhase {
  return PHASE_ACTIONS.has(action);
}

function findAvailableAnimationName(
  animations: readonly string[],
  preferredAnimation: string,
): string | null {
  return (
    animations.find((animation) => animation === preferredAnimation) ??
    animations.find(
      (animation) => animation.toLowerCase() === preferredAnimation.toLowerCase(),
    ) ??
    null
  );
}

export function chooseInitialAnimation(
  animations: readonly string[],
  preferred: string | undefined,
): ResolvedAvatarAnimation | null {
  if (preferred && animations.includes(preferred)) {
    return { animation: preferred };
  }

  if (preferred) {
    const exactCaseInsensitive = animations.find(
      (name) => name.toLowerCase() === preferred.toLowerCase(),
    );
    if (exactCaseInsensitive) {
      return {
        animation: exactCaseInsensitive,
        fallbackReason: `case-insensitive match for ${preferred}`,
      };
    }
  }

  const idleLike = animations.find((name) => {
    const normalized = name.toLowerCase();
    return normalized.includes("idle") || normalized === "default";
  });
  if (idleLike) {
    return {
      animation: idleLike,
      fallbackReason: preferred
        ? `${preferred} missing; using idle-like ${idleLike}`
        : `using idle-like ${idleLike}`,
    };
  }

  const first = animations[0];
  if (first) {
    return {
      animation: first,
      fallbackReason: preferred
        ? `${preferred} missing; using first animation ${first}`
        : `using first animation ${first}`,
    };
  }

  return null;
}

export function resolveAnimationName(
  animations: readonly string[],
  preferred: string | undefined,
  fallback: string | undefined,
): ResolvedAvatarAnimation | null {
  if (preferred && animations.includes(preferred)) {
    return { animation: preferred };
  }

  if (preferred) {
    const exactCaseInsensitive = animations.find(
      (name) => name.toLowerCase() === preferred.toLowerCase(),
    );
    if (exactCaseInsensitive) {
      return {
        animation: exactCaseInsensitive,
        fallbackReason: `case-insensitive match for ${preferred}`,
      };
    }
  }

  if (fallback && animations.includes(fallback)) {
    return {
      animation: fallback,
      fallbackReason: preferred ? `${preferred} missing; using ${fallback}` : undefined,
    };
  }

  return chooseInitialAnimation(animations, fallback ?? preferred);
}

export function resolveSpineActionMapping(
  entry: SpineAvatarModelEntry,
  action: AvatarModelAction,
  animations: readonly string[] = [],
): AvatarActionMapping {
  const configured = entry.spine.actionMapping?.[action];
  if (configured) return configured;

  const conventional = ARK_SPINE_ACTION_MAPPING[action];
  const conventionalAnimation = findAvailableAnimationName(
    animations,
    conventional.animation,
  );
  if (conventionalAnimation) {
    return {
      ...conventional,
      animation: conventionalAnimation,
    };
  }

  return {
    animation: entry.spine.defaultAnimation ?? "Default",
    loop: isAvatarPhaseAction(action),
    returnToPhase: action === "tap" || action === "dragEnd",
  };
}
