import * as PIXI7 from "pixi7";
import { Spine as Spine38 } from "pixi-spine";
import type { SpineAvatarModelEntry } from "@/avatar-models";

interface Spine38Resource {
  spineData: ConstructorParameters<typeof Spine38>[0];
}

export function getSpine38AssetAlias(entry: SpineAvatarModelEntry): string {
  return `spine38:${entry.id}`;
}

export async function loadSpine38Model(
  entry: SpineAvatarModelEntry,
): Promise<Spine38> {
  const alias = getSpine38AssetAlias(entry);
  if (!PIXI7.Assets.resolver.hasKey(alias)) {
    PIXI7.Assets.add({
      alias,
      src: entry.resources.skeleton,
      data: {
        spineAtlasFile: entry.resources.atlas,
        spineSkeletonScale: entry.spine.fit?.readerScale ?? 1,
      },
    });
  }
  const resource = await PIXI7.Assets.load<Spine38Resource>(alias);
  return new Spine38(resource.spineData);
}
