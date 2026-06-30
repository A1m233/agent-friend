import type {
  AvatarModelAction,
  AvatarModelBackend,
  AvatarModelCapabilities,
  AvatarRuntimePhase,
} from "@/avatar-models";

export type PetAvatarRuntimeKind = AvatarModelBackend;
export type PetAvatarRuntimePhase = AvatarRuntimePhase;
export type PetAvatarRuntimeAction = AvatarModelAction;

export type AvatarActionResult =
  | { status: "played"; animation?: string; fallbackReason?: string }
  | { status: "skipped"; reason: string };

export interface PetAvatarRuntime {
  kind: PetAvatarRuntimeKind;
  modelId: string;
  capabilities: AvatarModelCapabilities;
  ready: boolean;
  setPhase: (phase: PetAvatarRuntimePhase) => AvatarActionResult;
  playAction: (action: PetAvatarRuntimeAction) => AvatarActionResult;
  alphaHitTest: (cursor: { x: number; y: number }) => boolean;
}

export interface PetAvatarRuntimeHandle {
  runtime: PetAvatarRuntime | null;
}
