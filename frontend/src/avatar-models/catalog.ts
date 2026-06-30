import type { PetPhase } from "@/stores/petState";

export const DEFAULT_AVATAR_MODEL_ID = "builtin:live2d:hiyori";

export type AvatarModelBackend = "live2d" | "spine";
export type AvatarModelSource = "builtin" | "user" | "dev-fixture";
export type AvatarModelDistribution =
  | "redistributable"
  | "user-local-only"
  | "non-commercial-exploration";
export type SpineRuntimeMode = "spine43-pixi8" | "spine38-pixi7" | "unknown";
export type SpineResourceFormat = "skel" | "json";

export type AvatarRuntimePhase = "idle" | "thinking" | "speaking" | "error" | "sleep";
export type AvatarModelAction = AvatarRuntimePhase | "tap" | "dragStart" | "dragEnd";

export interface AvatarActionMapping {
  animation: string;
  loop: boolean;
  returnToPhase?: boolean;
}

export interface AvatarModelCapabilities {
  motions: boolean;
  alphaHitTest: boolean;
  mouthParams: boolean;
  gazeParams: boolean;
  expressionParams: boolean;
}

export interface AvatarModelBase {
  id: string;
  name: string;
  backend: AvatarModelBackend;
  source: AvatarModelSource;
  distribution: AvatarModelDistribution;
  enabled: boolean;
  licenseName?: string;
  noticePath?: string;
}

export interface Live2DAvatarModelEntry extends AvatarModelBase {
  backend: "live2d";
  resources: {
    model3: string;
  };
  live2d: {
    modelName: string;
    motionGroups: Record<PetPhase, string | null> & { tap?: string | null };
    motionNo: Record<PetPhase, number> & { tap?: number };
    spriteWidth: number;
  };
  capabilities: AvatarModelCapabilities;
}

export interface SpineAvatarModelEntry extends AvatarModelBase {
  backend: "spine";
  resources: {
    skeleton: string;
    atlas: string;
    textures?: string[];
  };
  spine: {
    format: SpineResourceFormat;
    runtime: SpineRuntimeMode;
    defaultAnimation?: string;
    actionMapping?: Partial<Record<AvatarModelAction, AvatarActionMapping>>;
  };
  capabilities: AvatarModelCapabilities;
}

export type AvatarModelCatalogEntry = Live2DAvatarModelEntry | SpineAvatarModelEntry;

export interface ResolvedLive2DConfig {
  modelName: string;
  modelPath: string;
  motionGroups: Record<PetPhase, string | null> & { tap?: string | null };
  motionNo: Record<PetPhase, number> & { tap?: number };
  spriteWidth: number;
}

export const HIYORI_LIVE2D_MODEL = {
  id: DEFAULT_AVATAR_MODEL_ID,
  name: "Hiyori",
  backend: "live2d",
  source: "builtin",
  distribution: "redistributable",
  enabled: true,
  licenseName: "Live2D sample model license",
  noticePath: "/live2d-models/hiyori/LICENSE.md",
  resources: {
    model3: "/live2d-models/hiyori/Hiyori.model3.json",
  },
  live2d: {
    modelName: "hiyori",
    motionGroups: {
      idle: "IdleLoop",
      thinking: null,
      speaking: null,
      error: null,
      tap: "Idle",
    },
    motionNo: {
      idle: 0,
      thinking: 0,
      speaking: 0,
      error: 0,
      tap: 4,
    },
    spriteWidth: 320,
  },
  capabilities: {
    motions: true,
    alphaHitTest: true,
    mouthParams: true,
    gazeParams: true,
    expressionParams: true,
  },
} satisfies Live2DAvatarModelEntry;

export const ARK_AMIYA_CAPABILITIES = {
  motions: true,
  alphaHitTest: true,
  mouthParams: false,
  gazeParams: false,
  expressionParams: false,
} satisfies AvatarModelCapabilities;

export const ARK_AMIYA_ACTION_MAPPING = {
  idle: { animation: "Default", loop: true },
  thinking: { animation: "Sit", loop: true },
  speaking: { animation: "Relax", loop: true },
  error: { animation: "Sit", loop: true },
  sleep: { animation: "Sleep", loop: true },
  tap: { animation: "Interact", loop: false, returnToPhase: true },
  dragStart: { animation: "Move", loop: true },
  dragEnd: { animation: "Relax", loop: false, returnToPhase: true },
} satisfies Record<AvatarModelAction, AvatarActionMapping>;

export const ARK_AMIYA_SPINE_FIXTURE_MODEL = {
  id: "dev-fixture:spine:ark-amiya",
  name: "Ark Amiya",
  backend: "spine",
  source: "dev-fixture",
  distribution: "non-commercial-exploration",
  enabled: true,
  licenseName: "Arknights / Ark-Models non-commercial exploration",
  noticePath: "/spine-spike-fixtures/ark-amiya/NOTICE.md",
  resources: {
    skeleton: "/spine-spike-fixtures/ark-amiya/build_char_002_amiya.skel",
    atlas: "/spine-spike-fixtures/ark-amiya/build_char_002_amiya.atlas",
    textures: ["/spine-spike-fixtures/ark-amiya/build_char_002_amiya.png"],
  },
  spine: {
    format: "skel",
    runtime: "spine38-pixi7",
    defaultAnimation: "Default",
    actionMapping: ARK_AMIYA_ACTION_MAPPING,
  },
  capabilities: ARK_AMIYA_CAPABILITIES,
} satisfies SpineAvatarModelEntry;

export const AVATAR_MODEL_CATALOG: readonly AvatarModelCatalogEntry[] = [
  HIYORI_LIVE2D_MODEL,
  ARK_AMIYA_SPINE_FIXTURE_MODEL,
];

export interface ResolveAvatarModelOptions {
  allowedBackends?: readonly AvatarModelBackend[];
  includeDevFixtures?: boolean;
}

function isSelectableEntry(
  entry: AvatarModelCatalogEntry,
  options: ResolveAvatarModelOptions,
): boolean {
  if (!entry.enabled) return false;
  if (options.allowedBackends && !options.allowedBackends.includes(entry.backend)) return false;
  if (entry.source === "dev-fixture" && !options.includeDevFixtures) return false;
  return true;
}

export function findAvatarModelEntry(id: string): AvatarModelCatalogEntry | null {
  return AVATAR_MODEL_CATALOG.find((entry) => entry.id === id) ?? null;
}

export function resolveAvatarModelSelection(
  id: string,
  options: ResolveAvatarModelOptions = {},
): AvatarModelCatalogEntry {
  const requested = findAvatarModelEntry(id);
  if (requested && isSelectableEntry(requested, options)) return requested;

  const fallback = findAvatarModelEntry(DEFAULT_AVATAR_MODEL_ID);
  if (!fallback || fallback.backend !== "live2d") {
    throw new Error(`Missing default avatar model: ${DEFAULT_AVATAR_MODEL_ID}`);
  }
  return fallback;
}

export function getDefaultLive2DConfig(): ResolvedLive2DConfig {
  const entry = resolveAvatarModelSelection(DEFAULT_AVATAR_MODEL_ID, {
    allowedBackends: ["live2d"],
  });
  if (entry.backend !== "live2d") {
    throw new Error(`Default avatar model is not Live2D: ${entry.id}`);
  }

  return {
    modelName: entry.live2d.modelName,
    modelPath: entry.resources.model3,
    motionGroups: entry.live2d.motionGroups,
    motionNo: entry.live2d.motionNo,
    spriteWidth: entry.live2d.spriteWidth,
  };
}
