import * as PIXI7 from "pixi7";
import { BaseTexture, Texture } from "@pixi/core";
import { Spine as Spine38, TextureAtlas } from "pixi-spine";
import { AtlasAttachmentLoader, SkeletonBinary } from "@pixi-spine/runtime-3.8";
import type { SpineAvatarModelEntry } from "@/avatar-models";

interface Spine38Resource {
  spineData: ConstructorParameters<typeof Spine38>[0];
  spineAtlas: TextureAtlas;
}

interface Spine38TextureQualityTarget {
  baseTexture: object;
}

const spine38ResourceCache = new Map<string, Promise<Spine38Resource>>();

export function getSpine38AssetAlias(entry: SpineAvatarModelEntry): string {
  return `spine38:v6:${entry.id}@${entry.contentRevision ?? "builtin"}`;
}

export function applySpine38TextureQuality(texture: Spine38TextureQualityTarget): void {
  const baseTexture = texture.baseTexture as BaseTexture;
  baseTexture.setStyle(
    PIXI7.SCALE_MODES.LINEAR as Parameters<BaseTexture["setStyle"]>[0],
    PIXI7.MIPMAP_MODES.ON as Parameters<BaseTexture["setStyle"]>[1],
  );
}

function readAtlasTexturePages(atlasText: string): string[] {
  const pages: string[] = [];
  let expectsPageTexture = true;
  for (const line of atlasText.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (trimmed === "") {
      expectsPageTexture = true;
      continue;
    }
    if (expectsPageTexture) {
      pages.push(trimmed);
      expectsPageTexture = false;
      continue;
    }
    expectsPageTexture = false;
  }
  return pages;
}

async function loadSpine38TextureAtlas(entry: SpineAvatarModelEntry) {
  if (!entry.resources.textures?.length) return undefined;

  const response = await fetch(entry.resources.atlas);
  if (!response.ok) {
    throw new Error(`Failed to load Spine atlas: ${response.status}`);
  }
  const atlasText = await response.text();
  const pages = readAtlasTexturePages(atlasText);
  const textures: Record<string, Texture> = {};
  await Promise.all(
    pages.map(async (page: string, index: number) => {
      const texturePath = entry.resources.textures?.[index];
      if (!texturePath) return;
      const textureResponse = await fetch(texturePath);
      if (!textureResponse.ok) {
        throw new Error(`Failed to load Spine texture: ${textureResponse.status}`);
      }
      const bitmap = await createImageBitmap(await textureResponse.blob());
      const texture = new Texture(BaseTexture.from(bitmap));
      applySpine38TextureQuality(texture);
      textures[page] = texture;
    }),
  );

  return await new Promise<TextureAtlas>((resolve, reject) => {
    let atlas: TextureAtlas | null = null;
    atlas = new TextureAtlas(
      atlasText,
      (page: string, callback: (baseTexture: unknown) => void) => {
        const texture = textures[page] ?? Object.values(textures)[0];
        if (!texture) {
          reject(new Error(`Missing Spine atlas texture: ${page}`));
          return;
        }
        callback(texture.baseTexture);
      },
      (loadedAtlas?: TextureAtlas) => {
        const resolvedAtlas = loadedAtlas ?? atlas;
        if (!resolvedAtlas) {
          reject(new Error("Failed to construct Spine texture atlas"));
          return;
        }
        resolve(resolvedAtlas);
      },
    );
  });
}

async function loadSpine38Resource(entry: SpineAvatarModelEntry): Promise<Spine38Resource> {
  const alias = getSpine38AssetAlias(entry);
  let resourcePromise = spine38ResourceCache.get(alias);
  if (!resourcePromise) {
    const loadPromise = (async () => {
      const spineAtlas = await loadSpine38TextureAtlas(entry);
      if (!spineAtlas) {
        throw new Error(`Missing Spine atlas textures: ${entry.id}`);
      }
      const response = await fetch(entry.resources.skeleton);
      if (!response.ok) {
        throw new Error(`Failed to load Spine skeleton: ${response.status}`);
      }
      const skeleton = new Uint8Array(await response.arrayBuffer());
      const parser = new SkeletonBinary(new AtlasAttachmentLoader(spineAtlas));
      parser.scale = entry.spine.fit?.readerScale ?? 1;
      return {
        spineData: parser.readSkeletonData(skeleton),
        spineAtlas,
      };
    })();
    spine38ResourceCache.set(alias, loadPromise);
    void loadPromise.catch(() => {
      if (spine38ResourceCache.get(alias) === loadPromise) {
        spine38ResourceCache.delete(alias);
      }
    });
    resourcePromise = loadPromise;
  }
  return resourcePromise;
}

export async function loadSpine38Model(entry: SpineAvatarModelEntry): Promise<Spine38> {
  const resource = await loadSpine38Resource(entry);
  return new Spine38(resource.spineData);
}
