import { useEffect, useState } from "react";
import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";
import {
  AVATAR_MODEL_CATALOG,
  DEFAULT_AVATAR_MODEL_ID,
  HIYORI_LIVE2D_MODEL,
  type AvatarModelBackend,
  type AvatarModelCatalogEntry,
} from "./catalog";

interface InstalledLive2DResourcePaths {
  model3Path: string;
}

interface InstalledSpineResourcePaths {
  skeletonPath: string;
  atlasPath: string;
  texturePaths?: string[];
}

export interface InstalledAvatarModelResources {
  id: string;
  backend: AvatarModelBackend;
  resources: InstalledLive2DResourcePaths | InstalledSpineResourcePaths;
  available: boolean;
  error?: string | null;
}

interface InstalledCatalogState {
  entries: readonly AvatarModelCatalogEntry[];
  loading: boolean;
  error: string | null;
}

const EMPTY_CATALOG_RETRY_DELAYS_MS = [100, 250, 500] as const;

type InstalledCatalogLoader = () => Promise<AvatarModelCatalogEntry[]>;
type Delay = (milliseconds: number) => Promise<void>;

const delay: Delay = (milliseconds) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

function isLive2DResources(value: unknown): value is InstalledLive2DResourcePaths {
  const candidate = value as InstalledLive2DResourcePaths;
  return typeof candidate?.model3Path === "string";
}

function isSpineResources(value: unknown): value is InstalledSpineResourcePaths {
  const candidate = value as InstalledSpineResourcePaths;
  return (
    typeof candidate?.skeletonPath === "string" &&
    typeof candidate.atlasPath === "string" &&
    (candidate.texturePaths === undefined ||
      (Array.isArray(candidate.texturePaths) &&
        candidate.texturePaths.every((item) => typeof item === "string")))
  );
}

export function mergeInstalledAvatarModelResources(
  installed: readonly InstalledAvatarModelResources[],
  convertPath: (path: string) => string = convertFileSrc,
  staticCatalog: readonly AvatarModelCatalogEntry[] = AVATAR_MODEL_CATALOG,
): AvatarModelCatalogEntry[] {
  const byId = new Map(installed.filter((item) => item.available).map((item) => [item.id, item]));
  const entries: AvatarModelCatalogEntry[] = [];

  for (const entry of staticCatalog) {
    if (entry.source === "dev-fixture") continue;
    const resource = byId.get(entry.id);
    if (!resource || resource.backend !== entry.backend) continue;

    if (entry.backend === "live2d") {
      if (!isLive2DResources(resource.resources)) continue;
      entries.push({
        ...entry,
        resources: {
          model3: convertPath(resource.resources.model3Path),
          model3FilePath: resource.resources.model3Path,
        },
      });
      continue;
    }

    if (isSpineResources(resource.resources)) {
      entries.push({
        ...entry,
        resources: {
          skeleton: convertPath(resource.resources.skeletonPath),
          atlas: convertPath(resource.resources.atlasPath),
          textures: resource.resources.texturePaths?.map((path) => convertPath(path)),
        },
      });
    }
  }

  return entries;
}

export async function loadInstalledAvatarModelCatalog(): Promise<AvatarModelCatalogEntry[]> {
  if (!isTauri()) {
    return AVATAR_MODEL_CATALOG.filter((entry) => entry.source !== "dev-fixture");
  }
  const installed = await invoke<InstalledAvatarModelResources[]>(
    "get_installed_avatar_model_resources",
  );
  return mergeInstalledAvatarModelResources(installed);
}

export async function loadInstalledAvatarModelCatalogWithRetry(
  loader: InstalledCatalogLoader = loadInstalledAvatarModelCatalog,
  wait: Delay = delay,
  retryDelays: readonly number[] = EMPTY_CATALOG_RETRY_DELAYS_MS,
): Promise<AvatarModelCatalogEntry[]> {
  let entries = await loader();
  for (const retryDelay of retryDelays) {
    if (entries.length > 0) return entries;
    await wait(retryDelay);
    entries = await loader();
  }
  return entries;
}

export function resolveInstalledAvatarModelSelection(
  entries: readonly AvatarModelCatalogEntry[],
  id: string,
): AvatarModelCatalogEntry | null {
  const requested = entries.find((entry) => entry.enabled && entry.id === id);
  if (requested) return requested;

  return entries.find((entry) => entry.id === DEFAULT_AVATAR_MODEL_ID) ?? null;
}

export function useInstalledAvatarModelCatalog(): InstalledCatalogState {
  const [state, setState] = useState<InstalledCatalogState>(() => ({
    entries: isTauri()
      ? []
      : AVATAR_MODEL_CATALOG.filter((entry) => entry.source !== "dev-fixture"),
    loading: isTauri(),
    error: null,
  }));

  useEffect(() => {
    let cancelled = false;
    void loadInstalledAvatarModelCatalogWithRetry()
      .then((entries) => {
        if (cancelled) return;
        if (entries.length === 0) {
          throw new Error("桌宠模型资源安装完成后仍未找到可用模型");
        }
        console.info(`[avatar-models] installed catalog ready; entries=${entries.length}`);
        setState({ entries, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : String(error);
        setState({
          entries: isTauri() ? [] : [HIYORI_LIVE2D_MODEL],
          loading: false,
          error: message,
        });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
