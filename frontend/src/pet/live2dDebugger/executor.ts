import type { Live2DSprite } from "easy-live2d";
import type { PetPhase } from "@/stores/petState";

import {
  findMotion,
  firstAvailableMotion,
} from "./catalog";
import {
  type Live2DDebugCommand,
  type Live2DDebugPriority,
  type Live2DDebugResponse,
  type Live2DMotionCatalog,
  type Live2DMotionEntry,
} from "./protocol";

export interface Live2DDebugExecutorEnv {
  getSprite: () => Live2DSprite | null;
  getSpriteReady: () => boolean;
  getCatalog: () => Promise<Live2DMotionCatalog>;
  getPhase: () => PetPhase;
  actions: {
    triggerTapFeedback: () => "played" | "skipped_by_phase";
    triggerTapParamsOnly: () => "played" | "skipped_by_phase";
  };
  random: () => number;
}

const EASY_LIVE2D_PRIORITY = {
  idle: 1,
  normal: 2,
  force: 3,
} as const;

function priorityToEasyLive2D(priority: Live2DDebugPriority): number {
  switch (priority) {
    case "idle":
      return EASY_LIVE2D_PRIORITY.idle;
    case "normal":
      return EASY_LIVE2D_PRIORITY.normal;
    case "force":
      return EASY_LIVE2D_PRIORITY.force;
  }
}

function ok(
  command: Live2DDebugCommand,
  message: string,
  catalog?: Live2DMotionCatalog,
): Live2DDebugResponse {
  return { requestId: command.requestId, ok: true, kind: command.kind, message, catalog };
}

function fail(
  command: Live2DDebugCommand,
  code: Exclude<Live2DDebugResponse, { ok: true }>["code"],
  message: string,
): Live2DDebugResponse {
  return { requestId: command.requestId, ok: false, kind: command.kind, code, message };
}

async function safeCatalog(
  command: Live2DDebugCommand,
  env: Live2DDebugExecutorEnv,
): Promise<Live2DMotionCatalog | Live2DDebugResponse> {
  try {
    return await env.getCatalog();
  } catch (error) {
    return fail(command, "catalog_failed", error instanceof Error ? error.message : String(error));
  }
}

function validateModel(
  command: Extract<Live2DDebugCommand, { modelId: string }>,
  catalog: Live2DMotionCatalog,
): Live2DDebugResponse | null {
  if (command.modelId !== catalog.model.modelId) {
    return fail(
      command,
      "model_mismatch",
      `Command targets ${command.modelId}, current model is ${catalog.model.modelId}`,
    );
  }
  return null;
}

function requireSprite(
  command: Live2DDebugCommand,
  env: Live2DDebugExecutorEnv,
): Live2DSprite | Live2DDebugResponse {
  const sprite = env.getSprite();
  if (!env.getSpriteReady() || !sprite) {
    return fail(command, "sprite_not_ready", "Live2D sprite is not ready");
  }
  return sprite;
}

async function playMotion(
  command: Live2DDebugCommand,
  env: Live2DDebugExecutorEnv,
  motion: Live2DMotionEntry,
  priority: Live2DDebugPriority,
): Promise<Live2DDebugResponse> {
  const sprite = requireSprite(command, env);
  if ("ok" in sprite) return sprite;

  try {
    await sprite.startMotion({
      group: motion.group,
      no: motion.index,
      priority: priorityToEasyLive2D(priority),
    });
    return ok(command, `Played ${motion.group}[${motion.index}]`);
  } catch (error) {
    return fail(command, "motion_failed", error instanceof Error ? error.message : String(error));
  }
}

export async function executeLive2DDebugCommand(
  command: Live2DDebugCommand,
  env: Live2DDebugExecutorEnv,
): Promise<Live2DDebugResponse> {
  if (command.kind === "queryCatalog") {
    const catalog = await safeCatalog(command, env);
    if ("ok" in catalog) return catalog;
    return ok(command, `Loaded catalog for ${catalog.model.modelName}`, catalog);
  }

  const catalog = await safeCatalog(command, env);
  if ("ok" in catalog) return catalog;

  const modelError = validateModel(command, catalog);
  if (modelError) return modelError;

  if (
    command.kind === "triggerTapFeedback" ||
    command.kind === "triggerTapParamsOnly" ||
    command.kind === "playMotion" ||
    command.kind === "playIdle" ||
    command.kind === "playRandomIdle"
  ) {
    const sprite = requireSprite(command, env);
    if ("ok" in sprite) return sprite;
  }

  if (command.kind === "triggerTapFeedback") {
    const result = env.actions.triggerTapFeedback();
    if (result === "skipped_by_phase") {
      return fail(command, "skipped_by_phase", `Skipped in ${env.getPhase()} phase`);
    }
    return ok(command, "Triggered tap feedback");
  }

  if (command.kind === "triggerTapParamsOnly") {
    const result = env.actions.triggerTapParamsOnly();
    if (result === "skipped_by_phase") {
      return fail(command, "skipped_by_phase", `Skipped in ${env.getPhase()} phase`);
    }
    return ok(command, "Triggered tap parameter feedback");
  }

  if (command.kind === "playMotion") {
    const group = catalog.groups.find((item) => item.name === command.group);
    if (!group) return fail(command, "group_not_found", `Motion group not found: ${command.group}`);

    const motion = findMotion(catalog, command.group, command.index);
    if (!motion) {
      return fail(command, "motion_not_found", `Motion not found: ${command.group}[${command.index}]`);
    }
    return playMotion(command, env, motion, command.priority);
  }

  if (command.kind === "playIdle") {
    if (!catalog.defaults.idleGroup) {
      return fail(command, "group_not_found", "Idle group is not configured");
    }
    const motion = findMotion(catalog, catalog.defaults.idleGroup, catalog.defaults.idleIndex);
    if (!motion) {
      return fail(
        command,
        "motion_not_found",
        `Idle motion not found: ${catalog.defaults.idleGroup}[${catalog.defaults.idleIndex}]`,
      );
    }
    return playMotion(command, env, motion, "force");
  }

  const group =
    catalog.groups.find((item) => item.name === catalog.defaults.idleGroup && item.motions.length > 0) ??
    catalog.groups.find((item) => item.motions.length > 0);
  if (!group) return fail(command, "group_not_found", "No motion groups are available");
  const index = Math.min(group.motions.length - 1, Math.floor(env.random() * group.motions.length));
  const motion = group.motions[index] ?? firstAvailableMotion(catalog, catalog.defaults.idleGroup);
  if (!motion) return fail(command, "motion_not_found", "No motions are available");
  return playMotion(command, env, motion, "force");
}
