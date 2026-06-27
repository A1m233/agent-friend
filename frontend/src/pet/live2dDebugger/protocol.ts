export const LIVE2D_DEBUGGER_WINDOW_LABEL = "live2d-debugger";
export const PET_WINDOW_LABEL = "pet";

export const LIVE2D_DEBUGGER_COMMAND_EVENT = "live2d-debugger://command";
export const LIVE2D_DEBUGGER_RESPONSE_EVENT = "live2d-debugger://response";

export interface Live2DModelRef {
  /** Stable model identifier. Current MVP derives it from modelName + modelPath. */
  modelId: string;
  modelName: string;
  modelPath: string;
}

export interface Live2DMotionEntry {
  group: string;
  index: number;
  file: string;
}

export interface Live2DMotionGroup {
  name: string;
  motions: Live2DMotionEntry[];
}

export interface Live2DMotionCatalog {
  model: Live2DModelRef;
  groups: Live2DMotionGroup[];
  defaults: {
    idleGroup: string | null;
    idleIndex: number;
    tapGroup: string | null;
    tapIndex: number;
  };
}

export type Live2DDebugPriority = "idle" | "normal" | "force";

export type Live2DDebugCommand =
  | { kind: "queryCatalog"; requestId: string }
  | {
      kind: "playMotion";
      requestId: string;
      modelId: string;
      group: string;
      index: number;
      priority: Live2DDebugPriority;
    }
  | { kind: "triggerTapFeedback"; requestId: string; modelId: string }
  | { kind: "triggerTapParamsOnly"; requestId: string; modelId: string }
  | { kind: "playIdle"; requestId: string; modelId: string }
  | { kind: "playRandomIdle"; requestId: string; modelId: string };

export type Live2DDebugErrorCode =
  | "sprite_not_ready"
  | "model_mismatch"
  | "group_not_found"
  | "motion_not_found"
  | "motion_failed"
  | "catalog_failed"
  | "skipped_by_phase";

export type Live2DDebugResponse =
  | {
      requestId: string;
      ok: true;
      kind: Live2DDebugCommand["kind"];
      catalog?: Live2DMotionCatalog;
      message: string;
    }
  | {
      requestId: string;
      ok: false;
      kind: Live2DDebugCommand["kind"];
      code: Live2DDebugErrorCode;
      message: string;
    };

export function makeModelId(modelName: string, modelPath: string): string {
  return `${modelName}:${modelPath}`;
}
