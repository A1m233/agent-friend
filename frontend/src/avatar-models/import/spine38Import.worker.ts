/// <reference lib="webworker" />

import { BaseTexture } from "@pixi/core";
import { TextureAtlas } from "pixi-spine";
import { AtlasAttachmentLoader, SkeletonBinary } from "@pixi-spine/runtime-3.8";

interface ValidationRequest {
  requestId: string;
  skeletonUrl: string;
  atlasUrl: string;
  textureUrls: string[];
}

interface ValidationResponse {
  requestId: string;
  success: boolean;
  defaultAnimation?: string;
  error?: string;
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
    }
  }
  return pages;
}

interface ValidationAtlas {
  atlas: TextureAtlas;
  baseTextures: BaseTexture[];
}

function createTextureAtlas(atlasText: string, bitmaps: ImageBitmap[]): Promise<ValidationAtlas> {
  const pages = readAtlasTexturePages(atlasText);
  if (pages.length === 0) throw new Error("atlas 中没有贴图页");
  if (pages.length !== bitmaps.length) {
    throw new Error(`atlas 贴图页数量不匹配: expected ${pages.length}, received ${bitmaps.length}`);
  }
  const textures = new Map(
    pages.map((page, index) => [page, BaseTexture.from(bitmaps[index])]),
  );
  return new Promise<ValidationAtlas>((resolve, reject) => {
    let atlas: TextureAtlas | null = null;
    atlas = new TextureAtlas(
      atlasText,
      (page, callback) => {
        const texture = textures.get(page);
        if (!texture) {
          reject(new Error(`atlas 引用的贴图不存在: ${page}`));
          return;
        }
        callback(texture);
      },
      (loadedAtlas) => {
        const resolved = loadedAtlas ?? atlas;
        if (!resolved) {
          reject(new Error("无法构造 Spine texture atlas"));
          return;
        }
        resolve({ atlas: resolved, baseTextures: [...textures.values()] });
      },
    );
  });
}

function chooseDefaultAnimation(names: string[]): string {
  for (const preferred of ["Relax", "Idle", "idle", "Stand", "stand"]) {
    if (names.includes(preferred)) return preferred;
  }
  const first = names[0];
  if (!first) throw new Error("骨骼数据中没有可播放动画");
  return first;
}

self.onmessage = async (event: MessageEvent<ValidationRequest>) => {
  const request = event.data;
  const response: ValidationResponse = { requestId: request.requestId, success: false };
  let validationAtlas: ValidationAtlas | undefined;
  let bitmaps: ImageBitmap[] = [];
  try {
    const [skeletonResponse, atlasResponse, ...textureResponses] = await Promise.all([
      fetch(request.skeletonUrl),
      fetch(request.atlasUrl),
      ...request.textureUrls.map((url) => fetch(url)),
    ]);
    const failed = [skeletonResponse, atlasResponse, ...textureResponses].find(
      (item) => !item.ok,
    );
    if (failed) throw new Error(`读取模型资源失败: HTTP ${failed.status}`);
    const [skeletonBytes, atlasText, ...textureBlobs] = await Promise.all([
      skeletonResponse.arrayBuffer(),
      atlasResponse.text(),
      ...textureResponses.map((item) => item.blob()),
    ]);
    bitmaps = await Promise.all(textureBlobs.map((blob) => createImageBitmap(blob)));
    validationAtlas = await createTextureAtlas(atlasText, bitmaps);
    const binary = new SkeletonBinary(new AtlasAttachmentLoader(validationAtlas.atlas));
    const skeletonData = binary.readSkeletonData(new Uint8Array(skeletonBytes)) as {
      animations: Array<{ name: string }>;
    };
    response.success = true;
    response.defaultAnimation = chooseDefaultAnimation(
      skeletonData.animations.map((animation) => animation.name),
    );
  } catch (error) {
    response.error = error instanceof Error ? error.message : String(error);
  } finally {
    validationAtlas?.baseTextures.forEach((texture) => texture.destroy());
    bitmaps.forEach((bitmap) => bitmap.close());
  }
  self.postMessage(response);
};

export {};
