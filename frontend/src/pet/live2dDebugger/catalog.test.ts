import { describe, expect, it, vi, afterEach } from "vitest";
import type { PetLive2DConfig } from "@/pet/live2dConfig";

import {
  buildMotionCatalogFromModel3,
  findMotion,
  firstAvailableMotion,
  loadCurrentMotionCatalog,
} from "./catalog";

const config: PetLive2DConfig = {
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
};

const model3 = {
  FileReferences: {
    Motions: {
      IdleLoop: [
        { File: "motions/Hiyori_m01.motion3.json" },
        { File: "motions/Hiyori_m02.motion3.json" },
      ],
      Idle: [
        { File: "motions/Hiyori_m01.motion3.json" },
        { File: "motions/Hiyori_m06.motion3.json" },
      ],
      TapBody: [{ File: "motions/Hiyori_m04.motion3.json" }],
    },
  },
};

describe("buildMotionCatalogFromModel3", () => {
  it("projects model3 motions into a model-scoped catalog", () => {
    const catalog = buildMotionCatalogFromModel3(config, model3);
    expect(catalog.model).toEqual({
      modelId: "hiyori:/live2d-models/hiyori/Hiyori.model3.json",
      modelName: "hiyori",
      modelPath: "/live2d-models/hiyori/Hiyori.model3.json",
    });
    expect(catalog.groups.map((group) => group.name)).toEqual(["IdleLoop", "Idle", "TapBody"]);
    expect(catalog.groups[1].motions[1]).toEqual({
      group: "Idle",
      index: 1,
      file: "motions/Hiyori_m06.motion3.json",
    });
    expect(catalog.defaults).toEqual({
      idleGroup: "IdleLoop",
      idleIndex: 0,
      tapGroup: "Idle",
      tapIndex: 4,
    });
  });

  it("ignores malformed motion entries without hard-coding Hiyori groups", () => {
    const catalog = buildMotionCatalogFromModel3(config, {
      FileReferences: {
        Motions: {
          Custom: [{ File: "a.motion3.json" }, { File: "" }, {}],
        },
      },
    });
    expect(catalog.groups).toEqual([
      {
        name: "Custom",
        motions: [{ group: "Custom", index: 0, file: "a.motion3.json" }],
      },
    ]);
  });
});

describe("catalog helpers", () => {
  const catalog = buildMotionCatalogFromModel3(config, model3);

  it("finds motions by group and index", () => {
    expect(findMotion(catalog, "TapBody", 0)?.file).toBe("motions/Hiyori_m04.motion3.json");
    expect(findMotion(catalog, "Missing", 0)).toBeNull();
    expect(findMotion(catalog, "TapBody", 9)).toBeNull();
  });

  it("falls back to the first available group", () => {
    expect(firstAvailableMotion(catalog, "Idle")?.group).toBe("Idle");
    expect(firstAvailableMotion(catalog, "Missing")?.group).toBe("IdleLoop");
  });
});

describe("loadCurrentMotionCatalog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads model3 JSON from the configured model path", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue(model3),
    });
    vi.stubGlobal("fetch", fetch);

    const catalog = await loadCurrentMotionCatalog(config);

    expect(fetch).toHaveBeenCalledWith("/live2d-models/hiyori/Hiyori.model3.json");
    expect(catalog.groups).toHaveLength(3);
  });
});
