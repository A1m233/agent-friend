import { describe, expect, it, vi } from "vitest";

import {
  DEFAULT_AVATAR_MODEL_ID,
  DEFAULT_SPINE38_CAPABILITIES,
  DEFAULT_SPINE38_PET_FIT,
  HIYORI_LIVE2D_MODEL,
  type AvatarModelCatalogEntry,
} from "./catalog";
import {
  mergeInstalledAvatarModelResources,
  loadInstalledAvatarModelCatalogWithRetry,
  resolveInstalledAvatarModelSelection,
  type InstalledAvatarModelResources,
} from "./installed";

const toAssetUrl = (path: string, protocol = "asset") => `${protocol}://${path}`;

describe("installed avatar model catalog", () => {
  it("merges installed Live2D resources into static metadata", () => {
    const entries = mergeInstalledAvatarModelResources(
      [
        {
          id: DEFAULT_AVATAR_MODEL_ID,
          backend: "live2d",
          available: true,
          resources: {
            model3Path: "C:/data/avatar-models/builtin/live2d/hiyori/Hiyori.model3.json",
          },
        },
      ],
      toAssetUrl,
    );

    const hiyori = entries.find((entry) => entry.id === DEFAULT_AVATAR_MODEL_ID);
    expect(hiyori).toMatchObject({
      id: DEFAULT_AVATAR_MODEL_ID,
      backend: "live2d",
      name: HIYORI_LIVE2D_MODEL.name,
    });
    expect(hiyori?.backend).toBe("live2d");
    if (hiyori?.backend !== "live2d") return;
    expect(hiyori.resources.model3).toBe(
      "asset://C:/data/avatar-models/builtin/live2d/hiyori/Hiyori.model3.json",
    );
    expect(hiyori.resources.model3FilePath).toBe(
      "C:/data/avatar-models/builtin/live2d/hiyori/Hiyori.model3.json",
    );
    expect(hiyori.live2d).toEqual(HIYORI_LIVE2D_MODEL.live2d);
  });

  it("merges installed Spine resources and filters unavailable entries", () => {
    const installed: InstalledAvatarModelResources[] = [
      {
        id: "builtin:spine:ark-models:002_amiya",
        backend: "spine",
        available: true,
        resources: {
          skeletonPath: "/data/spine/002_amiya/build_char_002_amiya.skel",
          atlasPath: "/data/spine/002_amiya/build_char_002_amiya.atlas",
          texturePaths: ["/data/spine/002_amiya/build_char_002_amiya.png"],
        },
      },
      {
        id: "builtin:spine:ark-models:010_chen",
        backend: "spine",
        available: false,
        resources: {
          skeletonPath: "/missing.skel",
          atlasPath: "/missing.atlas",
          texturePaths: ["/missing.png"],
        },
      },
    ];

    const entries = mergeInstalledAvatarModelResources(installed, toAssetUrl);
    const amiya = entries.find((entry) => entry.id === "builtin:spine:ark-models:002_amiya");
    expect(amiya?.backend).toBe("spine");
    if (amiya?.backend !== "spine") return;
    expect(amiya.resources).toEqual({
      skeleton: "asset:///data/spine/002_amiya/build_char_002_amiya.skel",
      atlas: "asset:///data/spine/002_amiya/build_char_002_amiya.atlas",
      textures: ["asset:///data/spine/002_amiya/build_char_002_amiya.png"],
    });
    expect(entries.some((entry) => entry.id === "builtin:spine:ark-models:010_chen")).toBe(
      false,
    );
  });

  it("ignores backend mismatches and dev fixtures", () => {
    const entries = mergeInstalledAvatarModelResources(
      [
        {
          id: DEFAULT_AVATAR_MODEL_ID,
          backend: "spine",
          available: true,
          resources: {
            skeletonPath: "/wrong.skel",
            atlasPath: "/wrong.atlas",
          },
        },
        {
          id: "dev-fixture:spine:ark-amiya",
          backend: "spine",
          available: true,
          resources: {
            skeletonPath: "/dev.skel",
            atlasPath: "/dev.atlas",
          },
        },
      ],
      toAssetUrl,
    );

    expect(entries).toEqual([]);
  });

  it("creates catalog entries only for runtime-validated user models", () => {
    const entries = mergeInstalledAvatarModelResources(
      [
        {
          id: "user:spine38:abc",
          name: "我的模型",
          backend: "spine",
          available: true,
          contentRevision: "revision-1",
          defaultAnimation: "Relax",
          resources: {
            skeletonPath: "C:/models/a/model.skel",
            atlasPath: "C:/models/a/model.atlas",
            texturePaths: ["C:/models/a/model.png"],
          },
        },
        {
          id: "user:spine38:not-validated",
          name: "未校验",
          backend: "spine",
          available: true,
          resources: {
            skeletonPath: "C:/models/b/model.skel",
            atlasPath: "C:/models/b/model.atlas",
          },
        },
      ],
      toAssetUrl,
    );

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      id: "user:spine38:abc",
      name: "我的模型",
      source: "user",
      distribution: "user-local-only",
      contentRevision: "revision-1",
      spine: {
        defaultAnimation: "Relax",
        runtime: "spine38-pixi7",
        fit: DEFAULT_SPINE38_PET_FIT,
      },
    });
    expect(entries[0]?.backend).toBe("spine");
    if (entries[0]?.backend !== "spine") return;
    expect(entries[0].spine.actionMapping).toBeUndefined();
    expect(entries[0].capabilities).toBe(DEFAULT_SPINE38_CAPABILITIES);
    expect(entries[0]?.resources).toEqual({
      skeleton: "asset://C:/models/a/model.skel",
      atlas: "asset://C:/models/a/model.atlas",
      textures: ["asset://C:/models/a/model.png"],
    });
  });

  it("resolves selected ids with Hiyori fallback from installed entries", () => {
    const entries = [
      {
        ...HIYORI_LIVE2D_MODEL,
        resources: { model3: "asset://hiyori.model3.json" },
      },
      {
        ...HIYORI_LIVE2D_MODEL,
        id: "custom-disabled",
        enabled: false,
        resources: { model3: "asset://disabled.model3.json" },
      },
    ] satisfies AvatarModelCatalogEntry[];

    expect(resolveInstalledAvatarModelSelection(entries, "missing")?.id).toBe(
      DEFAULT_AVATAR_MODEL_ID,
    );
    expect(resolveInstalledAvatarModelSelection(entries, "custom-disabled")?.id).toBe(
      DEFAULT_AVATAR_MODEL_ID,
    );
    expect(resolveInstalledAvatarModelSelection([], "missing")).toBeNull();
  });

  it("retries a transient empty catalog during first-launch asset installation", async () => {
    const hiyori = {
      ...HIYORI_LIVE2D_MODEL,
      resources: { model3: "asset://hiyori.model3.json" },
    } satisfies AvatarModelCatalogEntry;
    const loader = vi
      .fn<() => Promise<AvatarModelCatalogEntry[]>>()
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([hiyori]);
    const wait = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);

    await expect(
      loadInstalledAvatarModelCatalogWithRetry(loader, wait, [100, 250]),
    ).resolves.toEqual([hiyori]);
    expect(loader).toHaveBeenCalledTimes(2);
    expect(wait).toHaveBeenCalledOnce();
  });

  it("returns an empty catalog only after exhausting first-launch retries", async () => {
    const loader = vi.fn<() => Promise<AvatarModelCatalogEntry[]>>().mockResolvedValue([]);
    const wait = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);

    await expect(
      loadInstalledAvatarModelCatalogWithRetry(loader, wait, [100, 250]),
    ).resolves.toEqual([]);
    expect(loader).toHaveBeenCalledTimes(3);
    expect(wait).toHaveBeenCalledTimes(2);
  });
});
