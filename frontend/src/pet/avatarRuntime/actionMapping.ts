import type {
  AvatarActionMapping,
  AvatarModelAction,
  AvatarRuntimePhase,
  SpineAvatarModelEntry,
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
): AvatarActionMapping {
  const configured = entry.spine.actionMapping?.[action];
  if (configured) return configured;

  return {
    animation: entry.spine.defaultAnimation ?? "Default",
    loop: isAvatarPhaseAction(action),
    returnToPhase: action === "tap" || action === "dragEnd",
  };
}
