import type { PixelRegion } from "@/pet/petAlphaHitTest";
import {
  ARK_AMIYA_ACTION_MAPPING,
  ARK_AMIYA_CAPABILITIES,
} from "@/avatar-models";
import {
  chooseInitialAnimation as chooseRuntimeInitialAnimation,
  isAvatarPhaseAction,
  resolveAnimationName as resolveRuntimeAnimationName,
} from "@/pet/avatarRuntime/actionMapping";

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

export const ARK_SPINE_CAPABILITIES: PetAvatarCapabilities = ARK_AMIYA_CAPABILITIES;

export const ARK_ACTION_MAPPING: Record<PetAvatarAction, PetActionMapping> =
  ARK_AMIYA_ACTION_MAPPING;

export function chooseInitialAnimation(animations: string[], preferred: string): string {
  return chooseRuntimeInitialAnimation(animations, preferred)?.animation ?? "";
}

export function resolveAnimationName(animations: string[], preferred: string, fallback: string): string {
  return resolveRuntimeAnimationName(animations, preferred, fallback)?.animation ?? "";
}

export function isPhaseAction(action: PetAvatarAction): action is PetRuntimePhase {
  return isAvatarPhaseAction(action);
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
