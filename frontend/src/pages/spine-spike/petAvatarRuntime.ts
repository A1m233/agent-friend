import type { PixelRegion } from "@/pet/petAlphaHitTest";

export type RuntimeMode = "spine43-pixi8" | "spine38-pixi7";
export type PetRuntimePhase = "idle" | "thinking" | "speaking" | "error" | "sleep";
export type PetAvatarAction = PetRuntimePhase | "tap" | "dragStart" | "dragEnd";
export type AvatarRuntimeKind = "live2d" | "spine";

export interface PetActionMapping {
  animation: string;
  loop: boolean;
  returnToPhase?: boolean;
}

export interface PetAvatarCapabilities {
  motions: boolean;
  alphaHitTest: boolean;
  mouthParams: boolean;
  gazeParams: boolean;
  expressionParams: boolean;
}

export interface PetAvatarRuntime {
  kind: AvatarRuntimeKind;
  capabilities: PetAvatarCapabilities;
  mount(): Promise<void>;
  destroy(): void;
  setPhase(phase: PetRuntimePhase): void;
  playAction(action: PetAvatarAction): void;
  getBounds(): PixelRegion | null;
}

export const RUNTIME_OPTIONS: Array<{ value: RuntimeMode; label: string }> = [
  { value: "spine43-pixi8", label: "Pixi 8 / Spine 4.3" },
  { value: "spine38-pixi7", label: "Pixi 7 / Spine 3.8" },
];

export const ARK_SPINE_CAPABILITIES = {
  motions: true,
  alphaHitTest: true,
  mouthParams: false,
  gazeParams: false,
  expressionParams: false,
} satisfies PetAvatarCapabilities;

export const ARK_ACTION_MAPPING: Record<PetAvatarAction, PetActionMapping> = {
  idle: { animation: "Default", loop: true },
  thinking: { animation: "Sit", loop: true },
  speaking: { animation: "Relax", loop: true },
  error: { animation: "Sit", loop: true },
  sleep: { animation: "Sleep", loop: true },
  tap: { animation: "Interact", loop: false, returnToPhase: true },
  dragStart: { animation: "Move", loop: true },
  dragEnd: { animation: "Relax", loop: false, returnToPhase: true },
};

export function chooseInitialAnimation(animations: string[], preferred: string): string {
  if (animations.includes(preferred)) return preferred;
  const idle = animations.find((name) => name.toLowerCase().includes("idle"));
  return idle ?? animations[0] ?? "";
}

export function resolveAnimationName(animations: string[], preferred: string, fallback: string): string {
  if (animations.includes(preferred)) return preferred;
  const exactCaseInsensitive = animations.find((name) => name.toLowerCase() === preferred.toLowerCase());
  if (exactCaseInsensitive) return exactCaseInsensitive;
  if (fallback && animations.includes(fallback)) return fallback;
  return chooseInitialAnimation(animations, fallback);
}

export function isPhaseAction(action: PetAvatarAction): action is PetRuntimePhase {
  return action === "idle" || action === "thinking" || action === "speaking" || action === "error" || action === "sleep";
}

export function formatCapabilities(capabilities: PetAvatarCapabilities): string {
  const labels = [
    capabilities.motions ? "motions" : null,
    capabilities.alphaHitTest ? "alpha hit-test" : null,
    capabilities.mouthParams ? "mouth params" : null,
    capabilities.gazeParams ? "gaze params" : null,
    capabilities.expressionParams ? "expression params" : null,
  ].filter((item): item is string => item !== null);

  return labels.length ? labels.join(", ") : "none";
}
