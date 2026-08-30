import { describe, expect, it } from "vitest";

import {
  ARK_AMIYA_SPINE_FIXTURE_MODEL,
  ARK_MODELS_NOTICE_PATH,
  BUILTIN_ARK_SPINE_MODEL_SLUGS,
  BUILTIN_ARK_SPINE_MODELS,
  DEFAULT_AVATAR_MODEL_ID,
  DEFAULT_SPINE38_PET_FIT,
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
    expect(ARK_AMIYA_SPINE_FIXTURE_MODEL.spine.defaultAnimation).toBe("Relax");
    expect(ARK_AMIYA_SPINE_FIXTURE_MODEL.spine.actionMapping?.idle).toEqual({
      animation: "Relax",
      loop: true,
    });
    expect(ARK_AMIYA_SPINE_FIXTURE_MODEL.spine.actionMapping?.tap).toEqual({
      animation: "Interact",
      loop: false,
      returnToPhase: true,
    });
  });

  it("registers the requested built-in Ark-Models Spine entries", () => {
    expect(BUILTIN_ARK_SPINE_MODEL_SLUGS).toEqual([
      "002_amiya",
      "4202_haruka",
      "140_whitew",
      "103_angel",
      "358_lisa",
      "102_texas",
      "263_skadi",
      "003_kalts",
      "1035_wisdel",
      "4133_logos",
      "2027_wang",
      "2025_shu",
      "4179_monstr",
      "010_chen",
    ]);
    expect(BUILTIN_ARK_SPINE_MODELS).toHaveLength(14);

    for (const slug of BUILTIN_ARK_SPINE_MODEL_SLUGS) {
      const entry = findAvatarModelEntry(`builtin:spine:ark-models:${slug}`);
      expect(entry).toMatchObject({
        id: `builtin:spine:ark-models:${slug}`,
        backend: "spine",
        source: "builtin",
        distribution: "non-commercial-exploration",
        enabled: true,
        noticePath: ARK_MODELS_NOTICE_PATH,
      });
      expect(entry?.backend).toBe("spine");
      if (entry?.backend !== "spine") continue;
      const basePath = `/spine-models/ark-models/${slug}/build_char_${slug}`;
      expect(entry.resources).toEqual({
        skeleton: `${basePath}.skel`,
        atlas: `${basePath}.atlas`,
        textures: [`${basePath}.png`],
      });
      expect(entry.spine.runtime).toBe("spine38-pixi7");
      expect(entry.spine.format).toBe("skel");
      expect(entry.spine.defaultAnimation).toBe("Relax");
      expect(entry.spine.actionMapping?.idle).toEqual({
        animation: "Relax",
        loop: true,
      });
      expect(entry.spine.fit).toBe(DEFAULT_SPINE38_PET_FIT);
      expect(entry.spine.fit).toMatchObject({
        readerScale: 1,
        fitWidthPx: 320,
        fitHeightPx: 320,
        fitWidthRatio: 1.35,
        fitHeightRatio: 0.3,
        slotYRatio: 0.86,
        topPaddingRatio: 0.16,
      });
      expect(entry.capabilities).toMatchObject({
        motions: true,
        alphaHitTest: true,
        mouthParams: false,
        gazeParams: false,
        expressionParams: false,
      });
    }
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
    expect(
      resolveAvatarModelSelection("builtin:spine:ark-models:002_amiya", {
        allowedBackends: ["live2d"],
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
