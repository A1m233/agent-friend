import { PET_LIVE2D_CONFIG, type PetLive2DConfig } from "@/pet/live2dConfig";

import {
  makeModelId,
  type Live2DMotionCatalog,
  type Live2DMotionEntry,
  type Live2DMotionGroup,
} from "./protocol";

interface Model3MotionItem {
  File?: unknown;
}

interface Model3Json {
  FileReferences?: {
    Motions?: Record<string, Model3MotionItem[]>;
  };
}

function isModel3Json(value: unknown): value is Model3Json {
  return typeof value === "object" && value !== null;
}

function readMotionGroups(model3: unknown): Live2DMotionGroup[] {
  if (!isModel3Json(model3)) return [];
  const motions = model3.FileReferences?.Motions;
  if (!motions || typeof motions !== "object") return [];

  return Object.entries(motions)
    .filter(([, items]) => Array.isArray(items))
    .map(([group, items]) => ({
      name: group,
      motions: items
        .map((item, index): Live2DMotionEntry | null => {
          if (!item || typeof item !== "object") return null;
          const file = (item as Model3MotionItem).File;
          if (typeof file !== "string" || file.length === 0) return null;
          return { group, index, file };
        })
        .filter((entry): entry is Live2DMotionEntry => entry !== null),
    }))
    .filter((group) => group.motions.length > 0);
}

export function buildMotionCatalogFromModel3(
  config: PetLive2DConfig,
  model3: unknown,
): Live2DMotionCatalog {
  return {
    model: {
      modelId: makeModelId(config.modelName, config.modelPath),
      modelName: config.modelName,
      modelPath: config.modelPath,
    },
    groups: readMotionGroups(model3),
    defaults: {
      idleGroup: config.motionGroups.idle,
      idleIndex: config.motionNo.idle,
      tapGroup: config.motionGroups.tap ?? null,
      tapIndex: config.motionNo.tap ?? 0,
    },
  };
}

export async function loadCurrentMotionCatalog(
  config: PetLive2DConfig = PET_LIVE2D_CONFIG,
): Promise<Live2DMotionCatalog> {
  const response = await fetch(config.modelPath);
  if (!response.ok) {
    throw new Error(`Failed to load model3.json: HTTP ${response.status}`);
  }
  const model3 = await response.json();
  return buildMotionCatalogFromModel3(config, model3);
}

export function findMotion(
  catalog: Live2DMotionCatalog,
  groupName: string,
  index: number,
): Live2DMotionEntry | null {
  const group = catalog.groups.find((item) => item.name === groupName);
  return group?.motions.find((motion) => motion.index === index) ?? null;
}

export function firstAvailableMotion(
  catalog: Live2DMotionCatalog,
  preferredGroup: string | null,
): Live2DMotionEntry | null {
  if (preferredGroup) {
    const preferred = catalog.groups.find((group) => group.name === preferredGroup);
    if (preferred?.motions.length) return preferred.motions[0];
  }
  return catalog.groups.find((group) => group.motions.length > 0)?.motions[0] ?? null;
}
