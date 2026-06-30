import { describe, expect, it } from "vitest";

import {
  ARK_AMIYA_SPINE_FIXTURE_MODEL,
  DEFAULT_AVATAR_MODEL_ID,
  HIYORI_LIVE2D_MODEL,
  findAvatarModelEntry,
  getDefaultLive2DConfig,
  resolveAvatarModelSelection,
} from "./catalog";

describe("avatar model catalog", () => {
  it("keeps Hiyori as the stable built-in default", () => {
    expect(DEFAULT_AVATAR_MODEL_ID).toBe("builtin:live2d:hiyori");
    expect(HIYORI_LIVE2D_MODEL).toMatchObject({
      id: "builtin:live2d:hiyori",
      backend: "live2d",
      source: "builtin",
      distribution: "redistributable",
      enabled: true,
    });
    expect(HIYORI_LIVE2D_MODEL.resources.model3).toBe(
      "/live2d-models/hiyori/Hiyori.model3.json",
    );
  });

  it("marks Ark Amiya as a non-commercial exploration fixture", () => {
    expect(ARK_AMIYA_SPINE_FIXTURE_MODEL).toMatchObject({
      id: "dev-fixture:spine:ark-amiya",
      backend: "spine",
      source: "dev-fixture",
      distribution: "non-commercial-exploration",
    });
    expect(ARK_AMIYA_SPINE_FIXTURE_MODEL.spine.runtime).toBe("spine38-pixi7");
    expect(ARK_AMIYA_SPINE_FIXTURE_MODEL.spine.actionMapping?.tap).toEqual({
      animation: "Interact",
      loop: false,
      returnToPhase: true,
    });
  });

  it("finds entries without making dev fixtures product-selectable by default", () => {
    expect(findAvatarModelEntry("dev-fixture:spine:ark-amiya")).toBe(
      ARK_AMIYA_SPINE_FIXTURE_MODEL,
    );

    expect(resolveAvatarModelSelection("dev-fixture:spine:ark-amiya")).toBe(
      HIYORI_LIVE2D_MODEL,
    );
    expect(
      resolveAvatarModelSelection("dev-fixture:spine:ark-amiya", {
        includeDevFixtures: true,
      }),
    ).toBe(ARK_AMIYA_SPINE_FIXTURE_MODEL);
  });

  it("falls back to Hiyori when the selected id or backend is unavailable", () => {
    expect(resolveAvatarModelSelection("missing")).toBe(HIYORI_LIVE2D_MODEL);
    expect(
      resolveAvatarModelSelection("dev-fixture:spine:ark-amiya", {
        allowedBackends: ["live2d"],
        includeDevFixtures: true,
      }),
    ).toBe(HIYORI_LIVE2D_MODEL);
  });

  it("derives the current Live2D config from the catalog entry", () => {
    expect(getDefaultLive2DConfig()).toEqual({
      modelName: "hiyori",
      modelPath: "/live2d-models/hiyori/Hiyori.model3.json",
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
    });
  });
});
