import { describe, expect, it, vi } from "vitest";
import type { Live2DSprite } from "easy-live2d";

import { executeLive2DDebugCommand, type Live2DDebugExecutorEnv } from "./executor";
import type { Live2DMotionCatalog } from "./protocol";

const catalog: Live2DMotionCatalog = {
  model: {
    modelId: "hiyori:/live2d-models/hiyori/Hiyori.model3.json",
    modelName: "hiyori",
    modelPath: "/live2d-models/hiyori/Hiyori.model3.json",
  },
  groups: [
    {
      name: "IdleLoop",
      motions: [
        { group: "IdleLoop", index: 0, file: "motions/Hiyori_m01.motion3.json" },
        { group: "IdleLoop", index: 1, file: "motions/Hiyori_m02.motion3.json" },
      ],
    },
    {
      name: "Idle",
      motions: [{ group: "Idle", index: 4, file: "motions/Hiyori_m06.motion3.json" }],
    },
  ],
  defaults: {
    idleGroup: "IdleLoop",
    idleIndex: 0,
    tapGroup: "Idle",
    tapIndex: 4,
  },
};

function makeEnv(overrides: Partial<Live2DDebugExecutorEnv> = {}) {
  const sprite = {
    startMotion: vi.fn().mockResolvedValue(undefined),
  };
  const env: Live2DDebugExecutorEnv = {
    getSprite: () => sprite as unknown as Live2DSprite,
    getSpriteReady: () => true,
    getCatalog: async () => catalog,
    getPhase: () => "idle",
    actions: {
      triggerTapFeedback: vi.fn((): "played" => "played"),
      triggerTapParamsOnly: vi.fn((): "played" => "played"),
    },
    random: () => 0,
    ...overrides,
  };
  return { env, sprite };
}

describe("executeLive2DDebugCommand", () => {
  it("returns catalog without requiring sprite readiness", async () => {
    const { env } = makeEnv({ getSpriteReady: () => false, getSprite: () => null });
    const response = await executeLive2DDebugCommand({ kind: "queryCatalog", requestId: "r1" }, env);

    expect(response.ok).toBe(true);
    if (response.ok) {
      expect(response.catalog?.model.modelName).toBe("hiyori");
    }
  });

  it("plays an explicit motion with mapped priority", async () => {
    const { env, sprite } = makeEnv();
    const response = await executeLive2DDebugCommand(
      {
        kind: "playMotion",
        requestId: "r2",
        modelId: catalog.model.modelId,
        group: "Idle",
        index: 4,
        priority: "force",
      },
      env,
    );

    expect(response.ok).toBe(true);
    expect(sprite.startMotion).toHaveBeenCalledWith({
      group: "Idle",
      no: 4,
      priority: 3,
    });
  });

  it("rejects stale model commands", async () => {
    const { env } = makeEnv();
    const response = await executeLive2DDebugCommand(
      {
        kind: "playMotion",
        requestId: "r3",
        modelId: "old-model",
        group: "Idle",
        index: 4,
        priority: "normal",
      },
      env,
    );

    expect(response.ok).toBe(false);
    if (!response.ok) expect(response.code).toBe("model_mismatch");
  });

  it("reports group and motion lookup errors before touching sprite", async () => {
    const { env, sprite } = makeEnv();
    const missingGroup = await executeLive2DDebugCommand(
      {
        kind: "playMotion",
        requestId: "r4",
        modelId: catalog.model.modelId,
        group: "Missing",
        index: 0,
        priority: "normal",
      },
      env,
    );
    const missingMotion = await executeLive2DDebugCommand(
      {
        kind: "playMotion",
        requestId: "r5",
        modelId: catalog.model.modelId,
        group: "Idle",
        index: 9,
        priority: "normal",
      },
      env,
    );

    expect(missingGroup.ok).toBe(false);
    if (!missingGroup.ok) expect(missingGroup.code).toBe("group_not_found");
    expect(missingMotion.ok).toBe(false);
    if (!missingMotion.ok) expect(missingMotion.code).toBe("motion_not_found");
    expect(sprite.startMotion).not.toHaveBeenCalled();
  });

  it("reports sprite readiness errors", async () => {
    const { env } = makeEnv({ getSpriteReady: () => false, getSprite: () => null });
    const response = await executeLive2DDebugCommand(
      {
        kind: "playMotion",
        requestId: "r6",
        modelId: catalog.model.modelId,
        group: "Idle",
        index: 4,
        priority: "normal",
      },
      env,
    );

    expect(response.ok).toBe(false);
    if (!response.ok) expect(response.code).toBe("sprite_not_ready");
  });

  it("uses default idle config for playIdle", async () => {
    const { env, sprite } = makeEnv();
    const response = await executeLive2DDebugCommand(
      { kind: "playIdle", requestId: "r7", modelId: catalog.model.modelId },
      env,
    );

    expect(response.ok).toBe(true);
    expect(sprite.startMotion).toHaveBeenCalledWith({
      group: "IdleLoop",
      no: 0,
      priority: 3,
    });
  });

  it("selects a random idle motion from the preferred group", async () => {
    const { env, sprite } = makeEnv({ random: () => 0.9 });
    const response = await executeLive2DDebugCommand(
      { kind: "playRandomIdle", requestId: "r8", modelId: catalog.model.modelId },
      env,
    );

    expect(response.ok).toBe(true);
    expect(sprite.startMotion).toHaveBeenCalledWith({
      group: "IdleLoop",
      no: 1,
      priority: 3,
    });
  });

  it("routes tap shortcut actions and reports phase skips", async () => {
    const actions = {
      triggerTapFeedback: vi.fn(() => "skipped_by_phase" as const),
      triggerTapParamsOnly: vi.fn(() => "played" as const),
    };
    const { env } = makeEnv({ actions, getPhase: () => "speaking" });

    const feedback = await executeLive2DDebugCommand(
      { kind: "triggerTapFeedback", requestId: "r9", modelId: catalog.model.modelId },
      env,
    );
    const paramsOnly = await executeLive2DDebugCommand(
      { kind: "triggerTapParamsOnly", requestId: "r10", modelId: catalog.model.modelId },
      env,
    );

    expect(feedback.ok).toBe(false);
    if (!feedback.ok) expect(feedback.code).toBe("skipped_by_phase");
    expect(paramsOnly.ok).toBe(true);
    expect(actions.triggerTapFeedback).toHaveBeenCalledTimes(1);
    expect(actions.triggerTapParamsOnly).toHaveBeenCalledTimes(1);
  });
});
